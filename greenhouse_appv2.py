# greenhouse_appv2.py
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
# CLASSE POUR L'ÉDITEUR DE CONDITIONS (POP-UP)
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

        self.condition_lines = [] # Liste de dict: {'frame': ttk.Frame, 'widgets': dict, 'condition_id': str}
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

        # --- Zone Scrollable pour les Conditions ---
        conditions_container = ttk.Frame(dialog_frame)
        conditions_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Canvas pour contenir le frame scrollable
        self.conditions_canvas = tk.Canvas(conditions_container, borderwidth=0, highlightthickness=0)
        # Scrollbar verticale liée au canvas
        scrollbar = ttk.Scrollbar(conditions_container, orient="vertical", command=self.conditions_canvas.yview)
        # Frame interne qui contiendra les lignes de conditions
        self.scrollable_conditions_frame = ttk.Frame(self.conditions_canvas)

        # Quand le frame interne change de taille, on met à jour la scrollregion du canvas
        self.scrollable_conditions_frame.bind("<Configure>", self._on_frame_configure)

        # Placer le frame interne dans le canvas
        self.canvas_window = self.conditions_canvas.create_window((0, 0), window=self.scrollable_conditions_frame, anchor="nw")
        # Configurer le canvas pour utiliser la scrollbar
        self.conditions_canvas.configure(yscrollcommand=scrollbar.set)

        # Empaqueter le canvas et la scrollbar
        self.conditions_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Lier la molette de la souris au canvas pour le défilement
        # Utiliser bind (pas bind_all) pour éviter des conflits potentiels
        self.conditions_canvas.bind("<MouseWheel>", self._on_mousewheel) # Windows
        self.conditions_canvas.bind("<Button-4>", self._on_mousewheel) # Linux scroll up
        self.conditions_canvas.bind("<Button-5>", self._on_mousewheel) # Linux scroll down

        # --- Peupler les conditions initiales ---
        if not self.initial_conditions:
             # S'il n'y a pas de condition initiale, ajouter une ligne vide
             self._add_condition_line()
        else:
            # Sinon, ajouter une ligne pour chaque condition existante
            for condition_data in self.initial_conditions:
                self._add_condition_line(condition_data)

        # --- Bouton Ajouter Condition ---
        add_button_frame = ttk.Frame(dialog_frame)
        add_button_frame.pack(side=tk.TOP, fill=tk.X, pady=(10, 0))
        add_button = ttk.Button(add_button_frame, text="➕ Ajouter Condition", command=self._add_condition_line)
        add_button.pack()

        # Ajuster la taille initiale du pop-up et le rendre redimensionnable
        self.geometry("750x450")
        self.resizable(True, True)

        self._update_scrollregion() # Mise à jour initiale de la scrollregion

        return self.logic_combo # Mettre le focus initial sur le combobox de logique

    def _on_frame_configure(self, event=None):
        """Met à jour la scrollregion du canvas quand le frame interne change de taille."""
        # bbox("all") renvoie les dimensions actuelles de tout le contenu du canvas
        self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))

    def _on_mousewheel(self, event):
        """Gère le défilement avec la molette de la souris."""
        delta = 0
        # Déterminer la direction du scroll selon le système d'exploitation
        if event.num == 5: # Linux scroll down
            delta = 1
        elif event.num == 4: # Linux scroll up
            delta = -1
        elif hasattr(event, 'delta'): # Windows
             if event.delta < 0: # Windows scroll down
                 delta = 1
             elif event.delta > 0: # Windows scroll up
                 delta = -1

        if delta != 0:
            # Faire défiler le canvas verticalement
            self.conditions_canvas.yview_scroll(delta, "units")
            # Empêcher l'événement de se propager (optionnel, évite le scroll de la fenêtre principale)
            return "break"

    def _update_scrollregion(self):
        """Force la mise à jour de la scrollregion du canvas."""
        # S'assurer que Tkinter a traité les changements de taille avant de calculer la bbox
        self.scrollable_conditions_frame.update_idletasks()
        self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))

    def _add_condition_line(self, condition_data=None):
        """Ajoute une ligne de widgets (une condition) dans le frame scrollable."""
        line_frame = ttk.Frame(self.scrollable_conditions_frame, padding=2)
        line_frame.pack(fill=tk.X, expand=True, pady=1)

        widgets = {} # Dictionnaire pour stocker les widgets de cette ligne
        # Générer un ID unique pour la condition (soit existant, soit nouveau)
        condition_id = condition_data.get('condition_id', f"new_{self.condition_id_counter}") if condition_data else f"new_{self.condition_id_counter}"
        self.condition_id_counter += 1

        # 1. Type de condition (Capteur/Heure)
        widgets['type_var'] = tk.StringVar()
        widgets['type_combo'] = ttk.Combobox(line_frame, textvariable=widgets['type_var'], values=CONDITION_TYPES, state="readonly", width=8)
        widgets['type_combo'].pack(side=tk.LEFT, padx=2)
        # Lier l'événement de sélection pour adapter l'UI
        widgets['type_combo'].bind('<<ComboboxSelected>>', lambda e, lw=widgets: self._on_condition_type_change(lw))

        # 2. Sélecteur de Capteur (activé seulement si Type='Capteur')
        widgets['sensor_var'] = tk.StringVar()
        # Trier les noms de capteurs pour l'affichage, ajouter une option vide
        sensor_names = [""] + sorted([name for name, _id in self.available_sensors])
        widgets['sensor_combo'] = ttk.Combobox(line_frame, textvariable=widgets['sensor_var'], values=sensor_names, state="disabled", width=20)
        widgets['sensor_combo'].pack(side=tk.LEFT, padx=2)

        # 3. Opérateur de comparaison (<, >, =, etc.)
        widgets['operator_var'] = tk.StringVar()
        widgets['operator_combo'] = ttk.Combobox(line_frame, textvariable=widgets['operator_var'], values=OPERATORS, state="readonly", width=4)
        widgets['operator_combo'].pack(side=tk.LEFT, padx=2)

        # 4. Valeur (seuil numérique ou heure HH:MM)
        widgets['value_var'] = tk.StringVar()
        widgets['value_entry'] = ttk.Entry(line_frame, textvariable=widgets['value_var'], width=10)
        widgets['value_entry'].pack(side=tk.LEFT, padx=2)

        # 5. Bouton Supprimer (-)
        delete_button = ttk.Button(line_frame, text="➖", width=2, style="Red.TButton",
                                   command=lambda frame=line_frame: self._delete_condition_line(frame))
        delete_button.pack(side=tk.RIGHT, padx=5)

        # Stocker les informations de la ligne
        line_info = {'frame': line_frame, 'widgets': widgets, 'condition_id': condition_id}
        self.condition_lines.append(line_info)

        # Si des données initiales sont fournies, peupler les widgets
        if condition_data:
            cond_type = condition_data.get('type')
            widgets['type_var'].set(cond_type if cond_type in CONDITION_TYPES else '')
            widgets['operator_var'].set(condition_data.get('operator', ''))

            if cond_type == 'Capteur':
                sensor_id = condition_data.get('id')
                # Utiliser l'alias du capteur s'il existe
                sensor_name = self.app.get_alias('sensor', sensor_id) if sensor_id else ''
                widgets['sensor_var'].set(sensor_name if sensor_name in sensor_names else "")
                widgets['value_var'].set(str(condition_data.get('threshold', '')))
            elif cond_type == 'Heure':
                widgets['value_var'].set(condition_data.get('value', '')) # Format HH:MM

            # Mettre à jour l'état des widgets (activer/désactiver, valeurs possibles)
            self._on_condition_type_change(widgets)
        else:
            # Si c'est une nouvelle ligne, initialiser avec le premier type
             widgets['type_var'].set(CONDITION_TYPES[0])
             self._on_condition_type_change(widgets)

        # Mettre à jour la scrollregion après ajout
        self._update_scrollregion()

    def _on_condition_type_change(self, line_widgets):
        """Adapte l'UI d'une ligne (activer/désactiver widgets, changer opérateurs) quand le type de condition change."""
        selected_type = line_widgets['type_var'].get()
        current_op = line_widgets['operator_var'].get() # Opérateur actuel

        if selected_type == 'Capteur':
            # Activer le sélecteur de capteur et l'entrée de valeur
            line_widgets['sensor_combo'].config(state="readonly")
            line_widgets['value_entry'].config(state="normal")
            # Définir les opérateurs valides pour les capteurs
            line_widgets['operator_combo'].config(values=SENSOR_OPERATORS)
            # Si l'opérateur actuel n'est pas valide, le réinitialiser
            if current_op not in SENSOR_OPERATORS: line_widgets['operator_var'].set('')
            # Si la valeur ressemble à une heure, la vider
            if ':' in line_widgets['value_var'].get(): line_widgets['value_var'].set('')
        elif selected_type == 'Heure':
            # Désactiver le sélecteur de capteur et vider sa valeur
            line_widgets['sensor_combo'].config(state="disabled"); line_widgets['sensor_var'].set("")
            # Activer l'entrée de valeur
            line_widgets['value_entry'].config(state="normal")
            # Définir les opérateurs valides pour l'heure
            line_widgets['operator_combo'].config(values=TIME_OPERATORS)
            # Si l'opérateur actuel n'est pas valide, le réinitialiser
            if current_op not in TIME_OPERATORS: line_widgets['operator_var'].set('')
            # Si la valeur ressemble à un nombre, la vider (pour forcer HH:MM)
            try: float(line_widgets['value_var'].get()); line_widgets['value_var'].set('')
            except ValueError: pass # Ignorer si ce n'est pas un nombre
        else: # Cas où aucun type n'est sélectionné (ne devrait pas arriver avec combobox readonly)
            line_widgets['sensor_combo'].config(state="disabled"); line_widgets['sensor_var'].set("")
            line_widgets['value_entry'].config(state="disabled"); line_widgets['value_var'].set("")
            line_widgets['operator_combo'].config(values=OPERATORS) # Revenir aux opérateurs génériques
            line_widgets['operator_var'].set('') # Réinitialiser l'opérateur

    def _delete_condition_line(self, line_frame_to_delete):
        """Supprime une ligne de condition de l'UI et de la liste interne."""
        index_to_delete = -1
        # Trouver l'index de la ligne à supprimer dans notre liste
        for i, line_info in enumerate(self.condition_lines):
            if line_info['frame'] == line_frame_to_delete:
                index_to_delete = i
                break

        if index_to_delete != -1:
            # Supprimer de la liste interne
            del self.condition_lines[index_to_delete]
            # Détruire le frame Tkinter associé
            line_frame_to_delete.destroy()
            # Mettre à jour la scrollregion
            self._update_scrollregion()
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

        # Lier les touches Entrée et Echap aux actions OK et Annuler
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)

        box.pack()

    def validate(self):
        """Valide les données entrées dans toutes les lignes avant de fermer avec OK."""
        logging.debug("Validation éditeur conditions...")
        validated_conditions = []
        logic = self.logic_var.get()

        # Vérifier que la logique globale (ET/OU) est sélectionnée
        if not logic:
            messagebox.showwarning("Validation", "Veuillez sélectionner une logique globale (ET/OU).", parent=self)
            return 0 # Échec validation

        # Permettre 0 condition (cela désactive la clause SI ou JUSQU'À correspondante)
        if not self.condition_lines:
             logging.debug("Validation OK (aucune condition spécifiée).")
             self.result_logic = logic
             self.result_conditions = []
             return 1 # Validation réussie

        # Parcourir chaque ligne de condition pour la valider
        for i, line_info in enumerate(self.condition_lines):
            widgets = line_info['widgets']
            condition_data = {'condition_id': line_info['condition_id']} # Préparer le dict de sortie

            cond_type = widgets['type_var'].get()
            operator = widgets['operator_var'].get()
            value_str = widgets['value_var'].get().strip() # Valeur entrée, sans espaces superflus

            # --- Vérifications communes ---
            if not cond_type:
                messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez sélectionner un type de condition (Capteur ou Heure).", parent=self)
                return 0
            condition_data['type'] = cond_type

            if not operator:
                messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez sélectionner un opérateur.", parent=self)
                return 0
            condition_data['operator'] = operator

            if not value_str:
                messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez entrer une valeur.", parent=self)
                return 0

            # --- Vérifications spécifiques au type ---
            if cond_type == 'Capteur':
                sensor_name = widgets['sensor_var'].get()
                if not sensor_name:
                    messagebox.showwarning("Validation", f"Ligne {i+1}: Veuillez sélectionner un capteur.", parent=self)
                    return 0
                # Trouver l'ID du capteur basé sur son nom (alias)
                sensor_id = next((sid for name, sid in self.available_sensors if name == sensor_name), None)
                if not sensor_id:
                    # Devrait être rare si la liste est à jour, mais sécurité
                    messagebox.showwarning("Validation", f"Ligne {i+1}: Capteur '{sensor_name}' invalide ou non trouvé.", parent=self)
                    return 0
                condition_data['id'] = sensor_id # Stocker l'ID, pas le nom

                # Valider que la valeur est un nombre
                try:
                    condition_data['threshold'] = float(value_str.replace(',', '.')) # Accepter virgule ou point
                except ValueError:
                    messagebox.showwarning("Validation", f"Ligne {i+1}: Le seuil '{value_str}' est invalide. Entrez une valeur numérique.", parent=self)
                    return 0

                # Vérifier si l'opérateur est valide pour un capteur
                if operator not in SENSOR_OPERATORS:
                     messagebox.showwarning("Validation", f"Ligne {i+1}: L'opérateur '{operator}' n'est pas valide pour une condition de capteur.", parent=self)
                     return 0

            elif cond_type == 'Heure':
                # Valider le format HH:MM
                if not TIME_REGEX.match(value_str):
                    messagebox.showwarning("Validation", f"Ligne {i+1}: L'heure '{value_str}' est invalide. Utilisez le format HH:MM (ex: 14:30).", parent=self)
                    return 0
                condition_data['value'] = value_str # Stocker la chaîne HH:MM
                condition_data['id'] = None # Pas d'ID pour une condition d'heure

                # Vérifier si l'opérateur est valide pour l'heure
                if operator not in TIME_OPERATORS:
                     messagebox.showwarning("Validation", f"Ligne {i+1}: L'opérateur '{operator}' n'est pas valide pour une condition d'heure.", parent=self)
                     return 0

            # Si tout est bon pour cette ligne, ajouter au résultat
            validated_conditions.append(condition_data)

        # Stocker les résultats validés
        self.result_logic = logic
        self.result_conditions = validated_conditions
        logging.debug(f"Validation éditeur OK. Logique: {self.result_logic}, Conditions: {len(self.result_conditions)}")
        return 1 # Validation réussie pour toutes les lignes

    def apply(self):
        """Appelé automatiquement par simpledialog si validate() retourne True."""
        # Vérifier que les résultats ont bien été stockés par validate()
        if self.result_logic is not None and self.result_conditions is not None:
            logging.info(f"Application des changements de l'éditeur pour règle {self.rule_id}, type {self.condition_type}")
            # Appeler la méthode de l'application principale pour mettre à jour la règle
            self.app.update_rule_conditions_from_editor(
                self.rule_id,
                self.condition_type,
                self.result_logic,
                self.result_conditions
            )
        else:
            # Ne devrait pas arriver si validate() a réussi, mais sécurité
            logging.error("Apply appelé mais les résultats de la validation sont manquants.")

#--------------------------------------------------------------------------
# FIN CLASSE ConditionEditor
#--------------------------------------------------------------------------


#--------------------------------------------------------------------------
# CLASSE PRINCIPALE DE L'APPLICATION
#--------------------------------------------------------------------------
class GreenhouseApp:
    """Classe principale de l'application de gestion de serre."""

    def __init__(self, root):
        """Initialise l'application."""
        self.root = root
        self.root.title("Gestionnaire de Serre Connectée")
        try:
            # Définir une taille initiale raisonnable
            self.root.geometry("1300x800")
        except tk.TclError as e:
            logging.warning(f"Erreur lors de la définition de la géométrie initiale: {e}")

        # Configuration du style ttk pour les widgets
        style = ttk.Style(self.root)
        # Style pour les boutons rouges (suppression)
        style.configure("Red.TButton", foreground="red", background="white", font=('Helvetica', 10))
        style.map("Red.TButton",
                  foreground=[('pressed', 'white'), ('active', 'white')],
                  background=[('pressed', 'darkred'), ('active', 'red')])
        # Style pour les labels résumant les conditions (plus petit, italique)
        style.configure("RuleSummary.TLabel", font=('Helvetica', 8, 'italic'))

        # Mise en place du logging via une queue pour la communication inter-thread
        self.log_queue = queue.Queue()
        setup_logging(self.log_queue) # Configurer le handler de logging

        # Chargement de la configuration depuis le fichier YAML
        self.config = load_config(DEFAULT_CONFIG_FILE)
        # Récupération des alias (noms personnalisés)
        self.aliases = self.config.get('aliases', {"sensors": {}, "devices": {}, "outlets": {}})
        loaded_rules = self.config.get('rules', []) # Récupération des règles sauvegardées

        # Nettoyage et initialisation des règles chargées
        self.rules = []
        rule_counter = 1
        for rule_data in loaded_rules:
            if not isinstance(rule_data, dict): continue # Ignorer si ce n'est pas un dictionnaire

            # Assurer un ID unique pour chaque règle
            if 'id' not in rule_data or not rule_data['id']:
                rule_data['id'] = str(uuid.uuid4())

            # Définir des valeurs par défaut pour les champs potentiellement manquants
            rule_data.setdefault('name', f"Règle {rule_counter}")
            rule_data.setdefault('trigger_logic', 'ET') # Logique par défaut pour SI
            rule_data.setdefault('conditions', []) # Liste vide par défaut pour SI
            rule_data.setdefault('until_logic', 'OU') # Logique par défaut pour JUSQU'À
            rule_data.setdefault('until_conditions', []) # Liste vide par défaut pour JUSQU'À

            # Supprimer les anciens champs de condition (obsolètes) s'ils existent
            rule_data.pop('sensor_id', None)
            rule_data.pop('operator', None)
            rule_data.pop('threshold', None)
            rule_data.pop('until_condition', None) # Ancienne structure simple

            # Assurer un ID unique pour chaque condition dans les listes SI et JUSQU'À
            for cond_list_key in ['conditions', 'until_conditions']:
                if cond_list_key in rule_data and isinstance(rule_data[cond_list_key], list):
                    for cond in rule_data[cond_list_key]:
                        if isinstance(cond, dict):
                            cond.setdefault('condition_id', str(uuid.uuid4()))

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
        self.monitoring_active = False # Flag indiquant si la boucle de monitoring tourne
        self.monitoring_thread = None # Référence au thread de monitoring
        self.asyncio_loop = None # Référence à la boucle d'événements asyncio utilisée par le monitoring
        self.ui_update_job = None # Référence au job 'after' pour les mises à jour périodiques de l'UI
        self.live_kasa_states = {} # {mac: {index: bool}} état actuel des prises lu périodiquement
        self.rule_widgets = {} # {rule_id: {'frame': ttk.Frame, 'widgets': dict}} pour accéder aux widgets d'une règle

        # Création de l'interface graphique
        self.create_widgets()
        # Peuplement initial des règles dans l'UI
        self.populate_initial_ui_data()
        # Démarrage de la mise à jour de l'affichage des logs
        self.update_log_display()
        # Lancement de la découverte initiale des périphériques en arrière-plan
        self.discover_all_devices()
        # Gestion de la fermeture de la fenêtre
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Fonctions Alias (Gestion des noms personnalisés) ---
    def get_alias(self, item_type, item_id, sub_id=None):
        """Récupère l'alias (nom personnalisé) pour un capteur, appareil ou prise."""
        try:
            if item_type == 'sensor':
                # Cherche l'alias dans config['aliases']['sensors']
                return self.aliases.get('sensors', {}).get(str(item_id), str(item_id))
            elif item_type == 'device':
                # Cherche l'alias dans config['aliases']['devices']
                return self.aliases.get('devices', {}).get(str(item_id), str(item_id))
            elif item_type == 'outlet':
                # Cherche l'alias dans config['aliases']['outlets'][device_id]
                device_outlets = self.aliases.get('outlets', {}).get(str(item_id), {})
                # Nom par défaut si aucun alias trouvé
                fallback_name = f"Prise {sub_id}"
                # Essayer de récupérer le nom par défaut de la prise depuis Kasa si disponible
                if str(item_id) in self.kasa_devices:
                    kasa_info = self.kasa_devices[str(item_id)].get('info', {})
                    outlet_info_list = kasa_info.get('outlets', [])
                    outlet_info = next((o for o in outlet_info_list if o.get('index') == sub_id), None)
                    if outlet_info:
                        fallback_name = outlet_info.get('alias', fallback_name) # Utiliser l'alias Kasa comme fallback
                return device_outlets.get(str(sub_id), fallback_name)
        except KeyError:
            # En cas d'erreur (rare), retourner l'ID brut
            pass

        # Fallback général si la recherche échoue complètement
        if sub_id is not None:
             # Pour une prise, essayer de récupérer le nom Kasa si possible
             if item_type == 'outlet' and str(item_id) in self.kasa_devices:
                 kasa_info = self.kasa_devices[str(item_id)].get('info', {})
                 outlet_info_list = kasa_info.get('outlets', [])
                 outlet_info = next((o for o in outlet_info_list if o.get('index') == sub_id), None)
                 if outlet_info: return outlet_info.get('alias', f"Prise {sub_id}")
             return f"{item_id}-Prise {sub_id}" # ID_appareil-Prise X
        return str(item_id) # ID brut

    def update_alias(self, item_type, item_id, new_alias, sub_id=None):
        """Met à jour l'alias d'un élément dans la configuration."""
        # S'assurer que la structure 'aliases' existe dans la config
        if 'aliases' not in self.config:
            self.config['aliases'] = {"sensors": {}, "devices": {}, "outlets": {}}

        if item_type == 'outlet':
            # Gérer la structure imbriquée pour les prises
            if 'outlets' not in self.config['aliases']: self.config['aliases']['outlets'] = {}
            if str(item_id) not in self.config['aliases']['outlets']: self.config['aliases']['outlets'][str(item_id)] = {}
            self.config['aliases']['outlets'][str(item_id)][str(sub_id)] = new_alias
        elif item_type == 'device':
            if 'devices' not in self.config['aliases']: self.config['aliases']['devices'] = {}
            self.config['aliases']['devices'][str(item_id)] = new_alias
        elif item_type == 'sensor':
            if 'sensors' not in self.config['aliases']: self.config['aliases']['sensors'] = {}
            self.config['aliases']['sensors'][str(item_id)] = new_alias
        else:
            logging.error(f"Type d'élément inconnu pour la mise à jour d'alias: {item_type}")
            return

        # Mettre à jour la variable self.aliases utilisée par get_alias
        self.aliases = self.config['aliases']
        logging.info(f"Alias mis à jour pour {item_type} {item_id}" + (f"[{sub_id}]" if sub_id else "") + f": '{new_alias}'")
        # Note: La sauvegarde réelle se fait via le bouton "Sauvegarder"

    def edit_alias_dialog(self, item_type, item_id, current_name, sub_id=None):
        """Ouvre une boîte de dialogue pour modifier l'alias d'un élément."""
        prompt = f"Entrez le nouveau nom pour {item_type} '{current_name}'"
        # Personnaliser le message selon le type d'élément
        if item_type == 'outlet':
            device_alias = self.get_alias('device', item_id)
            prompt = f"Nouveau nom pour la prise '{current_name}'\n(Appareil: {device_alias})"
        elif item_type == 'device':
            prompt = f"Nouveau nom pour l'appareil Kasa '{current_name}'\n(MAC: {item_id})"
        elif item_type == 'sensor':
             prompt = f"Nouveau nom pour le capteur '{current_name}'\n(ID: {item_id})"

        # Ouvrir la boîte de dialogue modale
        new_name = simpledialog.askstring("Modifier Alias", prompt,
                                          initialvalue=current_name, parent=self.root)

        # Si un nouveau nom est entré et qu'il est différent de l'ancien
        if new_name and new_name.strip() and new_name.strip() != current_name:
            new_name = new_name.strip()
            self.update_alias(item_type, item_id, new_name, sub_id)
            # Rafraîchir l'interface pour refléter le changement
            self.refresh_device_lists() # Met à jour les listes internes et les combobox des règles
            # self.repopulate_all_rule_dropdowns() # Est appelé par refresh_device_lists
            self.update_status_display() # Met à jour l'affichage du statut
            self.root.update_idletasks() # Forcer la mise à jour de l'UI

    # --- Création des Widgets de l'Interface Principale ---
    def create_widgets(self):
        """Crée tous les widgets principaux de l'interface graphique."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Section des Règles (Scrollable) ---
        rules_frame_container = ttk.LabelFrame(main_frame, text="Règles d'Automatisation", padding="10")
        rules_frame_container.pack(fill=tk.X, expand=False, pady=5)

        # Canvas pour la zone scrollable des règles
        self.rules_canvas = tk.Canvas(rules_frame_container, borderwidth=0, highlightthickness=0)
        # Scrollbar verticale
        scrollbar = ttk.Scrollbar(rules_frame_container, orient="vertical", command=self.rules_canvas.yview)
        # Frame interne qui contiendra les règles
        self.scrollable_rules_frame = ttk.Frame(self.rules_canvas)
        # Mettre à jour la scrollregion quand le frame interne change
        self.scrollable_rules_frame.bind("<Configure>", lambda e: self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all")))
        # Placer le frame interne dans le canvas
        self.rules_canvas.create_window((0, 0), window=self.scrollable_rules_frame, anchor="nw")
        # Lier la scrollbar au canvas
        self.rules_canvas.configure(yscrollcommand=scrollbar.set)
        # Empaqueter
        self.rules_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        # Définir une hauteur fixe pour la zone des règles
        self.rules_canvas.config(height=300) # Ajustez si nécessaire

        # --- Bouton Ajouter une Règle ---
        add_rule_button = ttk.Button(main_frame, text="➕ Ajouter une Règle", command=self.add_rule_ui)
        add_rule_button.pack(pady=5)

        # --- Section Contrôles (Démarrer/Arrêter/Sauvegarder) ---
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.pack(fill=tk.X, expand=False, pady=5)
        self.start_button = ttk.Button(control_frame, text="🟢 Gérer ma Serre", command=self.start_monitoring)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(control_frame, text="🔴 Arrêter", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        save_button = ttk.Button(control_frame, text="💾 Sauvegarder Configuration", command=self.save_configuration)
        save_button.pack(side=tk.RIGHT, padx=5) # Placé à droite

        # --- Panneau Divisé pour Statut et Logs ---
        status_log_pane = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        status_log_pane.pack(fill=tk.BOTH, expand=True, pady=5)

        # --- Section Statut Actuel (Scrollable) ---
        status_frame_container = ttk.LabelFrame(status_log_pane, text="Statut Actuel", padding="10")
        status_log_pane.add(status_frame_container, weight=1) # Prend la moitié de l'espace (ajustable)

        # Canvas pour la zone scrollable du statut
        status_canvas = tk.Canvas(status_frame_container, borderwidth=0, highlightthickness=0)
        status_scrollbar = ttk.Scrollbar(status_frame_container, orient="vertical", command=status_canvas.yview)
        self.scrollable_status_frame = ttk.Frame(status_canvas)
        self.scrollable_status_frame.bind("<Configure>", lambda e: status_canvas.configure(scrollregion=status_canvas.bbox("all")))
        status_canvas.create_window((0, 0), window=self.scrollable_status_frame, anchor="nw")
        status_canvas.configure(yscrollcommand=status_scrollbar.set)
        status_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # --- Section Journal d'Événements ---
        log_frame_container = ttk.LabelFrame(status_log_pane, text="Journal d'Événements", padding="10")
        status_log_pane.add(log_frame_container, weight=1) # Prend l'autre moitié

        # Zone de texte scrollable pour les logs
        self.log_display = scrolledtext.ScrolledText(log_frame_container, wrap=tk.WORD, state=tk.DISABLED, height=15)
        self.log_display.pack(fill=tk.BOTH, expand=True)

        # Dictionnaires pour stocker les références aux widgets dynamiques
        self.status_labels = {} # Pour les labels de statut (capteurs, prises)
        # self.rule_widgets est initialisé dans __init__

    # --- Peuplement Initial de l'UI ---
    def populate_initial_ui_data(self):
        """Ajoute les règles chargées depuis la configuration à l'interface graphique."""
        for rule_data in self.rules:
            self.add_rule_ui(rule_data=rule_data)

    # --- Gestion de l'UI des Règles ---
    def add_rule_ui(self, rule_data=None):
        """Ajoute une nouvelle règle (vide) ou une règle existante à l'interface."""
        is_new_rule = False
        if not rule_data:
            # Si aucune donnée fournie, c'est une nouvelle règle
            is_new_rule = True
            rule_id = str(uuid.uuid4()) # Générer un nouvel ID unique
            # Créer une structure de données par défaut pour la nouvelle règle
            rule_data = {
                'id': rule_id,
                'name': f"Nouvelle Règle {len(self.rules) + 1}",
                'trigger_logic': 'ET', # Logique SI par défaut
                'conditions': [], # Liste vide de conditions SI
                'target_device_mac': None, # Aucun appareil cible par défaut
                'target_outlet_index': None, # Aucune prise cible par défaut
                'action': ACTIONS[0], # Action par défaut (ON)
                'until_logic': 'OU', # Logique JUSQU'À par défaut
                'until_conditions': [] # Liste vide de conditions JUSQU'À
            }
            # Ajouter la nouvelle règle à la liste interne
            self.rules.append(rule_data)
        else:
            # Si des données sont fournies, utiliser l'ID existant (ou en générer un si manquant)
            rule_id = rule_data.get('id')
            if not rule_id:
                rule_id = str(uuid.uuid4())
                rule_data['id'] = rule_id

        # --- Création du Frame principal pour cette règle ---
        rule_frame = ttk.Frame(self.scrollable_rules_frame, padding="5", borderwidth=1, relief="groove")
        rule_frame.pack(fill=tk.X, pady=3, padx=2)
        widgets = {} # Dictionnaire pour stocker les widgets de cette règle

        # --- Ligne 1: Nom de la règle et bouton Supprimer ---
        name_frame = ttk.Frame(rule_frame)
        name_frame.pack(side=tk.TOP, fill=tk.X, expand=True)
        # Label pour afficher le nom (modifiable)
        widgets['name_label'] = ttk.Label(name_frame, text=rule_data.get('name', 'Sans Nom'), font=('Helvetica', 10, 'bold'))
        widgets['name_label'].pack(side=tk.LEFT, padx=(0, 5), pady=(0, 3))
        # Bouton pour éditer le nom
        widgets['edit_name_button'] = ttk.Button(name_frame, text="✎", width=2,
                                                 command=lambda r_id=rule_id: self.edit_rule_name_dialog(r_id))
        widgets['edit_name_button'].pack(side=tk.LEFT, padx=(0, 15))
        # Bouton pour supprimer la règle
        delete_rule_button = ttk.Button(name_frame, text="❌", width=3, style="Red.TButton",
                                        command=lambda rid=rule_id: self.delete_rule(rid))
        delete_rule_button.pack(side=tk.RIGHT, padx=5) # Aligné à droite

        # --- Ligne 2: Conditions SI et partie ALORS ---
        main_line_frame = ttk.Frame(rule_frame)
        main_line_frame.pack(side=tk.TOP, fill=tk.X, expand=True, pady=3)

        # Label résumé pour les conditions SI
        widgets['si_summary_label'] = ttk.Label(main_line_frame,
                                                text=self._generate_condition_summary(rule_data.get('conditions', []), rule_data.get('trigger_logic', 'ET')),
                                                style="RuleSummary.TLabel", anchor="w", width=40) # Largeur fixe pour alignement
        widgets['si_summary_label'].pack(side=tk.LEFT, padx=(5, 0))
        # Bouton pour ouvrir l'éditeur de conditions SI
        widgets['edit_si_button'] = ttk.Button(main_line_frame, text="SI...", width=5,
                                               command=lambda r_id=rule_id: self.open_condition_editor(r_id, 'trigger'))
        widgets['edit_si_button'].pack(side=tk.LEFT, padx=(0, 10))

        # Label "ALORS"
        ttk.Label(main_line_frame, text="ALORS").pack(side=tk.LEFT, padx=(10, 2))
        # Combobox pour choisir l'appareil Kasa (multiprise)
        widgets['kasa_var'] = tk.StringVar()
        widgets['kasa_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['kasa_var'], width=25, state="readonly")
        widgets['kasa_combo']['values'] = [name for name, _mac in self.available_kasa_strips] # Peupler avec les alias Kasa
        widgets['kasa_combo'].pack(side=tk.LEFT, padx=2)
        # Mettre à jour les options de prise quand un appareil Kasa est sélectionné
        widgets['kasa_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.update_outlet_options(rid))

        # Combobox pour choisir la prise spécifique de l'appareil Kasa
        widgets['outlet_var'] = tk.StringVar()
        widgets['outlet_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['outlet_var'], width=20, state="readonly")
        widgets['outlet_combo']['values'] = [] # Sera peuplé par update_outlet_options
        widgets['outlet_combo'].pack(side=tk.LEFT, padx=2)
        # Enregistrer le changement de prise dans les données de la règle
        widgets['outlet_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # Combobox pour choisir l'action (ON/OFF)
        widgets['action_var'] = tk.StringVar()
        widgets['action_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['action_var'], values=ACTIONS, width=5, state="readonly")
        widgets['action_combo'].pack(side=tk.LEFT, padx=2)
        # Enregistrer le changement d'action dans les données de la règle
        widgets['action_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # --- Ligne 3: Conditions JUSQU'À ---
        until_frame = ttk.Frame(rule_frame)
        # Indenter légèrement pour montrer la dépendance à l'action ALORS
        until_frame.pack(side=tk.TOP, fill=tk.X, expand=True, padx=(30, 0), pady=(0, 2))
        # Petite flèche pour indiquer la condition d'arrêt
        ttk.Label(until_frame, text="↳").pack(side=tk.LEFT, padx=(0, 5))
        # Label résumé pour les conditions JUSQU'À
        widgets['until_summary_label'] = ttk.Label(until_frame,
                                                   text=self._generate_condition_summary(rule_data.get('until_conditions', []), rule_data.get('until_logic', 'OU')),
                                                   style="RuleSummary.TLabel", anchor="w", width=40) # Largeur fixe
        widgets['until_summary_label'].pack(side=tk.LEFT, padx=(0,0))
        # Bouton pour ouvrir l'éditeur de conditions JUSQU'À
        widgets['edit_until_button'] = ttk.Button(until_frame, text="JUSQU'À...", width=10,
                                                  command=lambda r_id=rule_id: self.open_condition_editor(r_id, 'until'))
        widgets['edit_until_button'].pack(side=tk.LEFT, padx=(5, 10))

        # Stocker les widgets créés pour cette règle
        self.rule_widgets[rule_id] = {'frame': rule_frame, 'widgets': widgets}

        # Si ce n'est pas une nouvelle règle, peupler les widgets avec les données existantes
        if not is_new_rule:
            self._populate_rule_ui_from_data(rule_id, rule_data)

        # Mettre à jour la scrollregion du canvas des règles
        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))

    def _generate_condition_summary(self, conditions, logic):
        """Génère une chaîne résumant le nombre de conditions et la logique."""
        if not isinstance(conditions, list): conditions = [] # Sécurité
        count = len(conditions)
        if count == 0:
            return "(Aucune condition)"
        elif count == 1:
            return "(1 condition)"
        else:
            # Assurer que la logique est valide avant de l'afficher
            logic_str = logic if logic in LOGIC_OPERATORS else 'ET' # Défaut ET si invalide
            return f"({count} conditions - {logic_str})"

    def edit_rule_name_dialog(self, rule_id):
        """Ouvre une boîte de dialogue pour modifier le nom d'une règle."""
        # Trouver les données de la règle correspondante
        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Impossible de modifier le nom: Règle {rule_id} non trouvée.")
            return

        current_name = rule_data.get('name', '')
        # Ouvrir la boîte de dialogue
        new_name = simpledialog.askstring("Modifier Nom de Règle",
                                          f"Entrez le nouveau nom pour la règle '{current_name}'",
                                          initialvalue=current_name, parent=self.root)

        # Si un nouveau nom est entré et est différent
        if new_name and new_name.strip() and new_name.strip() != current_name:
            new_name = new_name.strip()
            # Mettre à jour les données de la règle
            rule_data['name'] = new_name
            # Mettre à jour le label dans l'UI si le widget existe encore
            if rule_id in self.rule_widgets and 'name_label' in self.rule_widgets[rule_id]['widgets']:
                try:
                    self.rule_widgets[rule_id]['widgets']['name_label'].config(text=new_name)
                except tk.TclError:
                    pass # Ignorer si le widget a été détruit entre-temps
            logging.info(f"Nom de la règle {rule_id} mis à jour: '{new_name}'")

    def _populate_rule_ui_from_data(self, rule_id, rule_data):
        """Peuple les widgets d'une règle existante avec ses données."""
        if rule_id not in self.rule_widgets:
            logging.warning(f"Tentative de peupler l'UI pour règle {rule_id} non trouvée dans rule_widgets.")
            return

        widgets = self.rule_widgets[rule_id]['widgets']

        # Mettre à jour le nom et les résumés de conditions
        try:
            if widgets['name_label'].winfo_exists():
                 widgets['name_label'].config(text=rule_data.get('name', 'Sans Nom'))
            if widgets['si_summary_label'].winfo_exists():
                 widgets['si_summary_label'].config(text=self._generate_condition_summary(rule_data.get('conditions', []), rule_data.get('trigger_logic', 'ET')))
            if widgets['until_summary_label'].winfo_exists():
                 widgets['until_summary_label'].config(text=self._generate_condition_summary(rule_data.get('until_conditions', []), rule_data.get('until_logic', 'OU')))
        except tk.TclError:
             logging.warning(f"Erreur TclError lors de la mise à jour des labels pour la règle {rule_id} (widget détruit?).")
             return # Arrêter si les widgets de base n'existent plus

        # Récupérer les informations de la cible (ALORS)
        kasa_mac = rule_data.get('target_device_mac')
        outlet_index = rule_data.get('target_outlet_index') # Peut être None ou un entier

        # Mettre à jour les combobox Kasa, Prise et Action
        try:
            if widgets['kasa_combo'].winfo_exists():
                if kasa_mac:
                    kasa_alias = self.get_alias('device', kasa_mac)
                    # Vérifier si l'alias existe dans les options actuelles du combobox
                    kasa_options = widgets['kasa_combo']['values']
                    if kasa_alias in kasa_options:
                        widgets['kasa_var'].set(kasa_alias)
                        # Important: Stocker l'index désiré pour le pré-sélectionner après mise à jour des options
                        self.rule_widgets[rule_id]['desired_outlet_index'] = outlet_index
                        # Mettre à jour les options de prises pour cet appareil et pré-sélectionner la bonne
                        self.update_outlet_options(rule_id, preselect_outlet_index=outlet_index)
                    else:
                        # L'appareil Kasa n'est plus disponible ou son alias a changé
                        widgets['kasa_var'].set('')
                        if widgets['outlet_combo'].winfo_exists():
                            widgets['outlet_combo']['values'] = []
                            widgets['outlet_var'].set('')
                else:
                    # Aucune Kasa sélectionnée
                    widgets['kasa_var'].set('')
                    if widgets['outlet_combo'].winfo_exists():
                        widgets['outlet_combo']['values'] = []
                        widgets['outlet_var'].set('')

            # Mettre à jour l'action (ON/OFF)
            if widgets['action_combo'].winfo_exists():
                 action = rule_data.get('action', ACTIONS[0])
                 if action in ACTIONS:
                     widgets['action_var'].set(action)
                 else:
                     widgets['action_var'].set(ACTIONS[0]) # Action par défaut si invalide

        except tk.TclError:
             logging.warning(f"Erreur TclError lors de la mise à jour des combobox ALORS pour la règle {rule_id}.")

    def delete_rule(self, rule_id):
        """Supprime une règle de l'UI et de la liste interne."""
        if rule_id in self.rule_widgets:
            # Détruire le frame Tkinter de la règle
            try:
                self.rule_widgets[rule_id]['frame'].destroy()
            except tk.TclError:
                pass # Ignorer si déjà détruit
            # Supprimer l'entrée du dictionnaire des widgets
            del self.rule_widgets[rule_id]

            # Supprimer la règle de la liste interne self.rules
            initial_len = len(self.rules)
            self.rules = [rule for rule in self.rules if rule.get('id') != rule_id]

            if len(self.rules) < initial_len:
                logging.info(f"Règle {rule_id} supprimée.")
            else:
                # Ne devrait pas arriver si rule_id était dans rule_widgets
                logging.warning(f"Règle {rule_id} trouvée dans l'UI mais pas dans les données internes lors de la suppression.")

            # Mettre à jour la scrollregion du canvas des règles
            self.rules_canvas.update_idletasks()
            self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))
        else:
            logging.warning(f"Tentative de suppression de la règle {rule_id} non trouvée dans l'UI.")

    def update_outlet_options(self, rule_id, preselect_outlet_index=None):
        """Met à jour les options du combobox de prise en fonction de l'appareil Kasa sélectionné."""
        if rule_id not in self.rule_widgets: return # Sécurité

        widgets = self.rule_widgets[rule_id]['widgets']
        selected_kasa_name = widgets['kasa_var'].get() # Nom (alias) de l'appareil Kasa sélectionné

        # Trouver le MAC correspondant à l'alias sélectionné
        selected_mac = next((mac for name, mac in self.available_kasa_strips if name == selected_kasa_name), None)

        outlet_options = [] # Liste des alias de prises pour le combobox
        current_outlet_alias = "" # Alias de la prise à pré-sélectionner

        # Si un MAC valide a été trouvé et qu'on a des infos sur ses prises
        if selected_mac and selected_mac in self.available_outlets:
            # Récupérer les alias et index des prises pour cet appareil
            outlet_options = [name for name, _index in self.available_outlets[selected_mac]]

            # Si un index de pré-sélection est fourni
            if preselect_outlet_index is not None:
                # Trouver l'alias correspondant à cet index
                current_outlet_alias = next((name for name, index in self.available_outlets[selected_mac] if index == preselect_outlet_index), "")

        # Mettre à jour le combobox de prise (s'il existe encore)
        try:
            if widgets['outlet_combo'].winfo_exists():
                widgets['outlet_combo']['values'] = outlet_options # Mettre à jour la liste déroulante
                if current_outlet_alias:
                    widgets['outlet_var'].set(current_outlet_alias) # Pré-sélectionner l'alias trouvé
                elif outlet_options:
                    widgets['outlet_var'].set(outlet_options[0]) # Sélectionner la première prise si pas de pré-sélection
                else:
                    widgets['outlet_var'].set('') # Vider si aucune prise disponible
        except tk.TclError:
            pass # Ignorer si le widget a été détruit

        # Mettre à jour les données de la règle après changement d'appareil ou de prise
        self.on_rule_change(rule_id)

    def on_rule_change(self, rule_id):
        """Met à jour les données internes de la règle (partie ALORS) quand un combobox change."""
        if rule_id not in self.rule_widgets: return # Sécurité

        # Trouver les données de la règle correspondante
        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data:
            logging.warning(f"on_rule_change: Règle {rule_id} non trouvée dans les données.")
            return

        widgets = self.rule_widgets[rule_id]['widgets']

        # Récupérer les valeurs actuelles des combobox ALORS
        kasa_name = widgets['kasa_var'].get()
        outlet_name = widgets['outlet_var'].get()
        action = widgets['action_var'].get()

        # Trouver le MAC de l'appareil Kasa basé sur l'alias
        kasa_mac = next((m for n, m in self.available_kasa_strips if n == kasa_name), None)

        # Trouver l'index de la prise basé sur l'alias (et le MAC)
        outlet_index = None
        if kasa_mac and kasa_mac in self.available_outlets:
             outlet_index = next((idx for name, idx in self.available_outlets[kasa_mac] if name == outlet_name), None)

        # Mettre à jour les données de la règle
        rule_data['target_device_mac'] = kasa_mac
        rule_data['target_outlet_index'] = outlet_index # Sera None si non trouvé
        rule_data['action'] = action

        logging.debug(f"Partie ALORS de la règle {rule_id} mise à jour dans les données: MAC={kasa_mac}, Index={outlet_index}, Action={action}")

    def repopulate_all_rule_dropdowns(self):
        """Met à jour les listes déroulantes Kasa/Prise pour toutes les règles affichées."""
        logging.debug("Repopulation des listes déroulantes Kasa/Prise pour toutes les règles.")
        # Obtenir la liste actuelle des alias d'appareils Kasa
        kasa_names = [name for name, _mac in self.available_kasa_strips]

        # Parcourir toutes les règles actuellement affichées dans l'UI
        for rule_id, data in self.rule_widgets.items():
            widgets = data['widgets']
            # Trouver les données correspondantes pour cette règle
            rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
            if not rule_data: continue # Passer si la règle n'existe plus dans les données

            # Récupérer le MAC et l'alias Kasa actuellement sauvegardés pour cette règle
            current_kasa_mac = rule_data.get('target_device_mac')
            current_kasa_name = self.get_alias('device', current_kasa_mac) if current_kasa_mac else ""

            try:
                # Mettre à jour le combobox Kasa (s'il existe)
                if widgets['kasa_combo'].winfo_exists():
                    widgets['kasa_combo']['values'] = kasa_names # Mettre à jour la liste

                    # Si l'alias Kasa actuel est toujours valide
                    if current_kasa_name in kasa_names:
                        widgets['kasa_var'].set(current_kasa_name) # Resélectionner l'alias
                        # Récupérer l'index de prise désiré (soit depuis la sauvegarde, soit depuis l'état temporaire)
                        desired_outlet_index = data.get('desired_outlet_index', rule_data.get('target_outlet_index'))
                        # Mettre à jour les options de prise et pré-sélectionner la bonne
                        self.update_outlet_options(rule_id, preselect_outlet_index=desired_outlet_index)
                        # Supprimer l'état temporaire après utilisation
                        if 'desired_outlet_index' in data: del data['desired_outlet_index']
                    else:
                        # Si l'alias Kasa n'est plus valide (appareil disparu, renommé?)
                        widgets['kasa_var'].set('') # Vider la sélection Kasa
                        # Vider aussi la sélection de prise
                        if widgets['outlet_combo'].winfo_exists():
                            widgets['outlet_combo']['values'] = []
                            widgets['outlet_var'].set('')
            except tk.TclError:
                # Ignorer si un widget a été détruit pendant le processus
                logging.warning(f"Erreur TclError lors de la repopulation des dropdowns pour règle {rule_id}.")
                pass

    # --- Ouverture de l'éditeur de conditions ---
    def open_condition_editor(self, rule_id, condition_type):
        """Ouvre le pop-up ConditionEditor pour éditer les conditions SI ou JUSQU'À."""
        # Trouver les données de la règle
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Impossible d'ouvrir l'éditeur: Règle {rule_id} non trouvée.")
            messagebox.showerror("Erreur", f"Impossible de trouver les données pour la règle {rule_id}.", parent=self.root)
            return

        # Préparer les données initiales pour l'éditeur
        if condition_type == 'trigger': # Conditions SI
            logic = rule_data.get('trigger_logic', 'ET')
            # Passer une copie pour éviter la modification directe
            conditions = list(rule_data.get('conditions', []))
            title = f"Modifier les Conditions SI - Règle '{rule_data.get('name', rule_id)}'"
        elif condition_type == 'until': # Conditions JUSQU'À
            logic = rule_data.get('until_logic', 'OU')
            # Passer une copie
            conditions = list(rule_data.get('until_conditions', []))
            title = f"Modifier les Conditions JUSQU'À - Règle '{rule_data.get('name', rule_id)}'"
        else:
            logging.error(f"Type de condition inconnu demandé pour l'éditeur: {condition_type}")
            return

        # Créer et lancer l'éditeur (la fenêtre est modale, bloque l'application principale)
        logging.debug(f"Ouverture éditeur pour règle {rule_id}, type {condition_type}")
        editor = ConditionEditor(self.root, title, rule_id, condition_type, logic, conditions, self.available_sensors, self)
        # L'éditeur se charge du reste. Si l'utilisateur clique OK et que la validation réussit,
        # la méthode apply() de l'éditeur appellera self.update_rule_conditions_from_editor.

    # --- Méthode appelée par l'éditeur après clic sur OK et validation ---
    def update_rule_conditions_from_editor(self, rule_id, condition_type, new_logic, new_conditions):
        """Met à jour les données de la règle et l'UI principale après édition via le pop-up."""
        # Retrouver la règle dans les données internes
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Échec mise à jour depuis éditeur: Règle {rule_id} non trouvée.")
            return

        logging.info(f"Mise à jour des conditions '{condition_type}' pour la règle {rule_id}. Logique: {new_logic}, Nombre de conditions: {len(new_conditions)}")
        logging.debug(f"Nouvelles conditions: {new_conditions}")

        # Mettre à jour les données de la règle
        widgets = self.rule_widgets.get(rule_id, {}).get('widgets', {}) # Récupérer les widgets de l'UI pour cette règle

        if condition_type == 'trigger':
            rule_data['trigger_logic'] = new_logic
            rule_data['conditions'] = new_conditions
            # Mettre à jour le label résumé SI dans l'UI (si le widget existe)
            if 'si_summary_label' in widgets:
                try:
                    widgets['si_summary_label'].config(text=self._generate_condition_summary(new_conditions, new_logic))
                except tk.TclError: pass # Ignorer si détruit
        elif condition_type == 'until':
            rule_data['until_logic'] = new_logic
            rule_data['until_conditions'] = new_conditions
            # Mettre à jour le label résumé JUSQU'À dans l'UI (si le widget existe)
            if 'until_summary_label' in widgets:
                try:
                     widgets['until_summary_label'].config(text=self._generate_condition_summary(new_conditions, new_logic))
                except tk.TclError: pass # Ignorer si détruit

    # --- Découverte / Rafraîchissement des Périphériques ---
    def discover_all_devices(self):
        """Lance la découverte de tous les types de périphériques (Capteurs T°, Lux, Kasa)."""
        logging.info("Lancement de la découverte de tous les périphériques...")
        # Découverte des capteurs de température (synchrone, rapide)
        try:
            self.temp_manager.discover_sensors()
            logging.info(f"Découverte Température: {len(self.temp_manager.sensors)} capteur(s) trouvé(s).")
        except Exception as e:
            logging.error(f"Erreur lors de la découverte des capteurs de température: {e}")

        # Découverte des capteurs de lumière (synchrone, rapide)
        try:
            self.light_manager.scan_sensors()
            active_light_sensors = self.light_manager.get_active_sensors()
            logging.info(f"Découverte Lumière (BH1750): {len(active_light_sensors)} capteur(s) trouvé(s).")
        except Exception as e:
            logging.error(f"Erreur lors de la découverte des capteurs de lumière: {e}")

        # Découverte des appareils Kasa (asynchrone, potentiellement long)
        # Lancé dans un thread séparé pour ne pas bloquer l'UI
        threading.Thread(target=self._run_kasa_discovery_async, daemon=True).start()

    def _run_kasa_discovery_async(self):
        """Exécute la découverte Kasa asynchrone dans une boucle d'événements."""
        try:
            # Essayer de récupérer la boucle d'événements existante (si déjà créée par monitoring)
            loop = asyncio.get_event_loop()
        except RuntimeError:
            # Si aucune boucle n'existe, en créer une nouvelle
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        # Exécuter la tâche de découverte Kasa jusqu'à ce qu'elle soit terminée
        loop.run_until_complete(self._async_discover_kasa())
        # Note: Ne ferme pas la boucle ici, elle pourrait être réutilisée par le monitoring

    async def _async_discover_kasa(self):
        """Tâche asynchrone pour découvrir les appareils Kasa sur le réseau."""
        logging.info("Début découverte Kasa asynchrone...")
        discoverer = DeviceDiscoverer()
        try:
            discovered_kasa = await discoverer.discover() # Lance la découverte réseau
        except Exception as e:
            logging.error(f"Erreur critique pendant la découverte Kasa: {e}")
            discovered_kasa = []

        new_kasa_devices = {} # Dictionnaire temporaire pour les nouveaux appareils
        tasks_initial_state = [] # Tâches pour récupérer l'état initial et éteindre si besoin

        for dev_info in discovered_kasa:
            ip = dev_info.get('ip')
            mac = dev_info.get('mac')
            alias = dev_info.get('alias', 'N/A')

            if not ip or not mac:
                logging.warning(f"Appareil Kasa découvert sans IP ou MAC: Alias='{alias}', Info={dev_info}")
                continue

            # Créer un contrôleur pour cet appareil
            is_strip = dev_info.get('is_strip', False)
            is_plug = dev_info.get('is_plug', False)
            ctrl = DeviceController(ip, is_strip, is_plug)

            # Stocker les informations et le contrôleur
            new_kasa_devices[mac] = {'info': dev_info, 'controller': ctrl, 'ip': ip }

            # Si le monitoring n'est pas actif, on essaie d'éteindre toutes les prises par sécurité
            # (On ne le fait pas si le monitoring tourne pour ne pas interférer avec les règles)
            # On le fait ici pendant la découverte pour profiter de la connexion établie
            if not self.monitoring_active and (is_strip or is_plug):
                logging.debug(f"Ajout tâche d'extinction initiale pour {alias} ({mac})")
                tasks_initial_state.append(ctrl.turn_all_outlets_off())

        # Exécuter les tâches d'extinction initiale si nécessaire
        if tasks_initial_state:
             logging.info(f"Exécution de {len(tasks_initial_state)} tâches d'extinction initiale Kasa...")
             try:
                 # Exécuter en parallèle et attendre la fin
                 results = await asyncio.gather(*tasks_initial_state, return_exceptions=True)
                 for i, res in enumerate(results):
                     if isinstance(res, Exception):
                         # Trouver l'appareil correspondant à l'erreur
                         failed_task = tasks_initial_state[i]
                         # Malheureusement, difficile de retrouver le MAC/Alias facilement ici sans plus d'infos
                         logging.error(f"Erreur lors de l'extinction initiale Kasa (tâche {i}): {res}")
             except Exception as e_gather:
                 logging.error(f"Erreur imprévue durant gather pour l'extinction initiale: {e_gather}")
             logging.info("Tâches d'extinction initiale Kasa terminées.")

        # Mettre à jour la liste principale des appareils Kasa
        self.kasa_devices = new_kasa_devices
        logging.info(f"Découverte Kasa terminée: {len(self.kasa_devices)} appareil(s) trouvé(s).")

        # Planifier l'exécution de refresh_device_lists dans le thread principal de Tkinter
        # Utiliser after(0) ou after(100) pour s'assurer que cela s'exécute après la fin de cette coroutine
        self.root.after(100, self.refresh_device_lists)

    def refresh_device_lists(self):
        """Met à jour les listes internes (available_sensors, etc.) et rafraîchit l'UI."""
        logging.info("Rafraîchissement des listes de périphériques pour l'UI...")

        # --- Mise à jour des capteurs disponibles ---
        temp_sensor_ids = []
        light_sensor_ids = []
        try:
            # Récupérer les IDs des capteurs de température
            temp_sensor_ids = [s.id for s in self.temp_manager.sensors]
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des IDs de capteurs de température: {e}")
        try:
            # Récupérer les adresses (IDs) des capteurs de lumière actifs
            light_sensor_ids = [hex(addr) for addr in self.light_manager.get_active_sensors()]
        except Exception as e:
            logging.error(f"Erreur lors de la récupération des IDs de capteurs de lumière: {e}")

        # Combiner les IDs et créer la liste (alias, id) pour les combobox, triée par alias
        all_sensor_ids = set(temp_sensor_ids + light_sensor_ids) # Utiliser un set pour éviter les doublons si un ID est utilisé pour les deux
        self.available_sensors = sorted(
            [(self.get_alias('sensor', sensor_id), sensor_id) for sensor_id in all_sensor_ids],
            key=lambda x: x[0] # Trier par alias (le premier élément du tuple)
        )
        logging.debug(f"Capteurs disponibles mis à jour: {self.available_sensors}")

        # --- Mise à jour des appareils et prises Kasa disponibles ---
        self.available_kasa_strips = [] # Liste [(alias_appareil, mac), ...]
        self.available_outlets = {} # Dict {mac: [(alias_prise, index), ...]}

        # Trier les MAC des appareils Kasa par leur alias pour un affichage cohérent
        sorted_kasa_macs = sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m))

        for mac in sorted_kasa_macs:
            data = self.kasa_devices[mac]
            device_alias = self.get_alias('device', mac)
            # Ajouter l'appareil à la liste pour le combobox Kasa
            self.available_kasa_strips.append((device_alias, mac))

            outlets_for_device = []
            # Si c'est une multiprise ou une prise simple, récupérer ses prises
            if data['info'].get('is_strip') or data['info'].get('is_plug'):
                # Parcourir les informations des prises fournies par la découverte
                for outlet_data in data['info'].get('outlets', []):
                    outlet_index = outlet_data.get('index')
                    if outlet_index is not None: # S'assurer qu'on a un index
                        outlet_alias = self.get_alias('outlet', mac, outlet_index)
                        outlets_for_device.append((outlet_alias, outlet_index))

            # Stocker les prises pour cet appareil, triées par index
            self.available_outlets[mac] = sorted(outlets_for_device, key=lambda x: x[1])

        logging.debug(f"Appareils Kasa disponibles mis à jour: {self.available_kasa_strips}")
        logging.debug(f"Prises Kasa disponibles mises à jour: {self.available_outlets}")

        # --- Rafraîchir l'UI ---
        # Mettre à jour les listes déroulantes dans les règles existantes
        self.repopulate_all_rule_dropdowns()
        # Mettre à jour l'affichage du panneau de statut
        self.update_status_display()
        logging.info("Listes de périphériques et UI rafraîchies.")

    # --- Fonctions d'Affichage du Statut ---
    def update_status_display(self):
        """Met à jour le panneau de statut avec les informations actuelles des capteurs et prises."""
        logging.debug("Mise à jour de l'affichage du panneau de statut.")

        # Vider le contenu actuel du frame de statut scrollable
        for widget in self.scrollable_status_frame.winfo_children():
            widget.destroy()
        self.status_labels = {} # Réinitialiser le dictionnaire des labels de statut

        row_num = 0 # Compteur de ligne pour la grille

        # --- Affichage des Capteurs ---
        ttk.Label(self.scrollable_status_frame, text="Capteurs:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(5, 2))
        row_num += 1

        # Lire les valeurs actuelles (une seule fois pour l'affichage initial)
        try: all_temp_values = self.temp_manager.read_all_temperatures()
        except Exception: all_temp_values = {}
        try: all_light_values = self.light_manager.read_all_sensors()
        except Exception: all_light_values = {}

        # Parcourir les capteurs disponibles (déjà triés par alias dans refresh_device_lists)
        for sensor_alias, sensor_id in self.available_sensors:
            value_text, unit = "N/A", ""
            # Déterminer si c'est un capteur de température ou de lumière et récupérer sa valeur
            is_temp = sensor_id in all_temp_values
            is_light = sensor_id in all_light_values # Utiliser l'ID/adresse hexa pour la lumière

            if is_temp:
                temp_value = all_temp_values.get(sensor_id)
                value_text, unit = (f"{temp_value:.1f}", "°C") if temp_value is not None else ("Erreur", "")
            elif is_light:
                light_value = all_light_values.get(sensor_id) # Utiliser l'ID hexa ici
                value_text, unit = (f"{light_value:.0f}", " Lux") if light_value is not None else ("Erreur", "") # Afficher Lux sans décimales

            # Créer un frame pour cette ligne de capteur
            sensor_frame = ttk.Frame(self.scrollable_status_frame)
            sensor_frame.grid(row=row_num, column=0, columnspan=4, sticky='w')

            # Label pour le nom (alias)
            name_label = ttk.Label(sensor_frame, text=f"{sensor_alias}:", width=25) # Largeur fixe pour alignement
            name_label.pack(side=tk.LEFT, padx=5)
            # Label pour la valeur
            value_label = ttk.Label(sensor_frame, text=f"{value_text}{unit}", width=15) # Largeur fixe
            value_label.pack(side=tk.LEFT, padx=5)
            # Bouton pour éditer l'alias
            edit_button = ttk.Button(sensor_frame, text="✎", width=2,
                                     command=lambda s_id=sensor_id, s_name=sensor_alias: self.edit_alias_dialog('sensor', s_id, s_name))
            edit_button.pack(side=tk.LEFT, padx=2)

            # Stocker les références aux labels pour les mises à jour futures
            self.status_labels[sensor_id] = {'type': 'sensor', 'label_name': name_label, 'label_value': value_label, 'button_edit': edit_button}
            row_num += 1

        # --- Affichage des Prises Kasa ---
        ttk.Label(self.scrollable_status_frame, text="Prises Kasa:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(10, 2))
        row_num += 1

        # Parcourir les appareils Kasa triés par alias
        for mac in sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m)):
            data = self.kasa_devices[mac]
            device_alias = self.get_alias('device', mac)
            ip_address = data.get('ip', '?.?.?.?')

            # Créer un frame pour l'appareil Kasa
            device_frame = ttk.Frame(self.scrollable_status_frame)
            device_frame.grid(row=row_num, column=0, columnspan=4, sticky='w')

            # Label pour le nom de l'appareil, IP et MAC
            device_name_label = ttk.Label(device_frame, text=f"{device_alias} ({ip_address}) [{mac}]")
            device_name_label.pack(side=tk.LEFT, padx=5)
            # Bouton pour éditer l'alias de l'appareil
            device_edit_button = ttk.Button(device_frame, text="✎", width=2,
                                            command=lambda m=mac, n=device_alias: self.edit_alias_dialog('device', m, n))
            device_edit_button.pack(side=tk.LEFT, padx=2)

            # Stocker les références (on ne met pas à jour le nom de l'appareil dynamiquement ici)
            self.status_labels[mac] = {'type': 'device', 'label_name': device_name_label, 'button_edit': device_edit_button}
            row_num += 1

            # Afficher les prises de cet appareil (si disponibles)
            if mac in self.available_outlets:
                for outlet_alias, outlet_index in self.available_outlets[mac]: # Déjà trié par index
                    # Récupérer l'état partagé (lu périodiquement pendant le monitoring)
                    current_state_str = self._get_shared_kasa_state(mac, outlet_index)

                    # Si état inconnu (monitoring pas démarré?), essayer de lire depuis l'info initiale
                    if current_state_str == "Inconnu":
                         outlet_info_list = data['info'].get('outlets', [])
                         outlet_info = next((o for o in outlet_info_list if o.get('index') == outlet_index), None)
                         if outlet_info:
                             current_state_str = "ON" if outlet_info.get('is_on') else "OFF"

                    # Créer un frame pour la prise (indenté)
                    outlet_frame = ttk.Frame(self.scrollable_status_frame)
                    outlet_frame.grid(row=row_num, column=1, columnspan=3, sticky='w', padx=(20, 0)) # Indentation via column et padx

                    # Label pour le nom de la prise
                    outlet_name_label = ttk.Label(outlet_frame, text=f"└─ {outlet_alias}:", width=23) # Largeur fixe
                    outlet_name_label.pack(side=tk.LEFT, padx=5)
                    # Label pour l'état (ON/OFF/Inconnu)
                    outlet_value_label = ttk.Label(outlet_frame, text=current_state_str, width=10) # Largeur fixe
                    outlet_value_label.pack(side=tk.LEFT, padx=5)
                    # Bouton pour éditer l'alias de la prise
                    outlet_edit_button = ttk.Button(outlet_frame, text="✎", width=2,
                                                    command=lambda m=mac, i=outlet_index, n=outlet_alias: self.edit_alias_dialog('outlet', m, n, sub_id=i))
                    outlet_edit_button.pack(side=tk.LEFT, padx=2)

                    # Stocker les références aux labels pour mise à jour dynamique
                    outlet_key = f"{mac}_{outlet_index}" # Clé unique pour la prise
                    self.status_labels[outlet_key] = {'type': 'outlet', 'mac': mac, 'index': outlet_index, 'label_name': outlet_name_label, 'label_value': outlet_value_label, 'button_edit': outlet_edit_button}
                    row_num += 1

        # Mettre à jour la scrollregion du canvas de statut après ajout des éléments
        self.scrollable_status_frame.update_idletasks()
        status_canvas = self.scrollable_status_frame.master # Récupérer le canvas parent
        status_canvas.configure(scrollregion=status_canvas.bbox("all"))

    def schedule_periodic_updates(self):
        """Planifie la prochaine mise à jour de l'état live et se replanifie."""
        # Mettre à jour l'affichage immédiatement
        self.update_live_status()
        # Planifier la prochaine exécution dans 5 secondes (5000 ms)
        # Stocker l'ID du job pour pouvoir l'annuler
        self.ui_update_job = self.root.after(5000, self.schedule_periodic_updates)
        logging.debug(f"Prochaine mise à jour UI planifiée (ID: {self.ui_update_job}).")

    def cancel_periodic_updates(self):
        """Annule la mise à jour périodique de l'UI planifiée."""
        if self.ui_update_job:
            logging.debug(f"Annulation de la tâche de mise à jour UI (ID: {self.ui_update_job}).")
            try:
                self.root.after_cancel(self.ui_update_job)
            except tk.TclError as e:
                # Peut arriver si la tâche a déjà été exécutée ou annulée
                logging.warning(f"Erreur lors de l'annulation de la tâche UI {self.ui_update_job}: {e}")
            finally:
                self.ui_update_job = None # Réinitialiser l'ID

    def update_live_status(self):
        """Met à jour les labels de valeur dans le panneau de statut avec les données 'live'."""
        # Ne fait rien si le monitoring n'est pas actif (les données live ne seraient pas à jour)
        if not self.monitoring_active:
            return

        logging.debug("Mise à jour des valeurs live dans le panneau de statut...")
        # Récupérer les dernières valeurs lues par le thread de monitoring (supposées à jour)
        # Note: Ces lectures se font dans le thread principal Tkinter, pas idéal pour la performance
        # mais plus simple pour l'instant. Pourrait être optimisé en passant les données via queue.
        try: current_temps = self.temp_manager.read_all_temperatures()
        except Exception: current_temps = {}
        try: current_lights = self.light_manager.read_all_sensors()
        except Exception: current_lights = {}

        # Parcourir les labels stockés
        for item_id, data in self.status_labels.items():
             # Vérifier si le widget label existe toujours
             if 'label_value' in data and data['label_value'].winfo_exists():
                 if data['type'] == 'sensor':
                     value, unit = None, ""
                     is_temp = item_id in current_temps
                     is_light = item_id in current_lights # Utiliser l'ID hexa

                     if is_temp:
                         value, unit = current_temps.get(item_id), "°C"
                     elif is_light:
                         value, unit = current_lights.get(item_id), " Lux" # Utiliser l'ID hexa

                     # Mettre à jour le texte du label
                     data['label_value'].config(text=f"{value:.1f}{unit}" if value is not None and unit != " Lux" else f"{value:.0f}{unit}" if value is not None and unit == " Lux" else "Err/NA")

                 elif data['type'] == 'outlet':
                     # Mettre à jour l'état ON/OFF basé sur self.live_kasa_states
                     state_str = self._get_shared_kasa_state(data['mac'], data['index'])
                     data['label_value'].config(text=state_str)
             # else: # Le widget a été détruit (ex: suppression règle/appareil)
                 # On pourrait envisager de supprimer l'entrée de self.status_labels ici
                 # mais cela complique la logique si l'élément réapparaît.

    def _get_shared_kasa_state(self, mac, index):
        """Récupère l'état (ON/OFF/Inconnu) d'une prise depuis la variable partagée."""
        try:
            # Accéder à l'état stocké dans self.live_kasa_states
            is_on = self.live_kasa_states[mac][index]
            return "ON" if is_on else "OFF"
        except (AttributeError, KeyError, TypeError):
            # Si le MAC ou l'index n'existe pas, ou si live_kasa_states n'est pas initialisé
            return "Inconnu"

    # --- Gestion des Logs ---
    def update_log_display(self):
        """Vérifie la queue de logs et affiche les nouveaux messages dans la zone de texte."""
        while True:
            try:
                # Récupérer un message de la queue sans bloquer
                record = self.log_queue.get_nowait()
            except queue.Empty:
                # Si la queue est vide, arrêter de lire pour cette fois
                break
            else:
                # Si un message est récupéré:
                # Activer temporairement la zone de texte
                self.log_display.config(state=tk.NORMAL)
                # Insérer le message à la fin
                self.log_display.insert(tk.END, record + '\n')
                # Redésactiver la zone de texte
                self.log_display.config(state=tk.DISABLED)
                # Faire défiler automatiquement vers le bas pour voir le dernier message
                self.log_display.see(tk.END)
        # Planifier la prochaine vérification de la queue dans 100ms
        self.root.after(100, self.update_log_display)

    # --- Démarrage / Arrêt du Monitoring ---
    def start_monitoring(self):
        """Démarre le thread de monitoring et met à jour l'état de l'UI."""
        if self.monitoring_active:
            logging.warning("Tentative de démarrage du monitoring alors qu'il est déjà actif.")
            return

        logging.info("Démarrage du monitoring des règles...")
        self.monitoring_active = True # Mettre le flag à True

        # Mettre à jour l'état des boutons Start/Stop
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        # Désactiver les contrôles d'édition des règles
        self._set_rules_ui_state(tk.DISABLED)

        # Réinitialiser l'état connu des prises Kasa (sera lu par le thread)
        self.live_kasa_states = {}

        # Créer et démarrer le thread de monitoring
        self.monitoring_thread = threading.Thread(target=self._run_monitoring_loop, name="MonitoringThread", daemon=True)
        self.monitoring_thread.start()

        # Démarrer les mises à jour périodiques de l'UI
        self.schedule_periodic_updates()
        logging.info("Monitoring démarré.")

    def stop_monitoring(self):
        """Arrête le thread de monitoring, met à jour l'UI et éteint les prises."""
        if not self.monitoring_active:
            logging.warning("Tentative d'arrêt du monitoring alors qu'il n'est pas actif.")
            return

        logging.info("Arrêt du monitoring des règles...")
        self.monitoring_active = False # Mettre le flag à False (signal pour le thread)

        # Annuler les mises à jour périodiques de l'UI
        self.cancel_periodic_updates()

        # Attendre que le thread de monitoring se termine (avec un timeout)
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            logging.info("Attente de la fin du thread de monitoring (max 5 secondes)...")
            self.monitoring_thread.join(timeout=5.0)
            if self.monitoring_thread.is_alive():
                # Si le thread ne s'est pas arrêté à temps
                logging.warning("Le thread de monitoring n'a pas pu être arrêté dans le délai imparti.")
            else:
                logging.info("Thread de monitoring terminé proprement.")
        self.monitoring_thread = None # Réinitialiser la référence au thread
        self.asyncio_loop = None # Réinitialiser la référence à la boucle asyncio

        # Mettre à jour l'état des boutons Start/Stop
        # Utiliser 'after' pour s'assurer que c'est fait dans le thread Tkinter
        self.root.after(0, lambda: self.start_button.config(state=tk.NORMAL))
        self.root.after(0, lambda: self.stop_button.config(state=tk.DISABLED))

        # Réactiver les contrôles d'édition des règles
        self.root.after(0, lambda: self._set_rules_ui_state(tk.NORMAL))

        # Lancer l'extinction de toutes les prises Kasa en arrière-plan (sécurité)
        logging.info("Lancement de l'extinction de sécurité des prises Kasa...")
        threading.Thread(target=self._turn_off_all_kasa_safely, name="ShutdownKasaThread", daemon=True).start()

        logging.info("Processus d'arrêt du monitoring terminé.")


    def _set_rules_ui_state(self, state):
        """Active ou désactive les widgets d'édition des règles."""
        logging.debug(f"Changement de l'état des widgets de règles à: {state}")

        # --- Bouton "Ajouter une Règle" ---
        try:
            # Trouver le bouton dans le main_frame (suppose une structure spécifique)
            main_frame = self.root.winfo_children()[0]
            add_btn = next(w for w in main_frame.winfo_children() if isinstance(w, ttk.Button) and "Ajouter une Règle" in w.cget("text"))
            add_btn.config(state=state)
        except (IndexError, StopIteration, tk.TclError) as e:
            logging.warning(f"Impossible de trouver ou configurer le bouton 'Ajouter une Règle': {e}")

        # --- Widgets dans chaque règle affichée ---
        for rule_id, data in self.rule_widgets.items():
            widgets = data.get('widgets', {})
            rule_frame = data.get('frame')

            # Vérifier si le frame de la règle existe toujours
            if not rule_frame or not rule_frame.winfo_exists():
                continue # Passer à la règle suivante si celle-ci a été supprimée

            # --- Bouton Supprimer Règle (❌) ---
            try:
                # Trouver le bouton dans le name_frame (premier enfant du rule_frame)
                name_frame = rule_frame.winfo_children()[0]
                del_btn = next(w for w in name_frame.winfo_children() if isinstance(w, ttk.Button) and w.cget('text') == "❌")
                del_btn.config(state=state)
            except (IndexError, StopIteration, tk.TclError) as e:
                logging.warning(f"Impossible de trouver ou configurer le bouton Supprimer pour règle {rule_id}: {e}")

            # --- Boutons d'édition (Nom, SI, JUSQU'À) ---
            for btn_key in ['edit_name_button', 'edit_si_button', 'edit_until_button']:
                if btn_key in widgets:
                    try:
                        # Accéder au widget bouton via sa clé dans le dictionnaire
                        button_widget = widgets[btn_key]
                        if button_widget.winfo_exists(): # Vérifier si le widget existe encore
                           button_widget.config(state=state)
                    except tk.TclError:
                        # Ignorer si le widget a été détruit entre-temps
                        pass
                    except KeyError:
                         # Si la clé n'existe pas dans widgets (ne devrait pas arriver si bien initialisé)
                         logging.warning(f"Clé widget '{btn_key}' non trouvée pour règle {rule_id} lors du changement d'état.")


            # --- Widgets ALORS (Combobox Kasa, Prise, Action) ---
            for w_key in ['kasa_combo', 'outlet_combo', 'action_combo']:
                 if w_key in widgets:
                     try:
                         combo_widget = widgets[w_key]
                         if combo_widget.winfo_exists():
                             # Mettre 'readonly' si on active, 'disabled' si on désactive
                             combo_widget.config(state='readonly' if state == tk.NORMAL else tk.DISABLED)
                     except tk.TclError:
                         pass # Ignorer si détruit
                     except KeyError:
                         logging.warning(f"Clé widget '{w_key}' non trouvée pour règle {rule_id} lors du changement d'état.")


    def _run_monitoring_loop(self):
        """Point d'entrée pour le thread de monitoring, gère la boucle asyncio."""
        try:
            # Essayer de récupérer/créer une boucle d'événements asyncio pour ce thread
            try:
                self.asyncio_loop = asyncio.get_event_loop()
            except RuntimeError:
                self.asyncio_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.asyncio_loop)

            logging.info("Boucle d'événements asyncio démarrée pour le monitoring.")
            # Lancer la tâche principale de monitoring dans la boucle asyncio
            self.asyncio_loop.run_until_complete(self._async_monitoring_task())

        except Exception as e:
            # Capturer toute erreur critique dans la boucle asyncio
            logging.critical(f"Erreur fatale dans la boucle de monitoring asyncio: {e}", exc_info=True)
        finally:
            logging.info("Boucle de monitoring asyncio terminée.")
            # Si le monitoring est toujours marqué comme actif (ex: erreur), déclencher l'arrêt
            if self.monitoring_active:
                logging.warning("Arrêt du monitoring déclenché suite à la fin anormale de la boucle asyncio.")
                # Planifier l'appel à stop_monitoring dans le thread principal Tkinter
                self.root.after(0, self.stop_monitoring)

    async def _update_live_kasa_states_task(self):
        """Tâche asynchrone pour lire l'état actuel de toutes les prises Kasa."""
        logging.debug("[MONITORING] Début màj états Kasa live...") # DEBUG Log
        new_states = {} # Dictionnaire pour stocker les états lus {mac: {index: bool}}

        # Créer une liste de tâches pour lire l'état de chaque appareil Kasa en parallèle
        tasks = []
        for mac, device_data in self.kasa_devices.items():
             # Vérifier si c'est bien une prise ou multiprise avant d'essayer de lire l'état
             if device_data['info'].get('is_strip') or device_data['info'].get('is_plug'):
                 tasks.append(self._fetch_one_kasa_state(mac, device_data['controller']))
             # else: On pourrait logger qu'on ignore un appareil non contrôlable (ex: ampoule)

        if not tasks:
             logging.debug("[MONITORING] Aucun appareil Kasa contrôlable trouvé pour màj état.") # DEBUG Log
             self.live_kasa_states = {} # Vider l'état si aucun appareil
             return

        # Exécuter les tâches en parallèle et récupérer les résultats
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Traiter les résultats
        successful_reads = 0
        for res in results:
            if isinstance(res, Exception):
                # Logguer l'erreur mais continuer avec les autres résultats
                logging.error(f"[MONITORING] Erreur lecture état Kasa: {res}") # ERROR Log
            elif isinstance(res, dict) and res: # Vérifier que c'est un dict non vide
                new_states.update(res) # Fusionner les états lus {mac: {index: bool}}
                successful_reads += 1

        # Mettre à jour l'état partagé
        self.live_kasa_states = new_states
        logging.debug(f"[MONITORING] États Kasa live màj: {successful_reads}/{len(tasks)} appareils lus OK.") # DEBUG Log

    async def _fetch_one_kasa_state(self, mac, controller):
        """Tâche asynchrone pour lire l'état des prises d'un seul appareil Kasa."""
        try:
            # Assurer la connexion (peut impliquer une reconnexion si nécessaire)
            await controller._connect() # Note: Utilisation d'une méthode "privée"

            # Vérifier si la connexion/mise à jour a réussi
            if controller._device: # Accès à l'attribut "privé"
                outlet_states = await controller.get_outlet_state()
                if outlet_states is not None:
                    states_dict = {
                        outlet['index']: outlet['is_on']
                        for outlet in outlet_states
                        if 'index' in outlet and 'is_on' in outlet
                    }
                    return {mac: states_dict}
                else:
                    logging.warning(f"[MONITORING] État Kasa None pour {self.get_alias('device', mac)} ({mac}).") # WARNING Log
            else:
                logging.warning(f"[MONITORING] Échec connexion/màj Kasa pour {self.get_alias('device', mac)} ({mac}).") # WARNING Log
        except Exception as e:
            logging.error(f"[MONITORING] Erreur fetch état Kasa {self.get_alias('device', mac)} ({mac}): {e}") # ERROR Log
            raise e
        return {}

    # --- Logique d'Évaluation des Règles (Coeur du Monitoring) ---
    async def _async_monitoring_task(self):
        """Tâche asynchrone principale qui évalue les règles et contrôle les prises."""
        active_until_rules = {}
        last_kasa_update = datetime.min
        kasa_update_interval = timedelta(seconds=10)

        logging.info("Début de la boucle de monitoring principale.")

        while self.monitoring_active:
            now_dt = datetime.now()
            now_time = now_dt.time()
            logging.debug(f"--- Cycle Mon {now_dt:%Y-%m-%d %H:%M:%S} ---") # DEBUG Log

            # --- 1. Lecture des Capteurs ---
            current_sensor_values = {}
            try:
                temp_values = await self.asyncio_loop.run_in_executor(None, self.temp_manager.read_all_temperatures)
                light_values = await self.asyncio_loop.run_in_executor(None, self.light_manager.read_all_sensors)
                current_sensor_values = {k: v for k, v in {**temp_values, **light_values}.items() if v is not None}
                # *** DEBUG PRINT: Show sensor values read this cycle ***
                logging.debug(f"[MONITORING] Valeurs capteurs lues: {current_sensor_values}") # DEBUG Log
            except Exception as e:
                logging.error(f"[MONITORING] Erreur lecture capteurs: {e}") # ERROR Log

            # --- 2. Mise à jour des états Kasa ---
            if now_dt - last_kasa_update >= kasa_update_interval:
                try:
                    # *** DEBUG PRINT: Show Kasa states *before* update ***
                    logging.debug(f"[MONITORING] États Kasa avant màj: {self.live_kasa_states}") # DEBUG Log
                    await self._update_live_kasa_states_task()
                    last_kasa_update = now_dt
                    # *** DEBUG PRINT: Show Kasa states *after* update ***
                    logging.debug(f"[MONITORING] États Kasa après màj: {self.live_kasa_states}") # DEBUG Log
                except Exception as e:
                    logging.error(f"[MONITORING] Échec màj Kasa: {e}") # ERROR Log
            # else:
                 # logging.debug("[MONITORING] Skipping Kasa state update (interval not reached).") # DEBUG Log


            # --- 3. Évaluation des Règles ---
            tasks_to_run = []
            rules_to_evaluate = list(self.rules)
            desired_outlet_states = {}
            active_until_copy = dict(active_until_rules)

            # --- 3a. Évaluation des conditions JUSQU'À actives ---
            logging.debug(f"[MONITORING] Éval UNTIL - Règles actives: {list(active_until_copy.keys())}") # DEBUG Log
            for rule_id, until_info in active_until_copy.items():
                rule = next((r for r in rules_to_evaluate if r.get('id') == rule_id), None)
                if not rule:
                    logging.warning(f"[MONITORING] R{rule_id} (UNTIL): Règle non trouvée. Annulation.") # WARNING Log
                    del active_until_rules[rule_id]
                    continue

                mac = rule.get('target_device_mac')
                idx = rule.get('target_outlet_index')
                if mac is None or idx is None:
                     logging.warning(f"[MONITORING] R{rule_id} (UNTIL): Cible invalide. Annulation.") # WARNING Log
                     del active_until_rules[rule_id]
                     continue

                outlet_key = (mac, idx)
                until_logic = rule.get('until_logic', 'OU')
                until_conditions = rule.get('until_conditions', [])

                if not until_conditions:
                    logging.debug(f"[MONITORING] R{rule_id} (UNTIL): Aucune condition. Désactivation.") # DEBUG Log
                    del active_until_rules[rule_id]
                    continue

                until_condition_met = False
                condition_that_met_until = None # *** DEBUG: Store which condition met UNTIL ***
                if until_logic == 'ET':
                    all_true = True
                    if not until_conditions: all_true = False
                    else:
                        for cond in until_conditions:
                            cond_result = self._check_condition(cond, current_sensor_values, now_time)
                            if not cond_result:
                                all_true = False
                                logging.debug(f"[MONITORING] R{rule_id} UNTIL(ET) échoue sur CondID:{cond.get('condition_id','N/A')}") # DEBUG Log
                                break
                    until_condition_met = all_true
                    if until_condition_met: condition_that_met_until = "Toutes (ET)" # *** DEBUG ***
                elif until_logic == 'OU':
                    any_true = False
                    for cond in until_conditions:
                         cond_result = self._check_condition(cond, current_sensor_values, now_time)
                         if cond_result:
                            any_true = True
                            condition_that_met_until = cond.get('condition_id','N/A') # *** DEBUG ***
                            logging.debug(f"[MONITORING] R{rule_id} UNTIL(OU) réussit sur CondID:{condition_that_met_until}") # DEBUG Log
                            break
                    until_condition_met = any_true
                else:
                    logging.error(f"[MONITORING] R{rule_id}: Logique UNTIL inconnue '{until_logic}'.") # ERROR Log
                    until_condition_met = False

                if until_condition_met:
                    revert_action = until_info['revert_action']
                    # *** DEBUG PRINT: Show UNTIL met details ***
                    logging.info(f"[MONITORING] R{rule_id}: Condition JUSQU'À ({until_logic}) REMPLIE (par CondID: {condition_that_met_until}). Action retour: {revert_action}. Capteurs: {current_sensor_values}") # INFO Log
                    desired_outlet_states[outlet_key] = revert_action
                    if rule_id in active_until_rules: # Check before deleting
                        del active_until_rules[rule_id]

            # --- 3b. Évaluation des conditions SI ---
            logging.debug(f"[MONITORING] Éval SI - Règles à évaluer: {len(rules_to_evaluate)}") # DEBUG Log
            for rule in rules_to_evaluate:
                rule_id = rule.get('id')
                mac = rule.get('target_device_mac')
                idx = rule.get('target_outlet_index')
                action = rule.get('action')

                if not rule_id or mac is None or idx is None or not action:
                    continue

                outlet_key = (mac, idx)

                # Check if state already set by UNTIL this cycle
                if outlet_key in desired_outlet_states and rule_id not in active_until_rules: # Ensure it wasn't this rule's UNTIL
                     logging.debug(f"[MONITORING] R{rule_id}: Éval SI skip (état déjà fixé par UNTIL pour {outlet_key}).") # DEBUG Log
                     continue

                # Check if this rule is waiting for its OWN UNTIL condition
                if rule_id in active_until_rules:
                     logging.debug(f"[MONITORING] R{rule_id}: Éval SI skip (règle en attente UNTIL).") # DEBUG Log
                     continue

                trigger_logic = rule.get('trigger_logic', 'ET')
                trigger_conditions = rule.get('conditions', [])

                if not trigger_conditions:
                    continue

                trigger_condition_met = False
                condition_that_met_trigger = None # *** DEBUG: Store which condition met SI ***
                if trigger_logic == 'ET':
                    all_true=True
                    if not trigger_conditions: all_true=False
                    else:
                        for cond in trigger_conditions:
                            cond_result = self._check_condition(cond, current_sensor_values, now_time)
                            if not cond_result:
                                all_true=False
                                logging.debug(f"[MONITORING] R{rule_id} SI(ET) échoue sur CondID:{cond.get('condition_id','N/A')}") # DEBUG Log
                                break
                    trigger_condition_met = all_true
                    if trigger_condition_met: condition_that_met_trigger = "Toutes (ET)" # *** DEBUG ***
                elif trigger_logic == 'OU':
                    any_true=False
                    for cond in trigger_conditions:
                        cond_result = self._check_condition(cond, current_sensor_values, now_time)
                        if cond_result:
                            any_true=True
                            condition_that_met_trigger = cond.get('condition_id','N/A') # *** DEBUG ***
                            logging.debug(f"[MONITORING] R{rule_id} SI(OU) réussit sur CondID:{condition_that_met_trigger}") # DEBUG Log
                            break
                    trigger_condition_met = any_true
                else:
                    logging.error(f"[MONITORING] R{rule_id}: Logique SI inconnue '{trigger_logic}'.") # ERROR Log
                    trigger_condition_met = False

                if trigger_condition_met:
                    # *** DEBUG PRINT: Show SI met details ***
                    logging.info(f"[MONITORING] R{rule_id}: Condition SI ({trigger_logic}) REMPLIE (par CondID: {condition_that_met_trigger}). Action désirée: {action}. Capteurs: {current_sensor_values}") # INFO Log
                    if outlet_key not in desired_outlet_states:
                         desired_outlet_states[outlet_key] = action
                         if rule.get('until_conditions') and rule_id not in active_until_rules:
                             revert_action = 'OFF' if action == 'ON' else 'ON'
                             # *** DEBUG PRINT: Show UNTIL activation ***
                             logging.info(f"[MONITORING] R{rule_id}: Activation JUSQU'À ({rule.get('until_logic','OU')}). Action retour: {revert_action}.") # INFO Log
                             active_until_rules[rule_id] = {'revert_action': revert_action}
                    else:
                         # *** DEBUG PRINT: Potential conflict ***
                         logging.warning(f"[MONITORING] R{rule_id}: Conflit potentiel? État pour {outlet_key} déjà défini à {desired_outlet_states[outlet_key]} (par UNTIL ou autre SI?). Action {action} ignorée.") # WARNING Log


            # --- 4. Application des changements Kasa ---
            # *** DEBUG PRINT: Show final desired states before applying ***
            logging.debug(f"[MONITORING] États Kasa désirés finaux pour ce cycle: {desired_outlet_states}") # DEBUG Log
            all_managed_outlets = set(
                (r.get('target_device_mac'), r.get('target_outlet_index'))
                for r in rules_to_evaluate
                if r.get('target_device_mac') is not None and r.get('target_outlet_index') is not None
            )

            # --- 4a. Appliquer les états désirés explicites ---
            for outlet_key, desired_state in desired_outlet_states.items():
                mac, idx = outlet_key
                current_live_state = self.live_kasa_states.get(mac, {}).get(idx)

                action_needed = False
                kasa_function_name = None

                if desired_state == 'ON' and current_live_state is not True:
                    action_needed = True
                    kasa_function_name = 'turn_outlet_on'
                elif desired_state == 'OFF' and current_live_state is not False:
                    action_needed = True
                    kasa_function_name = 'turn_outlet_off'

                if action_needed:
                    if mac in self.kasa_devices:
                        controller = self.kasa_devices[mac]['controller']
                        # *** DEBUG PRINT: Show explicit action being taken ***
                        logging.info(f"[ACTION KASA] Explicite: {self.get_alias('device', mac)} / {self.get_alias('outlet', mac, idx)} -> {desired_state} (État live avant: {current_live_state})") # INFO Log
                        tasks_to_run.append(getattr(controller, kasa_function_name)(idx))
                        self.live_kasa_states.setdefault(mac, {})[idx] = (desired_state == 'ON')
                    else:
                        logging.error(f"[ACTION KASA] Erreur: Appareil Kasa {mac} non trouvé pour action {desired_state}.") # ERROR Log
                # else:
                     # logging.debug(f"[ACTION KASA] Aucune action explicite requise pour {outlet_key}, état déjà {desired_state}.") # DEBUG Log

            # --- 4b. Gérer les prises non explicitement désirées (implicitement OFF) ---
            live_outlets_to_check = []
            for mac, outlets in self.live_kasa_states.items():
                 for idx, is_on in outlets.items():
                      live_outlets_to_check.append(((mac, idx), is_on))

            for outlet_key, is_on in live_outlets_to_check:
                if outlet_key in all_managed_outlets and outlet_key not in desired_outlet_states:
                    if is_on:
                        mac, idx = outlet_key
                        if mac in self.kasa_devices:
                            controller = self.kasa_devices[mac]['controller']
                            # *** DEBUG PRINT: Show implicit action being taken ***
                            logging.info(f"[ACTION KASA] Implicite: {self.get_alias('device', mac)} / {self.get_alias('outlet', mac, idx)} -> OFF (non désirée explicitement ce cycle)") # INFO Log
                            tasks_to_run.append(controller.turn_outlet_off(idx))
                            self.live_kasa_states.setdefault(mac, {})[idx] = False
                        else:
                            logging.error(f"[ACTION KASA] Erreur: Appareil Kasa {mac} non trouvé pour action OFF implicite.") # ERROR Log

            # --- 5. Exécuter les tâches Kasa ---
            if tasks_to_run:
                logging.debug(f"[MONITORING] Exécution de {len(tasks_to_run)} tâches Kasa...") # DEBUG Log
                try:
                    results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                    for i, res in enumerate(results):
                        if isinstance(res, Exception):
                             logging.error(f"[MONITORING] Erreur tâche Kasa (index {i}): {res}") # ERROR Log
                except Exception as e_gather:
                     logging.error(f"[MONITORING] Erreur gather Kasa: {e_gather}") # ERROR Log
                logging.debug("[MONITORING] Tâches Kasa du cycle terminées.") # DEBUG Log
            # else:
                 # logging.debug("[MONITORING] Aucune action Kasa à exécuter ce cycle.") # DEBUG Log

            # --- 6. Attente avant le prochain cycle ---
            await asyncio.sleep(2)

        logging.info("Sortie de la boucle de monitoring principale.") # INFO Log


    # --- Fonction de Vérification de Condition ---
    # Add DEBUG logs inside _check_condition as well
    def _check_condition(self, condition_data, current_sensor_values, current_time_obj):
        """Évalue une condition unique (Capteur ou Heure)."""
        cond_type = condition_data.get('type')
        operator = condition_data.get('operator')
        cond_id_log = condition_data.get('condition_id', 'N/A')

        if not cond_type or not operator:
            logging.warning(f"[COND CHECK] Cond invalide (ID:{cond_id_log}): manque type/op - {condition_data}") # WARNING Log
            return False

        try:
            if cond_type == 'Capteur':
                sensor_id = condition_data.get('id')
                threshold = condition_data.get('threshold')

                if sensor_id is None or threshold is None or operator not in SENSOR_OPERATORS:
                    logging.warning(f"[COND CHECK] Cond Capteur invalide (ID:{cond_id_log}): {condition_data}") # WARNING Log
                    return False

                if sensor_id not in current_sensor_values:
                    logging.debug(f"[COND CHECK] (ID:{cond_id_log}): Valeur manquante pour capteur {self.get_alias('sensor', sensor_id)} ({sensor_id})") # DEBUG Log
                    return False

                current_value = current_sensor_values[sensor_id]
                # *** DEBUG PRINT: Show sensor condition check ***
                logging.debug(f"[COND CHECK] Eval Capteur (ID:{cond_id_log}): '{self.get_alias('sensor', sensor_id)}' ({current_value}) {operator} {threshold} ?") # DEBUG Log
                result = self._compare(current_value, operator, float(threshold))
                logging.debug(f"[COND CHECK] -> Résultat (ID:{cond_id_log}): {result}") # DEBUG Log
                return result

            elif cond_type == 'Heure':
                time_str = condition_data.get('value')

                if not time_str or operator not in TIME_OPERATORS:
                    logging.warning(f"[COND CHECK] Cond Heure invalide (ID:{cond_id_log}): {condition_data}") # WARNING Log
                    return False
                try:
                    target_time = datetime.strptime(time_str, '%H:%M').time()
                except ValueError:
                    logging.error(f"[COND CHECK] Format heure invalide (ID:{cond_id_log}): '{time_str}'") # ERROR Log
                    return False

                # *** DEBUG PRINT: Show time condition check ***
                logging.debug(f"[COND CHECK] Eval Heure (ID:{cond_id_log}): {current_time_obj:%H:%M:%S} {operator} {target_time:%H:%M} ?") # DEBUG Log
                if operator == '<': result = current_time_obj < target_time
                elif operator == '>': result = current_time_obj > target_time
                elif operator == '<=': result = current_time_obj <= target_time
                elif operator == '>=': result = current_time_obj >= target_time
                else:
                    current_minutes = current_time_obj.hour * 60 + current_time_obj.minute
                    target_minutes = target_time.hour * 60 + target_time.minute
                    if operator == '=': result = current_minutes == target_minutes
                    elif operator == '!=': result = current_minutes != target_minutes
                    else: result = False

                logging.debug(f"[COND CHECK] -> Résultat (ID:{cond_id_log}): {result}") # DEBUG Log
                return result
            else:
                logging.error(f"[COND CHECK] Type cond inconnu (ID:{cond_id_log}): {cond_type}") # ERROR Log
                return False
        except ValueError as e:
            logging.error(f"[COND CHECK] Erreur valeur (ID:{cond_id_log}) - {condition_data}: {e}") # ERROR Log
            return False
        except Exception as e:
            logging.error(f"[COND CHECK] Erreur eval cond (ID:{cond_id_log}) - {condition_data}: {e}", exc_info=True) # ERROR Log
            return False

    # --- Fonction de Comparaison Numérique ---
    def _compare(self, value1, operator, value2):
        """Effectue une comparaison numérique entre deux valeurs."""
        try:
            v1 = float(value1)
            v2 = float(value2)
            # logging.debug(f"Comparaison Numérique: {v1} {operator} {v2}") # Keep this commented unless very detailed debug needed

            if operator == '<': return v1 < v2
            elif operator == '>': return v1 > v2
            elif operator == '=': return abs(v1 - v2) < 1e-9
            elif operator == '!=': return abs(v1 - v2) >= 1e-9
            elif operator == '<=': return v1 <= v2
            elif operator == '>=': return v1 >= v2
            else:
                logging.warning(f"Opérateur comparaison numérique inconnu: {operator}") # WARNING Log
                return False
        except (ValueError, TypeError) as e:
            logging.error(f"Erreur comp num: impossible de convertir '{value1}' ou '{value2}'. Op: {operator}. Err: {e}") # ERROR Log
            return False

    # --- Fonctions d'Extinction / Sauvegarde / Fermeture ---
    def _turn_off_all_kasa_safely(self):
        """Lance l'extinction de toutes les prises Kasa dans une boucle asyncio."""
        logging.info("Tentative d'extinction sécurisée de toutes les prises Kasa...") # INFO Log
        try:
            # Essayer d'obtenir/créer une boucle asyncio et exécuter la tâche d'extinction
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                     future = asyncio.run_coroutine_threadsafe(self._async_turn_off_all(), loop)
                     future.result(timeout=15)
                else:
                     loop.run_until_complete(self._async_turn_off_all())
            except RuntimeError:
                logging.info("Aucune boucle asyncio existante, utilisation de asyncio.run pour l'extinction.") # INFO Log
                asyncio.run(self._async_turn_off_all())
        except asyncio.TimeoutError:
             logging.error("Timeout dépassé lors de l'attente de l'extinction des prises Kasa.") # ERROR Log
        except Exception as e:
            logging.error(f"Erreur inattendue lors de l'extinction sécurisée des prises Kasa: {e}", exc_info=True) # ERROR Log

    async def _async_turn_off_all(self):
        """Tâche asynchrone pour éteindre toutes les prises de tous les appareils Kasa connus."""
        tasks = {}
        logging.info(f"Préparation des tâches d'extinction pour {len(self.kasa_devices)} appareils Kasa...") # INFO Log

        for mac, device_data in self.kasa_devices.items():
            controller = device_data['controller']
            device_alias = self.get_alias('device', mac)
            task_key = f"{device_alias} ({mac})"

            if device_data['info'].get('is_strip') or device_data['info'].get('is_plug'):
                logging.debug(f"Ajout tâche extinction pour: {task_key}") # DEBUG Log
                tasks[task_key] = controller.turn_all_outlets_off()
            else:
                tasks[task_key] = asyncio.sleep(0)

        if tasks:
            logging.info(f"Exécution de {len(tasks)} tâches d'extinction Kasa en parallèle...") # INFO Log
            task_keys = list(tasks.keys())
            task_coroutines = list(tasks.values())
            results = await asyncio.gather(*task_coroutines, return_exceptions=True)

            success_count = 0
            failure_count = 0
            for i, result in enumerate(results):
                key = task_keys[i]
                if isinstance(result, Exception):
                    logging.error(f"Erreur lors de l'extinction de '{key}': {result}") # ERROR Log
                    failure_count += 1
                else:
                    # Check if the task was a real turn_off or just sleep(0)
                    original_coro = task_coroutines[i]
                    # Check if it's a coroutine and its name is 'sleep'
                    is_sleep_task = asyncio.iscoroutine(original_coro) and getattr(original_coro, '__name__', '') == 'sleep'
                    if not is_sleep_task:
                         # Only log success for actual turn_off tasks if needed
                         # logging.debug(f"Extinction réussie pour '{key}'.") # Optional DEBUG Log
                         pass
                    success_count += 1


            logging.info(f"Extinction Kasa terminée. Tâches complétées: {success_count}, Échecs: {failure_count}.") # INFO Log
        else:
            logging.info("Aucun appareil Kasa de type prise/multiprise trouvé à éteindre.") # INFO Log

    def save_configuration(self):
        """Sauvegarde la configuration actuelle (alias et règles) dans le fichier YAML."""
        logging.info("Préparation de la sauvegarde de la configuration...") # INFO Log

        for rule_id in list(self.rule_widgets.keys()):
             if rule_id in self.rule_widgets:
                 try:
                     self.on_rule_change(rule_id)
                 except Exception as e:
                     logging.error(f"Erreur on_rule_change avant save pour règle {rule_id}: {e}") # ERROR Log

        config_to_save = {
            "aliases": self.aliases,
            "rules": self.rules
        }
        logging.debug(f"Données préparées pour la sauvegarde: {config_to_save}") # DEBUG Log

        if save_config(config_to_save, DEFAULT_CONFIG_FILE):
            logging.info(f"Configuration sauvegardée avec succès dans {DEFAULT_CONFIG_FILE}.") # INFO Log
            messagebox.showinfo("Sauvegarde", "Configuration sauvegardée avec succès.", parent=self.root)
        else:
            messagebox.showerror("Sauvegarde Échouée", "Une erreur est survenue lors de la sauvegarde. Vérifiez les logs.", parent=self.root)

    def on_closing(self):
        """Gère l'événement de fermeture de la fenêtre principale."""
        if self.monitoring_active:
            if messagebox.askyesno("Quitter l'Application",
                                   "Le monitoring est actif.\n\nVoulez-vous arrêter et quitter ?",
                                   parent=self.root):
                logging.info("Arrêt monitoring & fermeture demandés...") # INFO Log
                self.stop_monitoring()
                logging.info("Fermeture app dans 1 sec...") # INFO Log
                self.root.after(1000, self.root.destroy)
            else:
                logging.debug("Fermeture annulée (monitoring actif).") # DEBUG Log
                return
        else:
            if messagebox.askyesno("Quitter l'Application",
                                   "Êtes-vous sûr de vouloir quitter ?",
                                   parent=self.root):
                logging.info("Fermeture demandée (monitoring inactif)...") # INFO Log
                logging.info("Lancement extinction Kasa...") # INFO Log
                threading.Thread(target=self._turn_off_all_kasa_safely, daemon=True).start()
                logging.info("Fermeture app dans 1 sec...") # INFO Log
                self.root.after(1000, self.root.destroy)
            else:
                logging.debug("Fermeture annulée (monitoring inactif).") # DEBUG Log


# --- Point d'Entrée Principal ---
if __name__ == "__main__":
    # Configure logging to show DEBUG messages
    # Make sure logger_setup.py *also* allows DEBUG level if you are using it
    # Add filename and line number to log format for easier debugging
    log_format = '%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s'
    logging.basicConfig(level=logging.DEBUG, format=log_format)

    # If using logger_setup.py, ensure it's configured for DEBUG level as well.
    # The basicConfig call here might be overridden by logger_setup if it also configures the root logger.
    # It's generally better to configure logging in one place (either here or in logger_setup).

    root = tk.Tk()
    # Pass the log queue if logger_setup expects it and basicConfig isn't used for the final handler
    # app = GreenhouseApp(root) # Assuming logger_setup handles queue integration

    # If NOT using logger_setup and relying solely on basicConfig + QueueHandler:
    # log_queue_main = queue.Queue()
    # queue_handler = logging.handlers.QueueHandler(log_queue_main)
    # logging.getLogger().addHandler(queue_handler)
    # # Modify GreenhouseApp.__init__ to accept and use log_queue_main if needed
    # app = GreenhouseApp(root) # Or app = GreenhouseApp(root, log_queue_main)

    # Assuming logger_setup handles the queue properly:
    app = GreenhouseApp(root)

    root.mainloop()
