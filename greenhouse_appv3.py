# greenhouse_appv3.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, font as tkFont
import asyncio
import threading
import queue
import logging # Import logging first
import uuid
from datetime import datetime, time, timedelta
import re # Pour la validation de l'heure
import copy # Pour la copie profonde des conditions

# Importer les modules personnalisés
# Assurez-vous que ces fichiers existent et sont corrects
try:
    # logger_setup.py (pour la configuration du logging)
    from logger_setup import setup_logging
    # discover_device.py (pour la découverte des appareils Kasa)
    from discover_device import DeviceDiscoverer
    # device_control.py (pour le contrôle des appareils Kasa)
    from device_control import DeviceController
    # temp_sensor_wrapper.py (pour les capteurs de température)
    from temp_sensor_wrapper import TempSensorManager
    # light_sensor.py (pour les capteurs de lumière BH1750)
    from light_sensor import BH1750Manager
    # config_manager.py (pour charger/sauvegarder la configuration)
    from config_manager import load_config, save_config
except ImportError as e:
    # Log critique si un module manque
    # Use basicConfig for fallback logging if setup_logging fails or isn't called yet
    logging.basicConfig(level=logging.CRITICAL)
    logging.critical(f"Erreur d'importation d'un module requis: {e}. Assurez-vous que tous les fichiers .py sont présents.")
    # Essayer d'afficher une erreur à l'utilisateur via Tkinter si possible
    try:
        root_err = tk.Tk()
        root_err.withdraw() # Cacher la fenêtre principale vide
        messagebox.showerror("Erreur d'Importation", f"Impossible de charger un module nécessaire: {e}\nVérifiez que tous les fichiers .py sont dans le même répertoire.")
        root_err.destroy()
    except tk.TclError:
        # Si Tkinter lui-même échoue, on ne peut rien afficher graphiquement
        pass
    exit() # Arrêter l'application car elle ne peut pas fonctionner

# --- Constantes ---
OPERATORS = ['<', '>', '=', '!=', '<=', '>='] # Opérateurs génériques
TIME_OPERATORS = ['<', '>', '=', '!=', '<=', '>='] # Opérateurs pour les conditions temporelles
SENSOR_OPERATORS = ['<', '>', '=', '!=', '<=', '>='] # Opérateurs pour les conditions de capteurs
ACTIONS = ['ON', 'OFF'] # Actions possibles sur les prises
LOGIC_OPERATORS = ['ET', 'OU'] # Opérateurs logiques entre conditions ('AND', 'OR')
CONDITION_TYPES = ['Capteur', 'Heure'] # Types de conditions possibles
DEFAULT_CONFIG_FILE = 'config.yaml' # Nom du fichier de configuration
TIME_REGEX = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$') # Expression régulière pour valider le format HH:MM

#--------------------------------------------------------------------------
# CLASSE POUR L'ÉDITEUR DE CONDITIONS (POP-UP) - VERSION AMÉLIORÉE
#--------------------------------------------------------------------------
class ConditionEditor(simpledialog.Dialog):
    """Fenêtre modale pour éditer une liste de conditions (SI ou JUSQUÀ)."""

    def __init__(self, parent, title, rule_id, condition_type,
                 initial_logic, initial_conditions, available_sensors, app_instance):
        """
        Initialise l'éditeur de conditions.

        Args:
            parent: La fenêtre parente (la fenêtre principale de l'application).
            title (str): Le titre de la fenêtre pop-up.
            rule_id (str): L'identifiant unique de la règle en cours d'édition.
            condition_type (str): 'trigger' (pour SI) ou 'until' (pour JUSQU'À).
            initial_logic (str): L'opérateur logique initial ('ET' ou 'OU').
            initial_conditions (list): La liste des conditions initiales.
            available_sensors (list): Liste des capteurs disponibles [(nom, id), ...].
            app_instance (GreenhouseApp): Référence à l'instance principale de l'application.
        """
        self.rule_id = rule_id
        self.condition_type = condition_type # 'trigger' or 'until'
        self.initial_logic = initial_logic if initial_logic in LOGIC_OPERATORS else LOGIC_OPERATORS[0]
        # Faire une copie profonde pour éviter de modifier l'original directement via le pop-up
        self.initial_conditions = copy.deepcopy(initial_conditions)
        self.available_sensors = available_sensors # [(name, id), ...]
        self.app = app_instance # Référence à GreenhouseApp pour utiliser get_alias

        # Liste de dict: {'frame': ttk.Frame, 'widgets': dict, 'condition_id': str, 'logic_label': ttk.Label or None}
        self.condition_lines = []
        self.result_logic = None # Stocke la logique validée ('ET'/'OU')
        self.result_conditions = None # Stocke la liste des conditions validées

        # Compteur pour générer des IDs uniques pour les *nouvelles* conditions dans cette session
        self.condition_id_counter = 0

        # Initialisation de la classe parente (simpledialog.Dialog)
        super().__init__(parent, title=title)

    def body(self, master):
        """Crée le contenu du corps de la boîte de dialogue."""
        dialog_frame = ttk.Frame(master, padding="10")
        dialog_frame.pack(fill=tk.BOTH, expand=True)

        # --- Section Logique Globale (ET/OU) ---
        logic_frame = ttk.Frame(dialog_frame)
        logic_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        ttk.Label(logic_frame, text="Logique entre conditions:").pack(side=tk.LEFT, padx=(0, 5))
        self.logic_var = tk.StringVar(value=self.initial_logic)
        self.logic_combo = ttk.Combobox(logic_frame, textvariable=self.logic_var, values=LOGIC_OPERATORS, state="readonly", width=5)
        self.logic_combo.pack(side=tk.LEFT)
        # Mettre à jour les labels de logique quand la sélection change
        self.logic_combo.bind('<<ComboboxSelected>>', self._update_logic_labels)


        # --- Zone Scrollable pour les Conditions ---
        conditions_container = ttk.Frame(dialog_frame)
        conditions_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Canvas pour contenir le frame scrollable
        self.conditions_canvas = tk.Canvas(conditions_container, borderwidth=0, highlightthickness=0)
        # Scrollbar verticale liée au canvas (placée à DROITE du canvas)
        scrollbar = ttk.Scrollbar(conditions_container, orient="vertical", command=self.conditions_canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y) # Pack scrollbar first to the right

        # Empaqueter le canvas pour qu'il prenne l'espace restant
        self.conditions_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True) # Pack canvas second to fill left

        # Frame interne qui contiendra les lignes de conditions
        self.scrollable_conditions_frame = ttk.Frame(self.conditions_canvas)

        # Quand le frame interne change de taille, on met à jour la scrollregion du canvas
        self.scrollable_conditions_frame.bind("<Configure>", self._on_frame_configure)

        # Placer le frame interne dans le canvas
        self.canvas_window = self.conditions_canvas.create_window((0, 0), window=self.scrollable_conditions_frame, anchor="nw")
        # Configurer le canvas pour utiliser la scrollbar
        self.conditions_canvas.configure(yscrollcommand=scrollbar.set)

        # Lier la molette de la souris au canvas pour le défilement
        self.conditions_canvas.bind("<MouseWheel>", self._on_mousewheel) # Windows
        self.conditions_canvas.bind("<Button-4>", self._on_mousewheel) # Linux scroll up
        self.conditions_canvas.bind("<Button-5>", self._on_mousewheel) # Linux scroll down

        # --- Labels d'en-tête pour les colonnes ---
        header_frame = ttk.Frame(self.scrollable_conditions_frame)
        header_frame.pack(fill=tk.X, expand=True, pady=(0, 5))
        # Configure columns for alignment (adjust weights/min sizes as needed)
        header_frame.columnconfigure(0, weight=0, minsize=30)  # Logic Label space
        header_frame.columnconfigure(1, weight=1, minsize=80)  # Type
        header_frame.columnconfigure(2, weight=2, minsize=150) # Capteur
        header_frame.columnconfigure(3, weight=0, minsize=40)  # OP
        header_frame.columnconfigure(4, weight=1, minsize=100) # Valeur
        header_frame.columnconfigure(5, weight=0) # Delete button

        # Empty label for alignment above logic labels
        ttk.Label(header_frame, text="").grid(row=0, column=0, padx=2, sticky="w")
        ttk.Label(header_frame, text="Type").grid(row=0, column=1, padx=2, sticky="w")
        ttk.Label(header_frame, text="Capteur").grid(row=0, column=2, padx=2, sticky="w")
        ttk.Label(header_frame, text="OP").grid(row=0, column=3, padx=2, sticky="w")
        # Label Valeur sur deux lignes si nécessaire
        val_label = ttk.Label(header_frame, text="Valeur\n(Num ou HH:MM)", justify=tk.LEFT)
        val_label.grid(row=0, column=4, padx=2, sticky="w")


        # --- Peupler les conditions initiales ---
        if not self.initial_conditions:
            # S'il n'y a pas de condition initiale, ajouter une ligne vide
             self._add_condition_line()
        else:
            # Sinon, ajouter une ligne pour chaque condition existante
            for condition_data in self.initial_conditions:
                self._add_condition_line(condition_data)

        # Mettre à jour les labels de logique initiaux
        self._update_logic_labels()

        # --- Bouton Ajouter Condition ---
        add_button_frame = ttk.Frame(dialog_frame)
        add_button_frame.pack(side=tk.TOP, fill=tk.X, pady=(10, 0))
        add_button = ttk.Button(add_button_frame, text="➕ Ajouter Condition", command=self._add_condition_line)
        add_button.pack()

        # Ajuster la taille initiale du pop-up et le rendre redimensionnable
        self.geometry("750x450") # Augmenté légèrement la largeur
        self.resizable(True, True)

        self._update_scrollregion() # Mise à jour initiale de la scrollregion

        return self.logic_combo # Mettre le focus initial sur le combobox de logique

    def _on_frame_configure(self, event=None):
        """Met à jour la scrollregion du canvas quand le frame interne change de taille."""
        self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))
        # Ajuster la largeur du frame interne à celle du canvas pour éviter le scroll horizontal inutile
        canvas_width = event.width if event else self.conditions_canvas.winfo_width()
        self.conditions_canvas.itemconfig(self.canvas_window, width=canvas_width)


    def _on_mousewheel(self, event):
        """Gère le défilement avec la molette de la souris."""
        delta = 0
        if event.num == 5: delta = 1   # Linux scroll down
        elif event.num == 4: delta = -1  # Linux scroll up
        elif hasattr(event, 'delta'):    # Windows
            delta = -1 if event.delta > 0 else 1
        if delta != 0:
            self.conditions_canvas.yview_scroll(delta, "units")
            return "break" # Empêcher l'événement de se propager

    def _update_scrollregion(self):
        """Force la mise à jour de la scrollregion du canvas."""
        self.scrollable_conditions_frame.update_idletasks()
        self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))
        # Ensure the frame width matches the canvas width after updates
        self.conditions_canvas.itemconfig(self.canvas_window, width=self.conditions_canvas.winfo_width())


    def _add_condition_line(self, condition_data=None):
        """Ajoute une ligne de widgets (une condition) dans le frame scrollable."""
        line_frame = ttk.Frame(self.scrollable_conditions_frame)
        line_frame.pack(fill=tk.X, expand=True, pady=1)

        # Configurer les colonnes comme dans le header pour l'alignement
        line_frame.columnconfigure(0, weight=0, minsize=30)  # Logic Label space
        line_frame.columnconfigure(1, weight=1, minsize=80)  # Type
        line_frame.columnconfigure(2, weight=2, minsize=150) # Capteur
        line_frame.columnconfigure(3, weight=0, minsize=40)  # OP
        line_frame.columnconfigure(4, weight=1, minsize=100) # Valeur
        line_frame.columnconfigure(5, weight=0) # Delete button

        widgets = {} # Dictionnaire pour stocker les widgets de cette ligne
        condition_id = condition_data.get('condition_id', f"new_{self.condition_id_counter}") if condition_data else f"new_{self.condition_id_counter}"
        self.condition_id_counter += 1

        # 0. Label Logique (ET/OU) - Ajouté seulement si ce n'est pas la première ligne
        logic_label = None
        if len(self.condition_lines) > 0:
            logic_label = ttk.Label(line_frame, text=self.logic_var.get(), width=3, anchor="e")
            logic_label.grid(row=0, column=0, padx=(0,5), sticky="e")
        else:
            # Placeholder for alignment on the first row
            ttk.Label(line_frame, text="").grid(row=0, column=0, padx=(0,5))


        # 1. Type de condition (Capteur/Heure)
        widgets['type_var'] = tk.StringVar()
        widgets['type_combo'] = ttk.Combobox(line_frame, textvariable=widgets['type_var'], values=CONDITION_TYPES, state="readonly", width=8)
        widgets['type_combo'].grid(row=0, column=1, padx=2, sticky="ew")
        widgets['type_combo'].bind('<<ComboboxSelected>>', lambda e, lw=widgets: self._on_condition_type_change(lw))

        # 2. Sélecteur de Capteur
        widgets['sensor_var'] = tk.StringVar()
        sensor_names = [""] + sorted([name for name, _id in self.available_sensors])
        widgets['sensor_combo'] = ttk.Combobox(line_frame, textvariable=widgets['sensor_var'], values=sensor_names, state="disabled", width=20)
        widgets['sensor_combo'].grid(row=0, column=2, padx=2, sticky="ew")

        # 3. Opérateur
        widgets['operator_var'] = tk.StringVar()
        widgets['operator_combo'] = ttk.Combobox(line_frame, textvariable=widgets['operator_var'], values=OPERATORS, state="readonly", width=4)
        widgets['operator_combo'].grid(row=0, column=3, padx=2, sticky="ew")

        # 4. Valeur
        widgets['value_var'] = tk.StringVar()
        widgets['value_entry'] = ttk.Entry(line_frame, textvariable=widgets['value_var'], width=10)
        widgets['value_entry'].grid(row=0, column=4, padx=2, sticky="ew")

        # 5. Bouton Supprimer (❌) - Changé ici
        delete_button = ttk.Button(line_frame, text="❌", width=3, style="Red.TButton",
                                   command=lambda frame=line_frame: self._delete_condition_line(frame))
        delete_button.grid(row=0, column=5, padx=(5, 2)) # Placé dans la dernière colonne

        # Stocker les informations de la ligne
        line_info = {'frame': line_frame, 'widgets': widgets, 'condition_id': condition_id, 'logic_label': logic_label}
        self.condition_lines.append(line_info)

        # Si des données initiales sont fournies, peupler les widgets
        if condition_data:
            cond_type = condition_data.get('type')
            widgets['type_var'].set(cond_type if cond_type in CONDITION_TYPES else '')
            widgets['operator_var'].set(condition_data.get('operator', ''))

            if cond_type == 'Capteur':
                sensor_id = condition_data.get('id')
                sensor_name = self.app.get_alias('sensor', sensor_id) if sensor_id else ''
                widgets['sensor_var'].set(sensor_name if sensor_name in sensor_names else "")
                widgets['value_var'].set(str(condition_data.get('threshold', '')))
            elif cond_type == 'Heure':
                widgets['value_var'].set(condition_data.get('value', '')) # Format HH:MM

            self._on_condition_type_change(widgets) # Mettre à jour l'état des widgets
        else:
            # Si c'est une nouvelle ligne, initialiser avec le premier type
             widgets['type_var'].set(CONDITION_TYPES[0])
             self._on_condition_type_change(widgets)

        # Mettre à jour la scrollregion après ajout
        self._update_scrollregion()
        # Mettre à jour les labels de logique (pour le cas où la première ligne est ajoutée/supprimée)
        self._update_logic_labels()

    def _update_logic_labels(self, event=None):
        """Met à jour le texte des labels de logique (ET/OU) pour toutes les lignes."""
        current_logic = self.logic_var.get()
        for i, line_info in enumerate(self.condition_lines):
            logic_label = line_info.get('logic_label')
            if logic_label: # Si le label existe (pas la première ligne)
                try:
                    if logic_label.winfo_exists():
                        # Afficher le label seulement s'il y a plus d'une condition
                        logic_label.config(text=current_logic if len(self.condition_lines) > 1 else "")
                except tk.TclError:
                    pass # Ignorer si le widget a été détruit

    def _on_condition_type_change(self, line_widgets):
        """Adapte l'UI d'une ligne quand le type de condition change."""
        selected_type = line_widgets['type_var'].get()
        current_op = line_widgets['operator_var'].get()

        try:
            sensor_combo = line_widgets['sensor_combo']
            value_entry = line_widgets['value_entry']
            operator_combo = line_widgets['operator_combo']

            if selected_type == 'Capteur':
                if sensor_combo.winfo_exists(): sensor_combo.config(state="readonly")
                if value_entry.winfo_exists(): value_entry.config(state="normal")
                if operator_combo.winfo_exists():
                    operator_combo.config(values=SENSOR_OPERATORS)
                    if current_op not in SENSOR_OPERATORS: line_widgets['operator_var'].set('')
                if ':' in line_widgets['value_var'].get(): line_widgets['value_var'].set('') # Clear time format
            elif selected_type == 'Heure':
                if sensor_combo.winfo_exists(): sensor_combo.config(state="disabled"); line_widgets['sensor_var'].set("")
                if value_entry.winfo_exists(): value_entry.config(state="normal")
                if operator_combo.winfo_exists():
                    operator_combo.config(values=TIME_OPERATORS)
                    if current_op not in TIME_OPERATORS: line_widgets['operator_var'].set('')
                try: float(line_widgets['value_var'].get()); line_widgets['value_var'].set('') # Clear numeric format
                except ValueError: pass
            else: # Should not happen with readonly combobox
                if sensor_combo.winfo_exists(): sensor_combo.config(state="disabled"); line_widgets['sensor_var'].set("")
                if value_entry.winfo_exists(): value_entry.config(state="disabled"); line_widgets['value_var'].set("")
                if operator_combo.winfo_exists(): operator_combo.config(values=OPERATORS); line_widgets['operator_var'].set('')
        except tk.TclError:
            logging.warning("TclError during _on_condition_type_change (widget likely destroyed)")
        except KeyError as e:
             logging.warning(f"KeyError during _on_condition_type_change: {e}")


    def _delete_condition_line(self, line_frame_to_delete):
        """Supprime une ligne de condition de l'UI et de la liste interne."""
        index_to_delete = -1
        for i, line_info in enumerate(self.condition_lines):
            if line_info['frame'] == line_frame_to_delete:
                index_to_delete = i
                break

        if index_to_delete != -1:
            # Supprimer de la liste interne
            del self.condition_lines[index_to_delete]
            # Détruire le frame Tkinter associé
            try:
                line_frame_to_delete.destroy()
            except tk.TclError:
                 logging.warning("TclError during line frame destruction (already destroyed?)")

            # Mettre à jour la scrollregion
            self._update_scrollregion()
            # Mettre à jour les labels de logique (important si la première ligne est supprimée)
            self._update_logic_labels()
            logging.debug(f"Ligne condition {index_to_delete} supprimée.")
        else:
            logging.warning("Tentative de suppression d'une ligne de condition non trouvée.")

    def buttonbox(self):
        """Crée les boutons OK et Annuler en bas du pop-up."""
        box = ttk.Frame(self)
        ok_button = ttk.Button(box, text="OK", width=10, command=self.ok, default=tk.ACTIVE)
        ok_button.pack(side=tk.LEFT, padx=5, pady=5)
        cancel_button = ttk.Button(box, text="Annuler", width=10, command=self.cancel)
        cancel_button.pack(side=tk.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack(side=tk.BOTTOM, pady=(10, 0)) # Pack at the bottom


    def validate(self):
        """Valide les données entrées dans toutes les lignes avant de fermer avec OK."""
        logging.debug("Validation éditeur conditions...")
        validated_conditions = []
        logic = self.logic_var.get()

        if not logic:
            messagebox.showwarning("Validation", "Veuillez sélectionner une logique globale (ET/OU).", parent=self)
            return 0

        if not self.condition_lines:
             logging.debug("Validation OK (aucune condition spécifiée).")
             self.result_logic = logic
             self.result_conditions = []
             return 1

        for i, line_info in enumerate(self.condition_lines):
            # Check if frame still exists before accessing widgets
            if not line_info['frame'].winfo_exists():
                logging.warning(f"Validation: Skipping line {i+1}, frame does not exist.")
                continue # Skip this line if the frame was destroyed

            widgets = line_info['widgets']
            condition_data = {'condition_id': line_info['condition_id']}

            try:
                cond_type = widgets['type_var'].get()
                operator = widgets['operator_var'].get()
                value_str = widgets['value_var'].get().strip()

                if not cond_type:
                    messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez sélectionner un type.", parent=self)
                    return 0
                condition_data['type'] = cond_type

                if not operator:
                    messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez sélectionner un opérateur.", parent=self)
                    return 0
                condition_data['operator'] = operator

                if not value_str:
                    messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez entrer une valeur.", parent=self)
                    return 0

                if cond_type == 'Capteur':
                    sensor_name = widgets['sensor_var'].get()
                    if not sensor_name:
                        messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez sélectionner un capteur.", parent=self)
                        return 0
                    sensor_id = next((sid for name, sid in self.available_sensors if name == sensor_name), None)
                    if not sensor_id:
                        messagebox.showwarning("Validation", f"Ligne {i+1}: Capteur '{sensor_name}' invalide.", parent=self)
                        return 0
                    condition_data['id'] = sensor_id

                    try:
                        condition_data['threshold'] = float(value_str.replace(',', '.'))
                    except ValueError:
                        messagebox.showwarning("Validation", f"Ligne {i+1}: Seuil '{value_str}' invalide (numérique attendu).", parent=self)
                        return 0
                    if operator not in SENSOR_OPERATORS:
                         messagebox.showwarning("Validation", f"Ligne {i+1}: Opérateur '{operator}' invalide pour capteur.", parent=self)
                         return 0


                elif cond_type == 'Heure':
                    if not TIME_REGEX.match(value_str):
                        messagebox.showwarning("Validation", f"Ligne {i+1}: Heure '{value_str}' invalide (HH:MM attendu).", parent=self)
                        return 0
                    condition_data['value'] = value_str
                    condition_data['id'] = None
                    if operator not in TIME_OPERATORS:
                        messagebox.showwarning("Validation", f"Ligne {i+1}: Opérateur '{operator}' invalide pour heure.", parent=self)
                        return 0

                validated_conditions.append(condition_data)

            except tk.TclError:
                 logging.warning(f"Validation: TclError accessing widgets for line {i+1}. Skipping.")
                 # Decide if you want to fail validation or just skip the broken line
                 # For now, let's skip it, assuming it was deleted.
                 continue
            except KeyError as e:
                logging.error(f"Validation: KeyError accessing widget for line {i+1}: {e}. Failing.")
                messagebox.showerror("Erreur Interne", f"Erreur de validation (KeyError) pour la ligne {i+1}. Vérifiez les logs.", parent=self)
                return 0


        self.result_logic = logic
        self.result_conditions = validated_conditions
        logging.debug(f"Validation éditeur OK. Logique: {self.result_logic}, Conditions: {len(self.result_conditions)}")
        return 1

    def apply(self):
        """Appelé automatiquement par simpledialog si validate() retourne True."""
        if self.result_logic is not None and self.result_conditions is not None:
            logging.info(f"Application des changements de l'éditeur pour règle {self.rule_id}, type {self.condition_type}")
            self.app.update_rule_conditions_from_editor(
                self.rule_id,
                self.condition_type,
                self.result_logic,
                self.result_conditions
            )
        else:
            logging.error("Apply appelé mais les résultats de la validation sont manquants.")

#--------------------------------------------------------------------------
# FIN CLASSE ConditionEditor
#--------------------------------------------------------------------------


#--------------------------------------------------------------------------
# CLASSE PRINCIPALE DE L'APPLICATION (Peu de changements ici, juste pour le contexte)
#--------------------------------------------------------------------------
class GreenhouseApp:
    """Classe principale de l'application de gestion de serre."""

    def __init__(self, root):
        """Initialise l'application."""
        self.root = root
        self.root.title("Gestionnaire de Serre Connectée")
        try:
            self.root.geometry("1300x800")
        except tk.TclError as e:
            logging.warning(f"Erreur lors de la définition de la géométrie initiale: {e}")

        # Configuration du style ttk pour les widgets
        style = ttk.Style(self.root)
        # Style pour les boutons rouges (suppression) - Utiliser "Red.TButton"
        style.configure("Red.TButton", foreground="white", background="red", font=('Helvetica', 10, 'bold'))
        style.map("Red.TButton",
                  foreground=[('pressed', 'white'), ('active', 'white')],
                  background=[('pressed', 'darkred'), ('active', '#FF5555')]) # Slightly lighter red on active
        # Style pour les labels résumant les conditions (plus petit, italique)
        style.configure("RuleSummary.TLabel", font=('Helvetica', 8, 'italic'))

        # Mise en place du logging via une queue pour la communication inter-thread
        self.log_queue = queue.Queue()
        # Assume logger_setup is called elsewhere or basicConfig is sufficient
        # setup_logging(self.log_queue) # Configurer le handler de logging

        # Chargement de la configuration depuis le fichier YAML
        self.config = load_config(DEFAULT_CONFIG_FILE)
        self.aliases = self.config.get('aliases', {"sensors": {}, "devices": {}, "outlets": {}})
        loaded_rules = self.config.get('rules', [])

        # Nettoyage et initialisation des règles chargées
        self.rules = []
        rule_counter = 1
        for rule_data in loaded_rules:
            if not isinstance(rule_data, dict): continue

            if 'id' not in rule_data or not rule_data['id']:
                rule_data['id'] = str(uuid.uuid4())

            rule_data.setdefault('name', f"Règle {rule_counter}")
            rule_data.setdefault('trigger_logic', 'ET')
            rule_data.setdefault('conditions', [])
            rule_data.setdefault('until_logic', 'OU')
            rule_data.setdefault('until_conditions', [])

            # --- Data Migration/Cleanup ---
            # Remove obsolete top-level condition fields if they exist
            rule_data.pop('sensor_id', None)
            rule_data.pop('operator', None)
            rule_data.pop('threshold', None)
            rule_data.pop('until_condition', None) # Old simple structure

            # Ensure unique IDs for conditions within lists
            for cond_list_key in ['conditions', 'until_conditions']:
                if cond_list_key in rule_data and isinstance(rule_data[cond_list_key], list):
                    for cond in rule_data[cond_list_key]:
                        if isinstance(cond, dict):
                            cond.setdefault('condition_id', str(uuid.uuid4()))
                            # Ensure 'type' exists (migration from older format)
                            if 'type' not in cond:
                                if 'threshold' in cond:
                                    cond['type'] = 'Capteur'
                                elif 'value' in cond and ':' in str(cond['value']):
                                     cond['type'] = 'Heure'
                                else:
                                     cond['type'] = 'Inconnu' # Mark for potential fixing
                            # Ensure 'id' exists for sensor conditions (migration)
                            if cond['type'] == 'Capteur' and 'id' not in cond:
                                cond['id'] = None # Mark as invalid, needs user selection

            self.rules.append(rule_data)
            rule_counter += 1
        logging.info(f"{len(self.rules)} règles chargées depuis {DEFAULT_CONFIG_FILE}.")

        # Initialisation des gestionnaires de périphériques et des listes d'état
        self.kasa_devices = {} # {mac: {'info': dict, 'controller': DeviceController, 'ip': str}}
        self.temp_manager = TempSensorManager()
        self.light_manager = BH1750Manager()
        self.available_sensors = [] # [(alias, id), ...] pour les combobox
        self.available_kasa_strips = [] # [(alias, mac), ...] pour les combobox
        self.available_outlets = {} # {mac: [(alias_prise, index), ...]} pour les combobox
        self.monitoring_active = False
        self.monitoring_thread = None
        self.asyncio_loop = None
        self.ui_update_job = None
        self.live_kasa_states = {} # {mac: {index: bool}}
        self.rule_widgets = {} # {rule_id: {'frame': ttk.Frame, 'widgets': dict}}

        # Création de l'interface graphique
        self.create_widgets()
        self.populate_initial_ui_data()
        self.update_log_display()
        self.discover_all_devices()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Fonctions Alias (Gestion des noms personnalisés) ---
    def get_alias(self, item_type, item_id, sub_id=None):
        """Récupère l'alias (nom personnalisé) pour un capteur, appareil ou prise."""
        # Default to item_id if no alias structure exists or item not found
        default_name = str(item_id)
        aliases_root = self.config.get('aliases', {})

        try:
            if item_type == 'sensor':
                return aliases_root.get('sensors', {}).get(str(item_id), default_name)
            elif item_type == 'device':
                 return aliases_root.get('devices', {}).get(str(item_id), default_name)
            elif item_type == 'outlet' and sub_id is not None:
                device_outlets = aliases_root.get('outlets', {}).get(str(item_id), {})
                # Fallback name construction
                fallback_name = f"Prise {sub_id}"
                # Try to get Kasa's default name if available
                if str(item_id) in self.kasa_devices:
                    kasa_info = self.kasa_devices[str(item_id)].get('info', {})
                    outlet_info_list = kasa_info.get('outlets', [])
                    outlet_info = next((o for o in outlet_info_list if o.get('index') == sub_id), None)
                    if outlet_info and outlet_info.get('alias'):
                        fallback_name = outlet_info['alias'] # Use Kasa alias as fallback
                return device_outlets.get(str(sub_id), fallback_name)
            else:
                 # Invalid type or missing sub_id for outlet
                 return default_name
        except Exception as e:
            logging.warning(f"Error getting alias for {item_type} {item_id} (sub:{sub_id}): {e}")
            return default_name # Return raw ID on error


    def update_alias(self, item_type, item_id, new_alias, sub_id=None):
        """Met à jour l'alias d'un élément dans la configuration."""
        # Ensure 'aliases' structure exists
        if 'aliases' not in self.config:
            self.config['aliases'] = {"sensors": {}, "devices": {}, "outlets": {}}

        aliases_root = self.config['aliases']

        try:
            if item_type == 'outlet' and sub_id is not None:
                aliases_root.setdefault('outlets', {}).setdefault(str(item_id), {})[str(sub_id)] = new_alias
            elif item_type == 'device':
                aliases_root.setdefault('devices', {})[str(item_id)] = new_alias
            elif item_type == 'sensor':
                aliases_root.setdefault('sensors', {})[str(item_id)] = new_alias
            else:
                logging.error(f"Type d'élément inconnu pour la mise à jour d'alias: {item_type}")
                return

            # Update the live self.aliases used by get_alias
            self.aliases = self.config['aliases']
            logging.info(f"Alias mis à jour pour {item_type} {item_id}" + (f"[{sub_id}]" if sub_id is not None else "") + f": '{new_alias}'")
            # Note: Actual saving happens via the "Save" button

        except Exception as e:
            logging.error(f"Error updating alias for {item_type} {item_id} (sub:{sub_id}): {e}")


    def edit_alias_dialog(self, item_type, item_id, current_name, sub_id=None):
        """Ouvre une boîte de dialogue pour modifier l'alias d'un élément."""
        prompt = f"Entrez le nouveau nom pour {item_type} '{current_name}'"
        title = "Modifier Alias"

        if item_type == 'outlet':
            device_alias = self.get_alias('device', item_id)
            prompt = f"Nouveau nom pour la prise '{current_name}'\n(Appareil: {device_alias} [{item_id}])"
            title = "Modifier Alias Prise"
        elif item_type == 'device':
            prompt = f"Nouveau nom pour l'appareil Kasa '{current_name}'\n(MAC: {item_id})"
            title = "Modifier Alias Appareil Kasa"
        elif item_type == 'sensor':
             prompt = f"Nouveau nom pour le capteur '{current_name}'\n(ID: {item_id})"
             title = "Modifier Alias Capteur"

        new_name = simpledialog.askstring(title, prompt,
                                          initialvalue=current_name, parent=self.root)

        if new_name and new_name.strip() and new_name.strip() != current_name:
            new_name = new_name.strip()
            self.update_alias(item_type, item_id, new_name, sub_id)
            # Refresh UI elements that display this alias
            self.refresh_device_lists() # Updates internal lists and rule dropdowns
            self.update_status_display() # Updates the status panel display
            # No need to call repopulate_all_rule_dropdowns separately, refresh_device_lists does it.
            self.root.update_idletasks() # Force UI update

    # --- Création des Widgets de l'Interface Principale ---
    def create_widgets(self):
        """Crée tous les widgets principaux de l'interface graphique."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Configure row/column weights for main_frame resizing
        main_frame.rowconfigure(3, weight=1) # PanedWindow row
        main_frame.columnconfigure(0, weight=1)

        # --- Section des Règles (Scrollable) ---
        rules_frame_container = ttk.LabelFrame(main_frame, text="Règles d'Automatisation", padding="10")
        # Use grid for better control over expansion
        rules_frame_container.grid(row=0, column=0, sticky="ew", pady=5)
        rules_frame_container.columnconfigure(0, weight=1) # Allow canvas to expand horizontally

        # Canvas pour la zone scrollable des règles
        self.rules_canvas = tk.Canvas(rules_frame_container, borderwidth=0, highlightthickness=0, height=300) # Set initial height
        # Scrollbar verticale
        scrollbar = ttk.Scrollbar(rules_frame_container, orient="vertical", command=self.rules_canvas.yview)
        # Grid layout for canvas and scrollbar
        self.rules_canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        rules_frame_container.rowconfigure(0, weight=1) # Allow canvas row to expand vertically


        # Frame interne qui contiendra les règles
        self.scrollable_rules_frame = ttk.Frame(self.rules_canvas)
        self.scrollable_rules_frame.bind("<Configure>", lambda e: self._on_rules_frame_configure(e)) # Use specific configure handler
        self.rules_canvas.create_window((0, 0), window=self.scrollable_rules_frame, anchor="nw", tags="frame")
        self.rules_canvas.configure(yscrollcommand=scrollbar.set)

        # --- Bouton Ajouter une Règle ---
        add_rule_button = ttk.Button(main_frame, text="➕ Ajouter une Règle", command=self.add_rule_ui)
        add_rule_button.grid(row=1, column=0, pady=5)

        # --- Section Contrôles (Démarrer/Arrêter/Sauvegarder) ---
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.grid(row=2, column=0, sticky="ew", pady=5)
        self.start_button = ttk.Button(control_frame, text="🟢 Gérer ma Serre", command=self.start_monitoring)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(control_frame, text="🔴 Arrêter", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        save_button = ttk.Button(control_frame, text="💾 Sauvegarder Configuration", command=self.save_configuration)
        save_button.pack(side=tk.RIGHT, padx=5)

        # --- Panneau Divisé pour Statut et Logs ---
        status_log_pane = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        status_log_pane.grid(row=3, column=0, sticky="nsew", pady=5) # Make it expand

        # --- Section Statut Actuel (Scrollable) ---
        status_frame_container = ttk.LabelFrame(status_log_pane, text="Statut Actuel", padding="10")
        status_log_pane.add(status_frame_container, weight=1) # Add to paned window
        status_frame_container.rowconfigure(0, weight=1) # Allow canvas row to expand
        status_frame_container.columnconfigure(0, weight=1) # Allow canvas col to expand


        # Canvas pour la zone scrollable du statut
        status_canvas = tk.Canvas(status_frame_container, borderwidth=0, highlightthickness=0)
        status_scrollbar = ttk.Scrollbar(status_frame_container, orient="vertical", command=status_canvas.yview)
        self.scrollable_status_frame = ttk.Frame(status_canvas)

        status_canvas.grid(row=0, column=0, sticky="nsew")
        status_scrollbar.grid(row=0, column=1, sticky="ns")

        self.scrollable_status_frame.bind("<Configure>", lambda e, c=status_canvas: self._on_generic_frame_configure(e, c))
        status_canvas.create_window((0, 0), window=self.scrollable_status_frame, anchor="nw", tags="frame")
        status_canvas.configure(yscrollcommand=status_scrollbar.set)


        # --- Section Journal d'Événements ---
        log_frame_container = ttk.LabelFrame(status_log_pane, text="Journal d'Événements", padding="10")
        status_log_pane.add(log_frame_container, weight=1) # Add to paned window
        log_frame_container.rowconfigure(0, weight=1) # Allow text widget to expand
        log_frame_container.columnconfigure(0, weight=1)

        # Zone de texte scrollable pour les logs
        self.log_display = scrolledtext.ScrolledText(log_frame_container, wrap=tk.WORD, state=tk.DISABLED, height=15)
        self.log_display.grid(row=0, column=0, sticky="nsew")

        # Dictionnaires pour stocker les références aux widgets dynamiques
        self.status_labels = {} # Pour les labels de statut (capteurs, prises)
        # self.rule_widgets est initialisé dans __init__

    # --- Handlers for scrollable frame configuration ---
    def _on_rules_frame_configure(self, event):
        """Update scrollregion and frame width for the rules canvas."""
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))
        self.rules_canvas.itemconfig("frame", width=event.width)

    def _on_generic_frame_configure(self, event, canvas):
        """Generic handler to update scrollregion and frame width."""
        canvas.configure(scrollregion=canvas.bbox("all"))
        canvas.itemconfig("frame", width=event.width)


    # --- Peuplement Initial de l'UI ---
    def populate_initial_ui_data(self):
        """Ajoute les règles chargées depuis la configuration à l'interface graphique."""
        if not self.rules:
             logging.info("Aucune règle à afficher initialement.")
             # Optionally add a placeholder label
             # ttk.Label(self.scrollable_rules_frame, text="Aucune règle définie.").pack()
             return

        for rule_data in self.rules:
            self.add_rule_ui(rule_data=rule_data)
        # Ensure scrollregion is updated after adding all rules
        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))


    # --- Gestion de l'UI des Règles ---
    def add_rule_ui(self, rule_data=None):
        """Ajoute une nouvelle règle (vide) ou une règle existante à l'interface."""
        is_new_rule = False
        if not rule_data:
            is_new_rule = True
            rule_id = str(uuid.uuid4())
            rule_data = {
                'id': rule_id, 'name': f"Nouvelle Règle {len(self.rules) + 1}",
                'trigger_logic': 'ET', 'conditions': [],
                'target_device_mac': None, 'target_outlet_index': None, 'action': ACTIONS[0],
                'until_logic': 'OU', 'until_conditions': []
            }
            self.rules.append(rule_data)
        else:
            rule_id = rule_data.get('id')
            if not rule_id: # Should have been assigned during load, but safety check
                rule_id = str(uuid.uuid4())
                rule_data['id'] = rule_id

        # --- Création du Frame principal pour cette règle ---
        rule_frame = ttk.Frame(self.scrollable_rules_frame, padding="5", borderwidth=1, relief="groove")
        rule_frame.pack(fill=tk.X, pady=3, padx=2, expand=True) # Expand to fill width
        widgets = {}

        # --- Ligne 1: Nom de la règle et bouton Supprimer ---
        name_frame = ttk.Frame(rule_frame)
        name_frame.pack(side=tk.TOP, fill=tk.X, expand=True)
        widgets['name_label'] = ttk.Label(name_frame, text=rule_data.get('name', 'Sans Nom'), font=('Helvetica', 10, 'bold'))
        widgets['name_label'].pack(side=tk.LEFT, padx=(0, 5), pady=(0, 3))
        widgets['edit_name_button'] = ttk.Button(name_frame, text="✎", width=2,
                                                 command=lambda r_id=rule_id: self.edit_rule_name_dialog(r_id))
        widgets['edit_name_button'].pack(side=tk.LEFT, padx=(0, 15))
        # Utiliser le style "Red.TButton" pour le bouton supprimer
        delete_rule_button = ttk.Button(name_frame, text="❌", width=3, style="Red.TButton",
                                        command=lambda rid=rule_id: self.delete_rule(rid))
        delete_rule_button.pack(side=tk.RIGHT, padx=5)

        # --- Ligne 2: Conditions SI et partie ALORS ---
        main_line_frame = ttk.Frame(rule_frame)
        main_line_frame.pack(side=tk.TOP, fill=tk.X, expand=True, pady=3)

        widgets['si_summary_label'] = ttk.Label(main_line_frame,
                                                text=self._generate_condition_summary(rule_data.get('conditions', []), rule_data.get('trigger_logic', 'ET')),
                                                style="RuleSummary.TLabel", anchor="w", width=40)
        widgets['si_summary_label'].pack(side=tk.LEFT, padx=(5, 0))
        widgets['edit_si_button'] = ttk.Button(main_line_frame, text="SI...", width=5,
                                               command=lambda r_id=rule_id: self.open_condition_editor(r_id, 'trigger'))
        widgets['edit_si_button'].pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(main_line_frame, text="ALORS").pack(side=tk.LEFT, padx=(10, 2))
        widgets['kasa_var'] = tk.StringVar()
        widgets['kasa_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['kasa_var'], width=25, state="readonly")
        widgets['kasa_combo']['values'] = [name for name, _mac in self.available_kasa_strips]
        widgets['kasa_combo'].pack(side=tk.LEFT, padx=2)
        widgets['kasa_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.update_outlet_options(rid))

        widgets['outlet_var'] = tk.StringVar()
        widgets['outlet_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['outlet_var'], width=20, state="readonly")
        widgets['outlet_combo']['values'] = []
        widgets['outlet_combo'].pack(side=tk.LEFT, padx=2)
        widgets['outlet_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['action_var'] = tk.StringVar()
        widgets['action_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['action_var'], values=ACTIONS, width=5, state="readonly")
        widgets['action_combo'].pack(side=tk.LEFT, padx=2)
        widgets['action_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # --- Ligne 3: Conditions JUSQU'À ---
        until_frame = ttk.Frame(rule_frame)
        until_frame.pack(side=tk.TOP, fill=tk.X, expand=True, padx=(30, 0), pady=(0, 2))
        ttk.Label(until_frame, text="↳").pack(side=tk.LEFT, padx=(0, 5))
        widgets['until_summary_label'] = ttk.Label(until_frame,
                                                   text=self._generate_condition_summary(rule_data.get('until_conditions', []), rule_data.get('until_logic', 'OU')),
                                                   style="RuleSummary.TLabel", anchor="w", width=40)
        widgets['until_summary_label'].pack(side=tk.LEFT, padx=(0,0))
        widgets['edit_until_button'] = ttk.Button(until_frame, text="JUSQU'À...", width=10,
                                                  command=lambda r_id=rule_id: self.open_condition_editor(r_id, 'until'))
        widgets['edit_until_button'].pack(side=tk.LEFT, padx=(5, 10))

        self.rule_widgets[rule_id] = {'frame': rule_frame, 'widgets': widgets}

        if not is_new_rule:
            self._populate_rule_ui_from_data(rule_id, rule_data)

        # Update scrollregion after adding the new rule frame
        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))


    def _generate_condition_summary(self, conditions, logic):
        """Génère une chaîne résumant le nombre de conditions et la logique."""
        if not isinstance(conditions, list): conditions = []
        count = len(conditions)
        if count == 0:
            return "(Aucune condition)"
        elif count == 1:
            # Find the condition details for better summary
            cond = conditions[0]
            cond_type = cond.get('type')
            op = cond.get('operator')
            if cond_type == 'Capteur':
                name = self.get_alias('sensor', cond.get('id'))
                val = cond.get('threshold')
                return f"({name} {op} {val})"
            elif cond_type == 'Heure':
                val = cond.get('value')
                return f"(Heure {op} {val})"
            else:
                 return "(1 condition)" # Fallback
        else:
            logic_str = logic if logic in LOGIC_OPERATORS else 'ET'
            return f"({count} conditions - {logic_str})"

    def edit_rule_name_dialog(self, rule_id):
        """Ouvre une boîte de dialogue pour modifier le nom d'une règle."""
        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Impossible de modifier le nom: Règle {rule_id} non trouvée.")
            return

        current_name = rule_data.get('name', '')
        new_name = simpledialog.askstring("Modifier Nom de Règle",
                                          f"Entrez le nouveau nom pour la règle '{current_name}'",
                                          initialvalue=current_name, parent=self.root)

        if new_name and new_name.strip() and new_name.strip() != current_name:
            new_name = new_name.strip()
            rule_data['name'] = new_name
            if rule_id in self.rule_widgets and 'name_label' in self.rule_widgets[rule_id]['widgets']:
                try:
                    if self.rule_widgets[rule_id]['widgets']['name_label'].winfo_exists():
                        self.rule_widgets[rule_id]['widgets']['name_label'].config(text=new_name)
                except tk.TclError: pass # Ignore if widget destroyed
            logging.info(f"Nom de la règle {rule_id} mis à jour: '{new_name}'")

    def _populate_rule_ui_from_data(self, rule_id, rule_data):
        """Peuple les widgets d'une règle existante avec ses données."""
        if rule_id not in self.rule_widgets:
            logging.warning(f"Tentative de peupler l'UI pour règle {rule_id} non trouvée dans rule_widgets.")
            return

        widgets = self.rule_widgets[rule_id]['widgets']

        # Check if frame exists before proceeding
        if not self.rule_widgets[rule_id]['frame'].winfo_exists():
             logging.warning(f"Frame for rule {rule_id} does not exist during populate.")
             # Clean up the widget entry if the frame is gone
             del self.rule_widgets[rule_id]
             return

        # Update name and summaries
        try:
            if widgets['name_label'].winfo_exists():
                widgets['name_label'].config(text=rule_data.get('name', 'Sans Nom'))
            if widgets['si_summary_label'].winfo_exists():
                widgets['si_summary_label'].config(text=self._generate_condition_summary(rule_data.get('conditions', []), rule_data.get('trigger_logic', 'ET')))
            if widgets['until_summary_label'].winfo_exists():
                widgets['until_summary_label'].config(text=self._generate_condition_summary(rule_data.get('until_conditions', []), rule_data.get('until_logic', 'OU')))
        except tk.TclError:
             logging.warning(f"Erreur TclError lors de la mise à jour des labels pour la règle {rule_id} (widget détruit?).")
             return # Stop if basic widgets are gone

        # Target info
        kasa_mac = rule_data.get('target_device_mac')
        outlet_index = rule_data.get('target_outlet_index') # Can be None or int

        # Update Kasa, Outlet, Action combos
        try:
            kasa_combo = widgets['kasa_combo']
            outlet_combo = widgets['outlet_combo']
            action_combo = widgets['action_combo']

            if not kasa_combo.winfo_exists() or not outlet_combo.winfo_exists() or not action_combo.winfo_exists():
                 logging.warning(f"Combobox missing for rule {rule_id} during populate.")
                 return

            # Update Kasa device selection
            if kasa_mac:
                kasa_alias = self.get_alias('device', kasa_mac)
                kasa_options = list(kasa_combo['values']) # Get tuple and convert to list
                if kasa_alias in kasa_options:
                    widgets['kasa_var'].set(kasa_alias)
                    # Store desired index for pre-selection after updating options
                    self.rule_widgets[rule_id]['desired_outlet_index'] = outlet_index
                    # Update outlet options for this device and pre-select
                    self.update_outlet_options(rule_id, preselect_outlet_index=outlet_index)
                else:
                    # Kasa device no longer available or alias changed
                    widgets['kasa_var'].set('')
                    outlet_combo['values'] = []
                    widgets['outlet_var'].set('')
            else:
                # No Kasa device selected
                widgets['kasa_var'].set('')
                outlet_combo['values'] = []
                widgets['outlet_var'].set('')

            # Update Action selection
            action = rule_data.get('action', ACTIONS[0])
            if action in ACTIONS:
                widgets['action_var'].set(action)
            else:
                widgets['action_var'].set(ACTIONS[0]) # Default if invalid

        except tk.TclError:
             logging.warning(f"Erreur TclError lors de la mise à jour des combobox ALORS pour la règle {rule_id}.")
        except KeyError as e:
             logging.warning(f"KeyError accessing widget for rule {rule_id} during populate: {e}")


    def delete_rule(self, rule_id):
        """Supprime une règle de l'UI et de la liste interne."""
        if rule_id in self.rule_widgets:
            try:
                if self.rule_widgets[rule_id]['frame'].winfo_exists():
                    self.rule_widgets[rule_id]['frame'].destroy()
            except tk.TclError: pass # Ignore if already destroyed
            except KeyError: pass # Ignore if frame key missing

            del self.rule_widgets[rule_id]

            initial_len = len(self.rules)
            self.rules = [rule for rule in self.rules if rule.get('id') != rule_id]

            if len(self.rules) < initial_len:
                logging.info(f"Règle {rule_id} supprimée.")
            else:
                logging.warning(f"Règle {rule_id} trouvée dans l'UI mais pas dans les données internes lors de la suppression.")

            # Update scrollregion
            self.scrollable_rules_frame.update_idletasks()
            self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))
            # Adjust canvas frame width after deletion
            self.rules_canvas.itemconfig("frame", width=self.rules_canvas.winfo_width())

        else:
            logging.warning(f"Tentative de suppression de la règle {rule_id} non trouvée dans l'UI.")

    def update_outlet_options(self, rule_id, preselect_outlet_index=None):
        """Met à jour les options du combobox de prise en fonction de l'appareil Kasa sélectionné."""
        if rule_id not in self.rule_widgets: return

        widgets = self.rule_widgets[rule_id]['widgets']
        # Ensure widgets exist before proceeding
        if not widgets['kasa_var'].get() or not widgets['outlet_combo'].winfo_exists():
             # logging.debug(f"update_outlet_options: Kasa var empty or outlet combo destroyed for rule {rule_id}")
             return

        selected_kasa_name = widgets['kasa_var'].get()
        selected_mac = next((mac for name, mac in self.available_kasa_strips if name == selected_kasa_name), None)

        outlet_options = []
        current_outlet_alias = ""

        if selected_mac and selected_mac in self.available_outlets:
            outlet_options = [name for name, _index in self.available_outlets[selected_mac]]
            if preselect_outlet_index is not None:
                current_outlet_alias = next((name for name, index in self.available_outlets[selected_mac] if index == preselect_outlet_index), "")

        try:
            outlet_combo = widgets['outlet_combo']
            outlet_var = widgets['outlet_var']
            outlet_combo['values'] = outlet_options # Update dropdown list

            if current_outlet_alias and current_outlet_alias in outlet_options:
                outlet_var.set(current_outlet_alias) # Pre-select found alias
            elif outlet_options:
                 outlet_var.set(outlet_options[0]) # Select first if no pre-selection or pre-selection invalid
            else:
                outlet_var.set('') # Clear if no options

        except tk.TclError:
            pass # Ignore if widget destroyed
        except KeyError as e:
             logging.warning(f"KeyError accessing outlet widget for rule {rule_id}: {e}")

        # Update rule data after changing device or outlet
        self.on_rule_change(rule_id)

    def on_rule_change(self, rule_id):
        """Met à jour les données internes de la règle (partie ALORS) quand un combobox change."""
        if rule_id not in self.rule_widgets: return

        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data:
            logging.warning(f"on_rule_change: Règle {rule_id} non trouvée dans les données.")
            return

        widgets = self.rule_widgets[rule_id]['widgets']

        try:
            # Ensure widgets exist before getting values
            if not all(w in widgets and widgets[w].winfo_exists() for w in ['kasa_var', 'outlet_var', 'action_var', 'kasa_combo', 'outlet_combo']):
                 logging.warning(f"on_rule_change: Widget missing for rule {rule_id}")
                 return

            kasa_name = widgets['kasa_var'].get()
            outlet_name = widgets['outlet_var'].get()
            action = widgets['action_var'].get()

            kasa_mac = next((m for n, m in self.available_kasa_strips if n == kasa_name), None)

            outlet_index = None
            if kasa_mac and kasa_mac in self.available_outlets:
                 outlet_index = next((idx for name, idx in self.available_outlets[kasa_mac] if name == outlet_name), None)

            # Update rule data only if values have actually changed
            changed = False
            if rule_data.get('target_device_mac') != kasa_mac:
                rule_data['target_device_mac'] = kasa_mac
                changed = True
            if rule_data.get('target_outlet_index') != outlet_index:
                 rule_data['target_outlet_index'] = outlet_index
                 changed = True
            if rule_data.get('action') != action:
                rule_data['action'] = action
                changed = True

            if changed:
                logging.debug(f"Partie ALORS de la règle {rule_id} mise à jour dans les données: MAC={kasa_mac}, Index={outlet_index}, Action={action}")

        except tk.TclError:
             logging.warning(f"on_rule_change: TclError accessing widget for rule {rule_id}")
        except KeyError as e:
            logging.warning(f"on_rule_change: KeyError accessing widget for rule {rule_id}: {e}")


    def repopulate_all_rule_dropdowns(self):
        """Met à jour les listes déroulantes Kasa/Prise pour toutes les règles affichées."""
        logging.debug("Repopulation des listes déroulantes Kasa/Prise pour toutes les règles.")
        kasa_names = [name for name, _mac in self.available_kasa_strips]

        # Iterate through a copy of keys in case rules are deleted during repopulation
        for rule_id in list(self.rule_widgets.keys()):
            if rule_id not in self.rule_widgets: continue # Rule was deleted

            data = self.rule_widgets[rule_id]
            widgets = data.get('widgets', {})
            rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
            if not rule_data: continue

            # Ensure widgets exist
            if not all(w in widgets and widgets[w].winfo_exists() for w in ['kasa_combo', 'outlet_combo']):
                logging.warning(f"repopulate: Combobox missing for rule {rule_id}")
                continue

            current_kasa_mac = rule_data.get('target_device_mac')
            current_kasa_name = self.get_alias('device', current_kasa_mac) if current_kasa_mac else ""

            try:
                kasa_combo = widgets['kasa_combo']
                kasa_combo['values'] = kasa_names # Update list

                if current_kasa_name in kasa_names:
                    widgets['kasa_var'].set(current_kasa_name)
                    # Retrieve desired outlet index (either from save data or temporary state)
                    desired_outlet_index = data.get('desired_outlet_index', rule_data.get('target_outlet_index'))
                    self.update_outlet_options(rule_id, preselect_outlet_index=desired_outlet_index)
                    # Clear temporary state after use
                    if 'desired_outlet_index' in data: del data['desired_outlet_index']
                else:
                    widgets['kasa_var'].set('')
                    widgets['outlet_combo']['values'] = []
                    widgets['outlet_var'].set('')
            except tk.TclError:
                 logging.warning(f"Erreur TclError lors de la repopulation des dropdowns pour règle {rule_id}.")
            except KeyError as e:
                 logging.warning(f"repopulate: KeyError accessing widget for rule {rule_id}: {e}")


    # --- Ouverture de l'éditeur de conditions ---
    def open_condition_editor(self, rule_id, condition_type):
        """Ouvre le pop-up ConditionEditor pour éditer les conditions SI ou JUSQU'À."""
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Impossible d'ouvrir l'éditeur: Règle {rule_id} non trouvée.")
            messagebox.showerror("Erreur", f"Impossible de trouver les données pour la règle {rule_id}.", parent=self.root)
            return

        if condition_type == 'trigger':
            logic = rule_data.get('trigger_logic', 'ET')
            conditions = list(rule_data.get('conditions', [])) # Pass a copy
            title = f"Modifier Conditions SI - Règle '{rule_data.get('name', rule_id)}'"
        elif condition_type == 'until':
            logic = rule_data.get('until_logic', 'OU')
            conditions = list(rule_data.get('until_conditions', [])) # Pass a copy
            title = f"Modifier Conditions JUSQU'À - Règle '{rule_data.get('name', rule_id)}'"
        else:
            logging.error(f"Type de condition inconnu demandé pour l'éditeur: {condition_type}")
            return

        logging.debug(f"Ouverture éditeur pour règle {rule_id}, type {condition_type}")
        # Pass self.available_sensors which is [(alias, id), ...]
        editor = ConditionEditor(self.root, title, rule_id, condition_type, logic, conditions, self.available_sensors, self)
        # Editor handles the rest via its apply() method calling back to update_rule_conditions_from_editor

    # --- Méthode appelée par l'éditeur après clic sur OK et validation ---
    def update_rule_conditions_from_editor(self, rule_id, condition_type, new_logic, new_conditions):
        """Met à jour les données de la règle et l'UI principale après édition via le pop-up."""
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Échec mise à jour depuis éditeur: Règle {rule_id} non trouvée.")
            return

        logging.info(f"Mise à jour des conditions '{condition_type}' pour la règle {rule_id}. Logique: {new_logic}, Nombre: {len(new_conditions)}")
        logging.debug(f"Nouvelles conditions: {new_conditions}")

        widgets = self.rule_widgets.get(rule_id, {}).get('widgets', {})

        if condition_type == 'trigger':
            rule_data['trigger_logic'] = new_logic
            rule_data['conditions'] = new_conditions
            if 'si_summary_label' in widgets:
                try:
                    if widgets['si_summary_label'].winfo_exists():
                         widgets['si_summary_label'].config(text=self._generate_condition_summary(new_conditions, new_logic))
                except tk.TclError: pass
        elif condition_type == 'until':
            rule_data['until_logic'] = new_logic
            rule_data['until_conditions'] = new_conditions
            if 'until_summary_label' in widgets:
                try:
                    if widgets['until_summary_label'].winfo_exists():
                        widgets['until_summary_label'].config(text=self._generate_condition_summary(new_conditions, new_logic))
                except tk.TclError: pass

    # --- Découverte / Rafraîchissement des Périphériques ---
    def discover_all_devices(self):
        """Lance la découverte de tous les types de périphériques (Capteurs T°, Lux, Kasa)."""
        logging.info("Lancement de la découverte de tous les périphériques...")
        # Temp sensors (sync)
        try:
            self.temp_manager.discover_sensors()
            logging.info(f"Découverte Température: {len(self.temp_manager.sensors)} capteur(s) trouvé(s).")
        except Exception as e:
            logging.error(f"Erreur lors de la découverte des capteurs de température: {e}")

        # Light sensors (sync)
        try:
            self.light_manager.scan_sensors()
            active_light_sensors = self.light_manager.get_active_sensors()
            logging.info(f"Découverte Lumière (BH1750): {len(active_light_sensors)} capteur(s) trouvé(s).")
        except Exception as e:
            logging.error(f"Erreur lors de la découverte des capteurs de lumière: {e}")

        # Kasa devices (async)
        threading.Thread(target=self._run_kasa_discovery_async, daemon=True).start()

    def _run_kasa_discovery_async(self):
        """Exécute la découverte Kasa asynchrone dans une boucle d'événements."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._async_discover_kasa())
        except Exception as e:
             logging.error(f"Erreur dans _run_kasa_discovery_async: {e}")


    async def _async_discover_kasa(self):
        """Tâche asynchrone pour découvrir les appareils Kasa sur le réseau."""
        logging.info("Début découverte Kasa asynchrone...")
        discoverer = DeviceDiscoverer()
        discovered_kasa = []
        try:
            discovered_kasa = await discoverer.discover()
        except Exception as e:
            logging.error(f"Erreur critique pendant la découverte Kasa: {e}")

        new_kasa_devices = {}
        tasks_initial_state = []

        for dev_info in discovered_kasa:
            ip = dev_info.get('ip')
            mac = dev_info.get('mac')
            alias = dev_info.get('alias', 'N/A')

            if not ip or not mac:
                logging.warning(f"Appareil Kasa découvert sans IP ou MAC: Alias='{alias}', Info={dev_info}")
                continue

            is_strip = dev_info.get('is_strip', False)
            is_plug = dev_info.get('is_plug', False)

            # Only add controllable devices (plugs/strips)
            if is_strip or is_plug:
                ctrl = DeviceController(ip, is_strip, is_plug)
                new_kasa_devices[mac] = {'info': dev_info, 'controller': ctrl, 'ip': ip }

                # If monitoring is not active, try to turn off outlets for safety
                if not self.monitoring_active:
                    logging.debug(f"Ajout tâche d'extinction initiale pour {alias} ({mac})")
                    tasks_initial_state.append(ctrl.turn_all_outlets_off())
            else:
                 logging.debug(f"Appareil Kasa ignoré (non contrôlable): {alias} ({mac})")


        # Execute initial turn-off tasks if needed
        if tasks_initial_state:
             logging.info(f"Exécution de {len(tasks_initial_state)} tâches d'extinction initiale Kasa...")
             try:
                 results = await asyncio.gather(*tasks_initial_state, return_exceptions=True)
                 for i, res in enumerate(results):
                     if isinstance(res, Exception):
                         # Log the error, but finding the specific device is hard here
                         logging.error(f"Erreur lors de l'extinction initiale Kasa (tâche {i}): {res}")
             except Exception as e_gather:
                 logging.error(f"Erreur imprévue durant gather pour l'extinction initiale: {e_gather}")
             logging.info("Tâches d'extinction initiale Kasa terminées.")

        # Update the main Kasa devices list
        self.kasa_devices = new_kasa_devices
        logging.info(f"Découverte Kasa terminée: {len(self.kasa_devices)} appareil(s) contrôlable(s) trouvé(s).")

        # Schedule UI refresh in the main Tkinter thread
        self.root.after(100, self.refresh_device_lists)

    def refresh_device_lists(self):
        """Met à jour les listes internes (available_sensors, etc.) et rafraîchit l'UI."""
        logging.info("Rafraîchissement des listes de périphériques pour l'UI...")

        # --- Update available sensors ---
        temp_sensor_ids = []
        light_sensor_ids = []
        try: temp_sensor_ids = [s.id for s in self.temp_manager.sensors]
        except Exception as e: logging.error(f"Erreur get temp sensor IDs: {e}")
        try:
            # Use hex representation consistent with how they might be stored/used
            light_sensor_ids = [hex(addr) for addr in self.light_manager.get_active_sensors()]
        except Exception as e: logging.error(f"Erreur get light sensor IDs: {e}")

        # Combine, ensure uniqueness, and create (alias, id) list, sorted by alias
        all_sensor_ids = set(temp_sensor_ids + light_sensor_ids)
        self.available_sensors = sorted(
            [(self.get_alias('sensor', sensor_id), sensor_id) for sensor_id in all_sensor_ids],
            key=lambda x: x[0] # Sort by alias
        )
        logging.debug(f"Capteurs disponibles mis à jour: {self.available_sensors}")

        # --- Update available Kasa devices and outlets ---
        self.available_kasa_strips = [] # List [(alias_device, mac), ...]
        self.available_outlets = {} # Dict {mac: [(alias_outlet, index), ...]}

        # Sort Kasa device MACs by their alias for consistent display
        sorted_kasa_macs = sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m))

        for mac in sorted_kasa_macs:
            data = self.kasa_devices[mac]
            device_alias = self.get_alias('device', mac)
            # Add the device to the list for the Kasa combobox
            self.available_kasa_strips.append((device_alias, mac))

            outlets_for_device = []
            # If it's a strip or plug, get its outlets
            if data['info'].get('is_strip') or data['info'].get('is_plug'):
                # Iterate through outlet info provided by discovery
                for outlet_data in data['info'].get('outlets', []):
                    outlet_index = outlet_data.get('index')
                    if outlet_index is not None: # Ensure we have an index
                        outlet_alias = self.get_alias('outlet', mac, outlet_index)
                        outlets_for_device.append((outlet_alias, outlet_index))

            # Store the outlets for this device, sorted by index
            self.available_outlets[mac] = sorted(outlets_for_device, key=lambda x: x[1])

        logging.debug(f"Appareils Kasa disponibles mis à jour: {self.available_kasa_strips}")
        logging.debug(f"Prises Kasa disponibles mises à jour: {self.available_outlets}")

        # --- Refresh UI ---
        self.repopulate_all_rule_dropdowns() # Update dropdowns in existing rules
        self.update_status_display() # Update the status panel display
        logging.info("Listes de périphériques et UI rafraîchies.")


    # --- Fonctions d'Affichage du Statut ---
    def update_status_display(self):
        """Met à jour le panneau de statut avec les informations actuelles des capteurs et prises."""
        logging.debug("Mise à jour de l'affichage du panneau de statut.")

        # Clear current content of the scrollable status frame
        for widget in self.scrollable_status_frame.winfo_children():
            widget.destroy()
        self.status_labels = {} # Reset status label dictionary

        # --- Display Sensors ---
        ttk.Label(self.scrollable_status_frame, text="Capteurs:", font=('Helvetica', 10, 'bold')).pack(anchor='w', pady=(5, 2))

        # Read current values (once for initial display)
        try: all_temp_values = self.temp_manager.read_all_temperatures()
        except Exception: all_temp_values = {}
        try: all_light_values = self.light_manager.read_all_sensors()
        except Exception: all_light_values = {}

        # Iterate through available sensors (already sorted by alias)
        if not self.available_sensors:
             ttk.Label(self.scrollable_status_frame, text=" Aucun capteur détecté").pack(anchor='w', padx=5)

        for sensor_alias, sensor_id in self.available_sensors:
            value_text, unit = "N/A", ""
            # Determine type and get value
            is_temp = sensor_id in all_temp_values
            # Use hex ID for light sensors when checking values
            is_light = sensor_id in [hex(addr) for addr in all_light_values.keys()]


            if is_temp:
                temp_value = all_temp_values.get(sensor_id)
                value_text, unit = (f"{temp_value:.1f}", "°C") if temp_value is not None else ("Erreur", "")
            elif is_light:
                # Use hex ID to get value from dict
                light_value = all_light_values.get(sensor_id) # sensor_id is already hex here from available_sensors
                value_text, unit = (f"{light_value:.0f}", " Lux") if light_value is not None else ("Erreur", "")

            # Create frame for this sensor line
            sensor_frame = ttk.Frame(self.scrollable_status_frame)
            sensor_frame.pack(fill='x', expand=True, padx=5)

            name_label = ttk.Label(sensor_frame, text=f"{sensor_alias}:", width=25, anchor='w') # Fixed width
            name_label.pack(side=tk.LEFT, padx=(5,0))
            value_label = ttk.Label(sensor_frame, text=f"{value_text}{unit}", width=15, anchor='w') # Fixed width
            value_label.pack(side=tk.LEFT, padx=5)
            edit_button = ttk.Button(sensor_frame, text="✎", width=2,
                                     command=lambda s_id=sensor_id, s_name=sensor_alias: self.edit_alias_dialog('sensor', s_id, s_name))
            edit_button.pack(side=tk.LEFT, padx=2)

            # Store references
            self.status_labels[sensor_id] = {'type': 'sensor', 'label_name': name_label, 'label_value': value_label, 'button_edit': edit_button}

        # --- Display Kasa Outlets ---
        ttk.Label(self.scrollable_status_frame, text="Prises Kasa:", font=('Helvetica', 10, 'bold')).pack(anchor='w', pady=(10, 2))

        if not self.kasa_devices:
             ttk.Label(self.scrollable_status_frame, text=" Aucun appareil Kasa détecté").pack(anchor='w', padx=5)

        # Iterate through Kasa devices sorted by alias
        for mac in sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m)):
            data = self.kasa_devices[mac]
            device_alias = self.get_alias('device', mac)
            ip_address = data.get('ip', '?.?.?.?')

            # Create frame for the Kasa device
            device_frame = ttk.Frame(self.scrollable_status_frame)
            device_frame.pack(fill='x', expand=True, padx=5)

            device_name_label = ttk.Label(device_frame, text=f"{device_alias} ({ip_address})", anchor='w')
            device_name_label.pack(side=tk.LEFT, padx=(5,0))
            device_edit_button = ttk.Button(device_frame, text="✎", width=2,
                                            command=lambda m=mac, n=device_alias: self.edit_alias_dialog('device', m, n))
            device_edit_button.pack(side=tk.LEFT, padx=2)

            # Store references (device name not updated dynamically here)
            self.status_labels[mac] = {'type': 'device', 'label_name': device_name_label, 'button_edit': device_edit_button}

            # Display outlets for this device (if available)
            if mac in self.available_outlets:
                for outlet_alias, outlet_index in self.available_outlets[mac]: # Already sorted by index
                    # Get shared state (read periodically during monitoring)
                    current_state_str = self._get_shared_kasa_state(mac, outlet_index)

                    # If state unknown (monitoring not started?), try reading from initial info
                    if current_state_str == "Inconnu":
                        outlet_info_list = data['info'].get('outlets', [])
                        outlet_info = next((o for o in outlet_info_list if o.get('index') == outlet_index), None)
                        if outlet_info:
                             current_state_str = "ON" if outlet_info.get('is_on') else "OFF"

                    # Create frame for the outlet (indented)
                    outlet_frame = ttk.Frame(self.scrollable_status_frame)
                    outlet_frame.pack(fill='x', expand=True, padx=(25, 5)) # Indent using padx

                    outlet_name_label = ttk.Label(outlet_frame, text=f"└─ {outlet_alias}:", width=23, anchor='w') # Fixed width
                    outlet_name_label.pack(side=tk.LEFT, padx=(5,0))
                    outlet_value_label = ttk.Label(outlet_frame, text=current_state_str, width=10, anchor='w') # Fixed width
                    outlet_value_label.pack(side=tk.LEFT, padx=5)
                    outlet_edit_button = ttk.Button(outlet_frame, text="✎", width=2,
                                                    command=lambda m=mac, i=outlet_index, n=outlet_alias: self.edit_alias_dialog('outlet', m, n, sub_id=i))
                    outlet_edit_button.pack(side=tk.LEFT, padx=2)

                    # Store references for dynamic update
                    outlet_key = f"{mac}_{outlet_index}" # Unique key for the outlet
                    self.status_labels[outlet_key] = {'type': 'outlet', 'mac': mac, 'index': outlet_index, 'label_name': outlet_name_label, 'label_value': outlet_value_label, 'button_edit': outlet_edit_button}

        # Update scrollregion of the status canvas after adding elements
        self.scrollable_status_frame.update_idletasks()
        status_canvas = self.scrollable_status_frame.master # Get the parent canvas
        status_canvas.configure(scrollregion=status_canvas.bbox("all"))
        # Ensure frame width matches canvas width
        status_canvas.itemconfig("frame", width=status_canvas.winfo_width())


    def schedule_periodic_updates(self):
        """Planifie la prochaine mise à jour de l'état live et se replanifie."""
        # Update display immediately
        self.update_live_status()
        # Schedule next execution in 5 seconds (5000 ms)
        # Store job ID to be able to cancel it
        self.ui_update_job = self.root.after(5000, self.schedule_periodic_updates)
        # logging.debug(f"Prochaine mise à jour UI planifiée (ID: {self.ui_update_job}).") # Reduce log noise

    def cancel_periodic_updates(self):
        """Annule la mise à jour périodique de l'UI planifiée."""
        if self.ui_update_job:
            logging.debug(f"Annulation de la tâche de mise à jour UI (ID: {self.ui_update_job}).")
            try:
                self.root.after_cancel(self.ui_update_job)
            except tk.TclError as e:
                # Can happen if the task has already run or been cancelled
                logging.warning(f"Erreur lors de l'annulation de la tâche UI {self.ui_update_job}: {e}")
            finally:
                self.ui_update_job = None # Reset ID

    def update_live_status(self):
        """Met à jour les labels de valeur dans le panneau de statut avec les données 'live'."""
        # Only update if monitoring is active (live data is available)
        if not self.monitoring_active:
            return

        # logging.debug("Mise à jour des valeurs live dans le panneau de statut...") # Reduce log noise
        # Get latest values read by the monitoring thread (assumed up-to-date)
        # Note: These reads happen in the main Tkinter thread, not ideal for performance
        # but simpler for now. Could be optimized by passing data via queue.
        try: current_temps = self.temp_manager.read_all_temperatures()
        except Exception: current_temps = {}
        try: current_lights = self.light_manager.read_all_sensors()
        except Exception: current_lights = {}

        # Iterate through stored labels
        for item_id, data in self.status_labels.items():
             # Check if the value label widget still exists
             if 'label_value' in data:
                 try:
                     label_widget = data['label_value']
                     if label_widget.winfo_exists():
                         if data['type'] == 'sensor':
                             value, unit = None, ""
                             is_temp = item_id in current_temps
                             # Use hex ID for light sensors
                             is_light = item_id in [hex(addr) for addr in current_lights.keys()]

                             if is_temp:
                                 value, unit = current_temps.get(item_id), "°C"
                             elif is_light:
                                 value, unit = current_lights.get(item_id), " Lux" # Use hex ID

                             # Update label text
                             label_widget.config(text=f"{value:.1f}{unit}" if value is not None and unit != " Lux" else f"{value:.0f}{unit}" if value is not None and unit == " Lux" else "Err/NA")

                         elif data['type'] == 'outlet':
                             # Update ON/OFF state based on self.live_kasa_states
                             state_str = self._get_shared_kasa_state(data['mac'], data['index'])
                             label_widget.config(text=state_str)
                 except tk.TclError:
                     # Widget might have been destroyed (e.g., rule/device deleted)
                     # logging.warning(f"TclError updating status label for {item_id}") # Reduce noise
                     pass
                 except KeyError as e:
                     logging.warning(f"KeyError updating status label for {item_id}: {e}")


    def _get_shared_kasa_state(self, mac, index):
        """Récupère l'état (ON/OFF/Inconnu) d'une prise depuis la variable partagée."""
        try:
            is_on = self.live_kasa_states[mac][index]
            return "ON" if is_on else "OFF"
        except (AttributeError, KeyError, TypeError):
            # If MAC or index doesn't exist, or live_kasa_states not initialized
            return "Inconnu"

    # --- Gestion des Logs ---
    def update_log_display(self):
        """Vérifie la queue de logs et affiche les nouveaux messages dans la zone de texte."""
        while True:
            try:
                record = self.log_queue.get_nowait()
            except queue.Empty:
                break
            else:
                # Temporarily enable text widget, insert, disable, scroll
                try:
                    self.log_display.config(state=tk.NORMAL)
                    self.log_display.insert(tk.END, record + '\n')
                    self.log_display.config(state=tk.DISABLED)
                    self.log_display.see(tk.END)
                except tk.TclError:
                     # Handle cases where the widget might be destroyed during shutdown
                     logging.warning("Log display widget no longer available.")
                     break # Stop trying to update if widget is gone
        # Schedule the next check
        self.root.after(100, self.update_log_display)

    # --- Démarrage / Arrêt du Monitoring ---
    def start_monitoring(self):
        """Démarre le thread de monitoring et met à jour l'état de l'UI."""
        if self.monitoring_active:
            logging.warning("Tentative de démarrage du monitoring alors qu'il est déjà actif.")
            return

        logging.info("Démarrage du monitoring des règles...")
        self.monitoring_active = True

        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self._set_rules_ui_state(tk.DISABLED) # Disable rule editing controls

        self.live_kasa_states = {} # Reset known Kasa states

        self.monitoring_thread = threading.Thread(target=self._run_monitoring_loop, name="MonitoringThread", daemon=True)
        self.monitoring_thread.start()

        self.schedule_periodic_updates() # Start periodic UI updates
        logging.info("Monitoring démarré.")

    def stop_monitoring(self):
        """Arrête le thread de monitoring, met à jour l'UI et éteint les prises."""
        if not self.monitoring_active:
            logging.warning("Tentative d'arrêt du monitoring alors qu'il n'est pas actif.")
            return

        logging.info("Arrêt du monitoring des règles...")
        self.monitoring_active = False # Signal the thread to stop

        self.cancel_periodic_updates() # Stop scheduled UI updates

        # Wait for the monitoring thread to finish (with timeout)
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            logging.info("Attente de la fin du thread de monitoring (max 5 secondes)...")
            self.monitoring_thread.join(timeout=5.0)
            if self.monitoring_thread.is_alive():
                logging.warning("Le thread de monitoring n'a pas pu être arrêté dans le délai imparti.")
            else:
                logging.info("Thread de monitoring terminé proprement.")
        self.monitoring_thread = None
        self.asyncio_loop = None # Reset asyncio loop reference

        # Update button states (use 'after' for thread safety)
        self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))

        # Re-enable rule editing controls (use 'after' for thread safety)
        self.root.after(0, lambda: self._set_rules_ui_state(tk.NORMAL))

        # Initiate safe shutdown of Kasa outlets in the background
        logging.info("Lancement de l'extinction de sécurité des prises Kasa...")
        threading.Thread(target=self._turn_off_all_kasa_safely, name="ShutdownKasaThread", daemon=True).start()

        logging.info("Processus d'arrêt du monitoring terminé.")


    def _set_rules_ui_state(self, state):
        """Active ou désactive les widgets d'édition des règles."""
        logging.debug(f"Changement de l'état des widgets de règles à: {state}")
        new_combo_state = 'readonly' if state == tk.NORMAL else tk.DISABLED

        # --- Add Rule Button ---
        try:
            # Find the button by iterating through main_frame children
            main_frame = self.root.winfo_children()[0] # Assumes main_frame is the first child
            add_rule_btn = next(w for w in main_frame.winfo_children() if isinstance(w, ttk.Button) and "Ajouter une Règle" in w.cget("text"))
            if add_rule_btn.winfo_exists():
                add_rule_btn.config(state=state)
        except (IndexError, StopIteration, tk.TclError) as e:
             logging.warning(f"Impossible de trouver/configurer le bouton 'Ajouter une Règle': {e}")


        # --- Widgets within each displayed rule ---
        # Iterate over a copy of keys in case a rule is deleted while iterating
        for rule_id in list(self.rule_widgets.keys()):
             if rule_id not in self.rule_widgets: continue # Rule was deleted

             data = self.rule_widgets[rule_id]
             widgets = data.get('widgets', {})
             rule_frame = data.get('frame')

             # Check if the rule frame still exists
             if not rule_frame or not rule_frame.winfo_exists():
                 # Clean up widget entry if frame is gone
                 if rule_id in self.rule_widgets:
                     del self.rule_widgets[rule_id]
                 continue

             # --- Delete Rule Button (❌) ---
             try:
                 # Find the delete button within the name_frame (assuming structure)
                 name_frame = rule_frame.winfo_children()[0] # First child is name_frame
                 del_btn = next(w for w in name_frame.winfo_children() if isinstance(w, ttk.Button) and w.cget('text') == "❌")
                 if del_btn.winfo_exists():
                     del_btn.config(state=state)
             except (IndexError, StopIteration, tk.TclError) as e:
                 logging.warning(f"Impossible de trouver/configurer le bouton Supprimer pour règle {rule_id}: {e}")


             # --- Edit Buttons (Name, SI, UNTIL) ---
             for btn_key in ['edit_name_button', 'edit_si_button', 'edit_until_button']:
                 if btn_key in widgets:
                     try:
                         button_widget = widgets[btn_key]
                         if button_widget.winfo_exists():
                             button_widget.config(state=state)
                     except tk.TclError: pass # Ignore if destroyed
                     except KeyError: pass # Ignore if key somehow missing

             # --- THEN Widgets (Kasa, Outlet, Action Combos) ---
             for w_key in ['kasa_combo', 'outlet_combo', 'action_combo']:
                  if w_key in widgets:
                      try:
                          combo_widget = widgets[w_key]
                          if combo_widget.winfo_exists():
                              combo_widget.config(state=new_combo_state)
                      except tk.TclError: pass # Ignore if destroyed
                      except KeyError: pass # Ignore if key somehow missing


    # --- Monitoring Loop Logic ---
    def _run_monitoring_loop(self):
        """Point d'entrée pour le thread de monitoring, gère la boucle asyncio."""
        try:
            try:
                self.asyncio_loop = asyncio.get_event_loop()
            except RuntimeError:
                self.asyncio_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.asyncio_loop)

            logging.info("Boucle d'événements asyncio démarrée pour le monitoring.")
            # Run the main monitoring task within the asyncio loop
            self.asyncio_loop.run_until_complete(self._async_monitoring_task())

        except Exception as e:
            logging.critical(f"Erreur fatale dans la boucle de monitoring asyncio: {e}", exc_info=True)
        finally:
            logging.info("Boucle de monitoring asyncio terminée.")
            # If monitoring is still marked as active (e.g., due to error), trigger stop
            if self.monitoring_active:
                logging.warning("Arrêt du monitoring déclenché suite à la fin anormale de la boucle asyncio.")
                # Schedule stop_monitoring call in the main Tkinter thread
                self.root.after(0, self.stop_monitoring)

    async def _update_live_kasa_states_task(self):
        """Tâche asynchrone pour lire l'état actuel de toutes les prises Kasa."""
        # logging.debug("[MONITORING] Début màj états Kasa live...") # Reduce log noise
        new_states = {} # {mac: {index: bool}}

        tasks = []
        for mac, device_data in self.kasa_devices.items():
             # Check if it's a controllable device
             if device_data['info'].get('is_strip') or device_data['info'].get('is_plug'):
                 tasks.append(self._fetch_one_kasa_state(mac, device_data['controller']))

        if not tasks:
            # logging.debug("[MONITORING] Aucun appareil Kasa contrôlable trouvé pour màj état.") # Reduce log noise
            self.live_kasa_states = {} # Clear state if no devices
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)

        successful_reads = 0
        for res in results:
            if isinstance(res, Exception):
                logging.error(f"[MONITORING] Erreur lecture état Kasa: {res}")
            elif isinstance(res, dict) and res: # Check it's a non-empty dict
                new_states.update(res) # Merge the read states {mac: {index: bool}}
                successful_reads += 1

        # Update the shared state
        self.live_kasa_states = new_states
        # logging.debug(f"[MONITORING] États Kasa live màj: {successful_reads}/{len(tasks)} appareils lus OK.") # Reduce log noise


    async def _fetch_one_kasa_state(self, mac, controller):
        """Tâche asynchrone pour lire l'état des prises d'un seul appareil Kasa."""
        try:
            # Ensure connection (may involve reconnection if needed)
            await controller._connect() # Note: Using "private" method

            # Check if connection/update succeeded
            if controller._device: # Access "private" attribute
                outlet_states = await controller.get_outlet_state()
                if outlet_states is not None:
                    # Convert list of dicts to dict {index: is_on}
                    states_dict = {
                        outlet['index']: outlet['is_on']
                        for outlet in outlet_states
                        if 'index' in outlet and 'is_on' in outlet
                    }
                    return {mac: states_dict}
                else:
                    logging.warning(f"[MONITORING] État Kasa None pour {self.get_alias('device', mac)} ({mac}).")
            else:
                logging.warning(f"[MONITORING] Échec connexion/màj Kasa pour {self.get_alias('device', mac)} ({mac}).")
        except Exception as e:
            # Log error but allow gather to continue
            logging.error(f"[MONITORING] Erreur fetch état Kasa {self.get_alias('device', mac)} ({mac}): {e}")
            # raise e # Re-raising stops the gather, better to just return empty
        return {} # Return empty dict on failure


    # --- Logique d'Évaluation des Règles (Coeur du Monitoring) ---
    async def _async_monitoring_task(self):
        """Tâche asynchrone principale qui évalue les règles et contrôle les prises."""
        # Store more info for active rules: original action needed to maintain state
        active_until_rules = {} # {rule_id: {'revert_action': 'ON'/'OFF', 'original_action': 'ON'/'OFF'}}
        last_kasa_update = datetime.min
        kasa_update_interval = timedelta(seconds=10) # Check Kasa state every 10 seconds

        logging.info("Début de la boucle de monitoring principale.")

        while self.monitoring_active:
            now_dt = datetime.now()
            now_time = now_dt.time()
            # logging.debug(f"--- Cycle Mon {now_dt:%Y-%m-%d %H:%M:%S} ---") # Reduce log noise

            # --- 1. Read Sensors ---
            current_sensor_values = {}
            try:
                # Use run_in_executor for potentially blocking I/O
                temp_values = await self.asyncio_loop.run_in_executor(None, self.temp_manager.read_all_temperatures)
                light_values = await self.asyncio_loop.run_in_executor(None, self.light_manager.read_all_sensors)
                # Combine and filter out None values, use hex for light sensor keys
                current_sensor_values = {k: v for k, v in temp_values.items() if v is not None}
                current_sensor_values.update({hex(k): v for k, v in light_values.items() if v is not None})

                # logging.debug(f"[MONITORING] Valeurs capteurs lues: {current_sensor_values}") # Reduce log noise
            except Exception as e:
                logging.error(f"[MONITORING] Erreur lecture capteurs: {e}")

            # --- 2. Update Kasa States ---
            if now_dt - last_kasa_update >= kasa_update_interval:
                try:
                    # logging.debug(f"[MONITORING] États Kasa avant màj: {self.live_kasa_states}") # Reduce noise
                    await self._update_live_kasa_states_task()
                    last_kasa_update = now_dt
                    # logging.debug(f"[MONITORING] États Kasa après màj: {self.live_kasa_states}") # Reduce noise
                except Exception as e:
                    logging.error(f"[MONITORING] Échec màj Kasa: {e}")

            # --- 3. Evaluate Rules ---
            desired_outlet_states = {} # { (mac, index): 'ON'/'OFF' } - Reset each cycle
            rules_to_evaluate = list(self.rules) # Make a copy
            active_until_copy = dict(active_until_rules) # Copy for safe iteration

            # --- 3a. Evaluate active UNTIL conditions ---
            # logging.debug(f"[MONITORING] Éval UNTIL - Règles actives: {list(active_until_copy.keys())}") # Reduce noise
            for rule_id, until_info in active_until_copy.items():
                rule = next((r for r in rules_to_evaluate if r.get('id') == rule_id), None)
                if not rule:
                    logging.warning(f"[MONITORING] R{rule_id} (UNTIL): Règle non trouvée. Annulation.")
                    if rule_id in active_until_rules: del active_until_rules[rule_id]
                    continue

                mac = rule.get('target_device_mac')
                idx = rule.get('target_outlet_index')
                if mac is None or idx is None:
                    logging.warning(f"[MONITORING] R{rule_id} (UNTIL): Cible invalide. Annulation.")
                    if rule_id in active_until_rules: del active_until_rules[rule_id]
                    continue

                outlet_key = (mac, idx)
                until_logic = rule.get('until_logic', 'OU')
                until_conditions = rule.get('until_conditions', [])

                if not until_conditions: # Should not happen if rule entered active_until
                    logging.debug(f"[MONITORING] R{rule_id} (UNTIL): Aucune condition. Désactivation.")
                    if rule_id in active_until_rules: del active_until_rules[rule_id]
                    continue

                # Check the UNTIL condition using the helper function
                until_condition_met = self._evaluate_logic_group(until_conditions, until_logic, current_sensor_values, now_time, rule_id, "UNTIL")

                if until_condition_met:
                    revert_action = until_info['revert_action']
                    logging.info(f"[MONITORING] R{rule_id} ({self.get_alias('rule', rule_id)}): Condition JUSQU'À ({until_logic}) REMPLIE. Action retour: {revert_action}.")
                    # Set desired state to revert action, potentially overriding SI from this cycle
                    desired_outlet_states[outlet_key] = revert_action
                    if rule_id in active_until_rules: # Remove from active list
                        del active_until_rules[rule_id]
                # else: UNTIL condition not met, rule remains active, state will be handled in 3c

            # --- 3b. Evaluate SI conditions ---
            # logging.debug(f"[MONITORING] Éval SI - Règles à évaluer: {len(rules_to_evaluate)}") # Reduce noise
            for rule in rules_to_evaluate:
                rule_id = rule.get('id')
                mac = rule.get('target_device_mac')
                idx = rule.get('target_outlet_index')
                action = rule.get('action')

                if not rule_id or mac is None or idx is None or not action:
                    continue # Skip invalid rules

                outlet_key = (mac, idx)

                # Skip SI evaluation if the rule is currently waiting for UNTIL
                if rule_id in active_until_rules:
                    # logging.debug(f"[MONITORING] R{rule_id}: Éval SI skip (règle en attente UNTIL).") # Reduce noise
                    continue

                # Skip SI evaluation if the state was already set by an UNTIL condition *this cycle*
                if outlet_key in desired_outlet_states:
                     # logging.debug(f"[MONITORING] R{rule_id}: Éval SI skip (état déjà fixé par UNTIL pour {outlet_key} ce cycle).") # Reduce noise
                     continue

                trigger_logic = rule.get('trigger_logic', 'ET')
                trigger_conditions = rule.get('conditions', [])

                if not trigger_conditions:
                    continue # Skip rules without trigger conditions

                # Check the SI condition using the helper function
                trigger_condition_met = self._evaluate_logic_group(trigger_conditions, trigger_logic, current_sensor_values, now_time, rule_id, "SI")

                if trigger_condition_met:
                    logging.info(f"[MONITORING] R{rule_id} ({self.get_alias('rule', rule_id)}): Condition SI ({trigger_logic}) REMPLIE. Action désirée: {action}.")
                    # Set desired state ONLY if not already set by UNTIL this cycle (already checked above)
                    desired_outlet_states[outlet_key] = action

                    # Check if this rule has an UNTIL condition to activate
                    if rule.get('until_conditions'):
                        revert_action = 'OFF' if action == 'ON' else 'ON'
                        logging.info(f"[MONITORING] R{rule_id} ({self.get_alias('rule', rule_id)}): Activation JUSQU'À ({rule.get('until_logic','OU')}). Action retour: {revert_action}.")
                        # Store both original action and revert action
                        active_until_rules[rule_id] = {'revert_action': revert_action, 'original_action': action}

            # --- 3c. Maintain state for active rules (UNTIL not met) ---
            # logging.debug(f"[MONITORING] Maintien états actifs - Règles: {list(active_until_rules.keys())}") # Reduce noise
            for rule_id, until_info in active_until_rules.items():
                 rule = next((r for r in rules_to_evaluate if r.get('id') == rule_id), None)
                 if not rule: continue

                 mac = rule.get('target_device_mac')
                 idx = rule.get('target_outlet_index')
                 if mac is None or idx is None: continue

                 outlet_key = (mac, idx)
                 original_action = until_info['original_action']

                 # If the state wasn't set by its own UNTIL condition being met this cycle,
                 # maintain the original action state. This prevents the implicit OFF.
                 if outlet_key not in desired_outlet_states:
                     # logging.debug(f"[MONITORING] R{rule_id}: Maintien état actif {original_action} pour {outlet_key}") # Reduce noise
                     desired_outlet_states[outlet_key] = original_action
                 # else: State was already set (likely by its UNTIL being met), do nothing here.


            # --- 4. Determine Kasa Actions Needed ---
            # logging.debug(f"[MONITORING] États Kasa désirés finaux pour ce cycle: {desired_outlet_states}") # Reduce noise
            tasks_to_run = []
            actions_log = [] # For summary logging

            # Determine all outlets managed by ANY rule
            all_managed_outlets = set(
                (r.get('target_device_mac'), r.get('target_outlet_index'))
                for r in rules_to_evaluate
                if r.get('target_device_mac') is not None and r.get('target_outlet_index') is not None
            )
            # logging.debug(f"[MONITORING] Prises gérées par les règles: {all_managed_outlets}") # Reduce noise

            # Iterate through all *managed* outlets to determine necessary actions
            for mac, idx in all_managed_outlets:
                outlet_key = (mac, idx)
                desired_state = desired_outlet_states.get(outlet_key) # 'ON', 'OFF', or None
                current_live_state = self.live_kasa_states.get(mac, {}).get(idx) # True, False, or None

                action_needed = False
                kasa_function_name = None
                target_state_bool = None # For optimistic update
                log_state_change = ""

                if desired_state == 'ON' and current_live_state is not True:
                    action_needed = True
                    kasa_function_name = 'turn_outlet_on'
                    target_state_bool = True
                    log_state_change = f"{self.get_alias('outlet', mac, idx)} -> ON (était {current_live_state})"
                elif desired_state == 'OFF' and current_live_state is not False:
                    action_needed = True
                    kasa_function_name = 'turn_outlet_off'
                    target_state_bool = False
                    log_state_change = f"{self.get_alias('outlet', mac, idx)} -> OFF (était {current_live_state})"
                elif desired_state is None and current_live_state is True:
                     # Implicit OFF: No rule wants it ON or OFF, but it's currently ON
                    action_needed = True
                    kasa_function_name = 'turn_outlet_off'
                    target_state_bool = False
                    log_state_change = f"{self.get_alias('outlet', mac, idx)} -> OFF (Implicite, était ON)"


                if action_needed:
                    if mac in self.kasa_devices:
                        controller = self.kasa_devices[mac]['controller']
                        actions_log.append(log_state_change) # Add to summary log
                        tasks_to_run.append(getattr(controller, kasa_function_name)(idx))
                        # Optimistic update of live state immediately
                        self.live_kasa_states.setdefault(mac, {})[idx] = target_state_bool
                    else:
                        logging.error(f"[ACTION KASA] Erreur: Appareil Kasa {mac} non trouvé pour action.")


            # --- 5. Execute Kasa Tasks ---
            if tasks_to_run:
                logging.info(f"[ACTION KASA] Actions: {'; '.join(actions_log)}") # Log summary of actions
                # logging.debug(f"[MONITORING] Exécution de {len(tasks_to_run)} tâches Kasa...") # Reduce noise
                try:
                    results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                    for i, res in enumerate(results):
                        if isinstance(res, Exception):
                            # Log error, associating with action is complex here
                            logging.error(f"[MONITORING] Erreur tâche Kasa (index {i}): {res}")
                except Exception as e_gather:
                    logging.error(f"[MONITORING] Erreur gather Kasa: {e_gather}")
                # logging.debug("[MONITORING] Tâches Kasa du cycle terminées.") # Reduce noise

            # --- 6. Wait before next cycle ---
            await asyncio.sleep(2) # Wait 2 seconds

        logging.info("Sortie de la boucle de monitoring principale.")

    # --- Helper function to evaluate a list of conditions based on logic (ET/OU) ---
    def _evaluate_logic_group(self, conditions, logic, current_sensor_values, current_time_obj, rule_id_log, group_type_log):
        """Evaluates a list of conditions based on ET/OU logic."""
        if not conditions: return False # Empty group is never True

        results = [self._check_condition(cond, current_sensor_values, current_time_obj) for cond in conditions]

        if logic == 'ET':
            final_result = all(results)
            # if not final_result: logging.debug(f"[MONITORING] R{rule_id_log} {group_type_log}(ET) ÉCHOUE. Résultats individuels: {results}") # Reduce noise
            return final_result
        elif logic == 'OU':
             final_result = any(results)
             # if final_result: logging.debug(f"[MONITORING] R{rule_id_log} {group_type_log}(OU) RÉUSSIT. Résultats individuels: {results}") # Reduce noise
             return final_result
        else:
            logging.error(f"[MONITORING] R{rule_id_log}: Logique {group_type_log} inconnue '{logic}'.")
            return False


    # --- Condition Checking Function ---
    def _check_condition(self, condition_data, current_sensor_values, current_time_obj):
        """Évalue une condition unique (Capteur ou Heure)."""
        cond_type = condition_data.get('type')
        operator = condition_data.get('operator')
        cond_id_log = condition_data.get('condition_id', 'N/A')

        if not cond_type or not operator:
            logging.warning(f"[COND CHECK] Cond invalide (ID:{cond_id_log}): manque type/op - {condition_data}")
            return False

        try:
            if cond_type == 'Capteur':
                sensor_id = condition_data.get('id')
                threshold = condition_data.get('threshold')

                if sensor_id is None or threshold is None or operator not in SENSOR_OPERATORS:
                    logging.warning(f"[COND CHECK] Cond Capteur invalide (ID:{cond_id_log}): {condition_data}")
                    return False

                # Use the correct key (might be hex for light sensors)
                current_value = current_sensor_values.get(sensor_id)

                if current_value is None:
                    # logging.debug(f"[COND CHECK] (ID:{cond_id_log}): Valeur manquante pour capteur {self.get_alias('sensor', sensor_id)} ({sensor_id})") # Reduce noise
                    return False # Cannot evaluate if sensor value is missing

                # logging.debug(f"[COND CHECK] Eval Capteur (ID:{cond_id_log}): '{self.get_alias('sensor', sensor_id)}' ({current_value}) {operator} {threshold} ?") # Reduce noise
                result = self._compare(current_value, operator, float(threshold))
                # logging.debug(f"[COND CHECK] -> Résultat (ID:{cond_id_log}): {result}") # Reduce noise
                return result

            elif cond_type == 'Heure':
                time_str = condition_data.get('value')

                if not time_str or operator not in TIME_OPERATORS:
                    logging.warning(f"[COND CHECK] Cond Heure invalide (ID:{cond_id_log}): {condition_data}")
                    return False
                try:
                    target_time = datetime.strptime(time_str, '%H:%M').time()
                except ValueError:
                    logging.error(f"[COND CHECK] Format heure invalide (ID:{cond_id_log}): '{time_str}'")
                    return False

                # logging.debug(f"[COND CHECK] Eval Heure (ID:{cond_id_log}): {current_time_obj:%H:%M:%S} {operator} {target_time:%H:%M} ?") # Reduce noise
                if operator == '<': result = current_time_obj < target_time
                elif operator == '>': result = current_time_obj > target_time
                elif operator == '<=': result = current_time_obj <= target_time
                elif operator == '>=': result = current_time_obj >= target_time
                else: # Compare only hour and minute for '=' and '!='
                    current_minutes = current_time_obj.hour * 60 + current_time_obj.minute
                    target_minutes = target_time.hour * 60 + target_time.minute
                    if operator == '=': result = current_minutes == target_minutes
                    elif operator == '!=': result = current_minutes != target_minutes
                    else: result = False

                # logging.debug(f"[COND CHECK] -> Résultat (ID:{cond_id_log}): {result}") # Reduce noise
                return result
            else:
                logging.error(f"[COND CHECK] Type cond inconnu (ID:{cond_id_log}): {cond_type}")
                return False
        except ValueError as e:
            logging.error(f"[COND CHECK] Erreur valeur (ID:{cond_id_log}) - {condition_data}: {e}")
            return False
        except Exception as e:
            logging.error(f"[COND CHECK] Erreur eval cond (ID:{cond_id_log}) - {condition_data}: {e}", exc_info=True)
            return False

    # --- Numeric Comparison Function ---
    def _compare(self, value1, operator, value2):
        """Effectue une comparaison numérique entre deux valeurs."""
        try:
            v1 = float(value1)
            v2 = float(value2)
            if operator == '<': return v1 < v2
            elif operator == '>': return v1 > v2
            elif operator == '=': return abs(v1 - v2) < 1e-9 # Float equality tolerance
            elif operator == '!=': return abs(v1 - v2) >= 1e-9
            elif operator == '<=': return v1 <= v2
            elif operator == '>=': return v1 >= v2
            else:
                logging.warning(f"Opérateur comparaison numérique inconnu: {operator}")
                return False
        except (ValueError, TypeError) as e:
            logging.error(f"Erreur comp num: impossible de convertir '{value1}' ou '{value2}'. Op: {operator}. Err: {e}")
            return False

    # --- Fonctions d'Extinction / Sauvegarde / Fermeture ---
    def _turn_off_all_kasa_safely(self):
        """Lance l'extinction de toutes les prises Kasa dans une boucle asyncio."""
        logging.info("Tentative d'extinction sécurisée de toutes les prises Kasa...")
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                     # If a loop is running, run in threadsafe manner
                     future = asyncio.run_coroutine_threadsafe(self._async_turn_off_all(), loop)
                     future.result(timeout=15) # Wait with timeout
                else:
                     # If no loop is running, run until complete
                     loop.run_until_complete(self._async_turn_off_all())
            except RuntimeError:
                # If get_event_loop fails, use asyncio.run
                logging.info("Aucune boucle asyncio existante, utilisation de asyncio.run pour l'extinction.")
                asyncio.run(self._async_turn_off_all())
        except asyncio.TimeoutError:
             logging.error("Timeout dépassé lors de l'attente de l'extinction des prises Kasa.")
        except Exception as e:
            logging.error(f"Erreur inattendue lors de l'extinction sécurisée des prises Kasa: {e}", exc_info=True)

    async def _async_turn_off_all(self):
        """Tâche asynchrone pour éteindre toutes les prises de tous les appareils Kasa connus."""
        tasks = {}
        logging.info(f"Préparation des tâches d'extinction pour {len(self.kasa_devices)} appareils Kasa...")

        for mac, device_data in self.kasa_devices.items():
            controller = device_data['controller']
            device_alias = self.get_alias('device', mac)
            task_key = f"{device_alias} ({mac})"

            if device_data['info'].get('is_strip') or device_data['info'].get('is_plug'):
                # logging.debug(f"Ajout tâche extinction pour: {task_key}") # Reduce noise
                tasks[task_key] = controller.turn_all_outlets_off()
            else:
                 # Add a dummy task for non-controllable devices
                 tasks[task_key] = asyncio.sleep(0)

        if tasks:
            logging.info(f"Exécution de {len(tasks)} tâches d'extinction Kasa en parallèle...")
            task_keys = list(tasks.keys())
            task_coroutines = list(tasks.values())
            results = await asyncio.gather(*task_coroutines, return_exceptions=True)

            success_count = 0
            failure_count = 0
            for i, result in enumerate(results):
                key = task_keys[i]
                if isinstance(result, Exception):
                    logging.error(f"Erreur lors de l'extinction de '{key}': {result}")
                    failure_count += 1
                else:
                    # Check if it was a real turn_off task or just sleep(0)
                    original_coro = task_coroutines[i]
                    is_sleep_task = asyncio.iscoroutine(original_coro) and getattr(original_coro, '__name__', '') == 'sleep'
                    # if not is_sleep_task: logging.debug(f"Extinction réussie pour '{key}'.") # Optional DEBUG Log
                    success_count += 1

            logging.info(f"Extinction Kasa terminée. Tâches complétées: {success_count}, Échecs: {failure_count}.")
        else:
            logging.info("Aucun appareil Kasa de type prise/multiprise trouvé à éteindre.")

    def save_configuration(self):
        """Sauvegarde la configuration actuelle (alias et règles) dans le fichier YAML."""
        logging.info("Préparation de la sauvegarde de la configuration...")

        # Ensure current UI selections are reflected in self.rules before saving
        for rule_id in list(self.rule_widgets.keys()):
             if rule_id in self.rule_widgets: # Check if rule still exists in UI
                 try:
                     # This updates the rule data based on combobox selections
                     self.on_rule_change(rule_id)
                 except Exception as e:
                     logging.error(f"Erreur on_rule_change avant save pour règle {rule_id}: {e}")

        config_to_save = {
            "aliases": self.aliases,
            "rules": self.rules # self.rules should now be up-to-date
        }
        # logging.debug(f"Données préparées pour la sauvegarde: {config_to_save}") # Reduce noise

        if save_config(config_to_save, DEFAULT_CONFIG_FILE):
            logging.info(f"Configuration sauvegardée avec succès dans {DEFAULT_CONFIG_FILE}.")
            messagebox.showinfo("Sauvegarde", "Configuration sauvegardée avec succès.", parent=self.root)
        else:
            # Error logged within save_config
            messagebox.showerror("Sauvegarde Échouée", "Une erreur est survenue lors de la sauvegarde. Vérifiez les logs.", parent=self.root)

    def on_closing(self):
        """Gère l'événement de fermeture de la fenêtre principale."""
        close_app = False
        if self.monitoring_active:
            if messagebox.askyesno("Quitter l'Application",
                                   "Le monitoring est actif.\n\nVoulez-vous arrêter et quitter ?",
                                   parent=self.root):
                logging.info("Arrêt monitoring & fermeture demandés...")
                self.stop_monitoring()
                # Allow time for stop_monitoring tasks (like Kasa shutdown) to initiate
                logging.info("Fermeture app dans 1 sec...")
                self.root.after(1000, self.root.destroy) # Schedule destroy after delay
            else:
                logging.debug("Fermeture annulée (monitoring actif).")
                # Do not close
        else:
            if messagebox.askyesno("Quitter l'Application",
                                   "Êtes-vous sûr de vouloir quitter ?",
                                   parent=self.root):
                logging.info("Fermeture demandée (monitoring inactif)...")
                # Attempt safe shutdown even if monitoring wasn't active
                logging.info("Lancement extinction Kasa (sécurité)...")
                threading.Thread(target=self._turn_off_all_kasa_safely, daemon=True).start()
                logging.info("Fermeture app dans 1 sec...")
                self.root.after(1000, self.root.destroy) # Schedule destroy after delay
            else:
                logging.debug("Fermeture annulée (monitoring inactif).")
                # Do not close

# --- Point d'Entrée Principal ---
if __name__ == "__main__":
    # Configure basic logging FIRST
    log_format = '%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'
    logging.basicConfig(level=logging.DEBUG, format=log_format, datefmt=date_format) # Set level to DEBUG

    log_queue_main = queue.Queue()

    # --- Setup logging to use the queue ---
    # Get the root logger
    logger = logging.getLogger()
    # Remove existing handlers if any (optional, prevents duplicate logs if basicConfig was called before)
    # for handler in logger.handlers[:]:
    #    logger.removeHandler(handler)

    # Add the queue handler
    queue_handler = logging.handlers.QueueHandler(log_queue_main)
    logger.addHandler(queue_handler)
    # Set the root logger level (redundant if basicConfig already set it, but safe)
    logger.setLevel(logging.DEBUG)

    # --- Start Tkinter App ---
    root = tk.Tk()
    # Pass the queue to the app instance (modify __init__ if it needs it directly)
    # GreenhouseApp now uses the root logger which has the QueueHandler
    app = GreenhouseApp(root)

    # Start the main loop
    root.mainloop()

