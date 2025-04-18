# greenhouse_app.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, font as tkFont
import asyncio
import threading
import queue
import logging
import uuid
from datetime import datetime, time, timedelta
import re # Pour la validation de l'heure

# Importer les modules personnalisés
from logger_setup import setup_logging
from discover_device import DeviceDiscoverer # Votre classe
from device_control import DeviceController   # Votre classe
from temp_sensor_wrapper import TempSensorManager
from light_sensor import BH1750Manager # Votre classe
from config_manager import load_config, save_config

# --- Constantes ---
OPERATORS = ['<', '>', '=', '!=', '<=', '>=']
TIME_OPERATORS = ['<', '>', '=', '!=', '<=', '>=']
SENSOR_OPERATORS = ['<', '>', '=', '!=', '<=', '>=']
ACTIONS = ['ON', 'OFF']
LOGIC_OPERATORS = ['ET', 'OU'] # 'AND', 'OR'
CONDITION_TYPES = ['Capteur', 'Heure']
DEFAULT_CONFIG_FILE = 'config.yaml'
# Format pour la validation de l'heure
TIME_REGEX = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$') # HH:MM (00:00 - 23:59)

#--------------------------------------------------------------------------
# CLASSE POUR L'ÉDITEUR DE CONDITIONS (POP-UP)
#--------------------------------------------------------------------------
class ConditionEditor(simpledialog.Dialog):

    def __init__(self, parent, title, rule_id, condition_type,
                 initial_logic, initial_conditions, available_sensors, app_instance):
        self.rule_id = rule_id
        self.condition_type = condition_type # 'trigger' or 'until'
        self.initial_logic = initial_logic if initial_logic in LOGIC_OPERATORS else LOGIC_OPERATORS[0]
        # Faire une copie profonde pour éviter de modifier l'original accidentellement
        self.initial_conditions = [cond.copy() for cond in initial_conditions]
        self.available_sensors = available_sensors # [(name, id), ...]
        self.app = app_instance # Référence à l'instance GreenhouseApp pour callback

        # Pour stocker les widgets de chaque ligne de condition
        self.condition_lines = [] # Liste de dictionnaires {'frame': ttk.Frame, 'widgets': dict, 'condition_id': str}

        # Pour stocker le résultat
        self.result_logic = None
        self.result_conditions = None

        super().__init__(parent, title=title)


    def body(self, master):
        """Crée le contenu du corps de la boîte de dialogue."""
        dialog_frame = ttk.Frame(master, padding="10")
        dialog_frame.pack(fill=tk.BOTH, expand=True)

        # --- Logique Globale (ET/OU) ---
        logic_frame = ttk.Frame(dialog_frame)
        logic_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 10))
        ttk.Label(logic_frame, text="Logique entre conditions:").pack(side=tk.LEFT, padx=(0, 5))
        self.logic_var = tk.StringVar(value=self.initial_logic)
        self.logic_combo = ttk.Combobox(logic_frame, textvariable=self.logic_var, values=LOGIC_OPERATORS, state="readonly", width=5)
        self.logic_combo.pack(side=tk.LEFT)

        # --- Zone Scrollable pour les Conditions ---
        conditions_container = ttk.Frame(dialog_frame)
        conditions_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.conditions_canvas = tk.Canvas(conditions_container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(conditions_container, orient="vertical", command=self.conditions_canvas.yview)
        self.scrollable_conditions_frame = ttk.Frame(self.conditions_canvas) # Frame interne

        self.scrollable_conditions_frame.bind(
            "<Configure>",
            lambda e: self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))
        )

        self.conditions_canvas.create_window((0, 0), window=self.scrollable_conditions_frame, anchor="nw")
        self.conditions_canvas.configure(yscrollcommand=scrollbar.set)

        self.conditions_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Peupler les conditions initiales
        if not self.initial_conditions:
             # Ajouter une ligne vide si aucune condition n'existe
             self._add_condition_line()
        else:
            for condition_data in self.initial_conditions:
                self._add_condition_line(condition_data)

        # --- Bouton Ajouter Condition ---
        add_button_frame = ttk.Frame(dialog_frame)
        add_button_frame.pack(side=tk.TOP, fill=tk.X, pady=(10, 0))
        add_button = ttk.Button(add_button_frame, text="➕ Ajouter Condition", command=self._add_condition_line)
        add_button.pack()

        # Ajuster la taille initiale du pop-up (peut nécessiter ajustement)
        self.geometry("750x450") # Largeur x Hauteur

        # Centrer le canvas initialement
        self.scrollable_conditions_frame.update_idletasks()
        self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))

        # Retourner le widget qui doit avoir le focus initial (optionnel)
        # return self.logic_combo

    def _add_condition_line(self, condition_data=None):
        """Ajoute une ligne de widgets pour une condition."""
        line_frame = ttk.Frame(self.scrollable_conditions_frame, padding=2)
        line_frame.pack(fill=tk.X, expand=True, pady=1)

        widgets = {}
        condition_id = condition_data.get('condition_id', str(uuid.uuid4())) if condition_data else str(uuid.uuid4())

        # --- Type (Capteur/Heure) ---
        widgets['type_var'] = tk.StringVar()
        widgets['type_combo'] = ttk.Combobox(line_frame, textvariable=widgets['type_var'], values=CONDITION_TYPES, state="readonly", width=8)
        widgets['type_combo'].pack(side=tk.LEFT, padx=2)
        # Lier le changement de type pour adapter l'UI de la ligne
        widgets['type_combo'].bind('<<ComboboxSelected>>', lambda e, lw=widgets, lf=line_frame: self._on_condition_type_change(lw, lf))

        # --- Capteur (si type='Capteur') ---
        widgets['sensor_var'] = tk.StringVar()
        widgets['sensor_combo'] = ttk.Combobox(line_frame, textvariable=widgets['sensor_var'],
                                                values=[name for name, _id in self.available_sensors], state="disabled", width=20)
        widgets['sensor_combo'].pack(side=tk.LEFT, padx=2)

        # --- Opérateur ---
        widgets['operator_var'] = tk.StringVar()
        widgets['operator_combo'] = ttk.Combobox(line_frame, textvariable=widgets['operator_var'], values=OPERATORS, state="readonly", width=4)
        widgets['operator_combo'].pack(side=tk.LEFT, padx=2)

        # --- Valeur (pour Capteur ou Heure) ---
        widgets['value_var'] = tk.StringVar()
        # Mettre une largeur plus grande pour HH:MM ou floats
        widgets['value_entry'] = ttk.Entry(line_frame, textvariable=widgets['value_var'], width=10)
        widgets['value_entry'].pack(side=tk.LEFT, padx=2)

        # --- Bouton Supprimer Ligne ---
        # Utiliser une sous-fonction lambda pour capturer le line_frame actuel
        delete_button = ttk.Button(line_frame, text="➖", width=2, style="Red.TButton",
                                    command=lambda frame=line_frame: self._delete_condition_line(frame))
        delete_button.pack(side=tk.RIGHT, padx=5)

        # Stocker les infos de la ligne
        line_info = {'frame': line_frame, 'widgets': widgets, 'condition_id': condition_id}
        self.condition_lines.append(line_info)

        # Peupler si des données existent
        if condition_data:
            cond_type = condition_data.get('type')
            widgets['type_var'].set(cond_type if cond_type in CONDITION_TYPES else '')
            widgets['operator_var'].set(condition_data.get('operator', ''))

            if cond_type == 'Capteur':
                sensor_id = condition_data.get('id')
                sensor_name = self.app.get_alias('sensor', sensor_id) if sensor_id else ''
                widgets['sensor_var'].set(sensor_name)
                widgets['value_var'].set(str(condition_data.get('threshold', '')))
                # Activer le combobox capteur
                widgets['sensor_combo'].config(state="readonly")
                widgets['operator_combo'].config(values=SENSOR_OPERATORS) # S'assurer que les bons opérateurs sont là
                # Configurer l'entry pour nombres
                # (Pourrait ajouter validation numérique ici si souhaité)

            elif cond_type == 'Heure':
                widgets['value_var'].set(condition_data.get('value', ''))
                # Désactiver le combobox capteur
                widgets['sensor_combo'].config(state="disabled")
                widgets['sensor_var'].set('') # Vider le champ capteur
                widgets['operator_combo'].config(values=TIME_OPERATORS) # S'assurer que les bons opérateurs sont là
                # Configurer l'entry pour HH:MM
                # (Pourrait ajouter validation format ici si souhaité)
            else:
                 # Type inconnu ou non défini, désactiver tout sauf type
                 widgets['sensor_combo'].config(state="disabled")
                 widgets['operator_combo'].config(values=OPERATORS)

        else:
             # Ligne nouvelle/vide, forcer l'appel pour configurer l'état initial
             self._on_condition_type_change(widgets, line_frame)


        # Mettre à jour la scrollregion
        self.scrollable_conditions_frame.update_idletasks()
        self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))


    def _on_condition_type_change(self, line_widgets, line_frame):
        """Adapte l'UI d'une ligne quand le type de condition change."""
        selected_type = line_widgets['type_var'].get()

        if selected_type == 'Capteur':
            line_widgets['sensor_combo'].config(state="readonly")
            line_widgets['value_entry'].config(state="normal") # Entry pour seuil numérique
            line_widgets['operator_combo'].config(values=SENSOR_OPERATORS)
            # Vider la valeur si elle contenait une heure ?
            if ':' in line_widgets['value_var'].get(): line_widgets['value_var'].set('')

        elif selected_type == 'Heure':
            line_widgets['sensor_combo'].config(state="disabled")
            line_widgets['sensor_var'].set("") # Vider le nom du capteur
            line_widgets['value_entry'].config(state="normal") # Entry pour HH:MM
            line_widgets['operator_combo'].config(values=TIME_OPERATORS)
            # Vider la valeur si elle contenait un nombre ?
            try: float(line_widgets['value_var'].get()); line_widgets['value_var'].set('')
            except ValueError: pass # Ce n'était pas un nombre, ok

        else: # Aucun type sélectionné ou type inconnu
            line_widgets['sensor_combo'].config(state="disabled")
            line_widgets['sensor_var'].set("")
            line_widgets['value_entry'].config(state="disabled")
            line_widgets['value_var'].set("")
            line_widgets['operator_combo'].config(values=OPERATORS)

        # Réinitialiser l'opérateur si la liste a changé
        if line_widgets['operator_var'].get() not in line_widgets['operator_combo']['values']:
             line_widgets['operator_var'].set('')


    def _delete_condition_line(self, line_frame_to_delete):
        """Supprime une ligne de condition de l'UI et de la liste interne."""
        # Trouver l'index de la ligne à supprimer
        index_to_delete = -1
        for i, line_info in enumerate(self.condition_lines):
            if line_info['frame'] == line_frame_to_delete:
                index_to_delete = i
                break

        if index_to_delete != -1:
            # Supprimer de la liste interne
            del self.condition_lines[index_to_delete]
            # Détruire le frame de l'UI
            line_frame_to_delete.destroy()
            # Mettre à jour la scrollregion
            self.scrollable_conditions_frame.update_idletasks()
            self.conditions_canvas.configure(scrollregion=self.conditions_canvas.bbox("all"))
            logging.debug(f"Ligne de condition {index_to_delete} supprimée.")
        else:
            logging.warning("Tentative de suppression d'une ligne de condition non trouvée.")


    def validate(self):
        """Valide les données saisies avant de fermer avec OK."""
        logging.debug("Validation de l'éditeur de conditions...")
        self.result_conditions = [] # Réinitialiser avant validation
        logic = self.logic_var.get()
        if not logic:
             messagebox.showwarning("Validation échouée", "Veuillez sélectionner une logique globale (ET/OU).", parent=self)
             return 0 # Empêche la fermeture

        if not self.condition_lines:
             # Permettre 0 condition? Oui, ça désactive la partie SI ou JUSQUÀ
             logging.debug("Validation OK (aucune condition).")
             self.result_logic = logic
             self.result_conditions = []
             return 1 # OK

        for i, line_info in enumerate(self.condition_lines):
            widgets = line_info['widgets']
            condition_data = {'condition_id': line_info['condition_id']} # Garder l'ID

            cond_type = widgets['type_var'].get()
            operator = widgets['operator_var'].get()
            value_str = widgets['value_var'].get().strip()

            if not cond_type:
                messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Veuillez sélectionner un type (Capteur/Heure).", parent=self)
                return 0
            condition_data['type'] = cond_type

            if not operator:
                 messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Veuillez sélectionner un opérateur.", parent=self)
                 return 0
            condition_data['operator'] = operator

            if not value_str:
                messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Veuillez entrer une valeur.", parent=self)
                return 0

            if cond_type == 'Capteur':
                sensor_name = widgets['sensor_var'].get()
                if not sensor_name:
                     messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Veuillez sélectionner un capteur.", parent=self)
                     return 0
                # Trouver l'ID du capteur basé sur le nom
                sensor_id = next((sid for name, sid in self.available_sensors if name == sensor_name), None)
                if not sensor_id:
                    messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Capteur '{sensor_name}' invalide ou non trouvé.", parent=self)
                    return 0
                condition_data['id'] = sensor_id

                # Valider la valeur comme float
                try:
                    threshold = float(value_str.replace(',', '.'))
                    condition_data['threshold'] = threshold
                except ValueError:
                     messagebox.showwarning("Validation échouée", f"Ligne {i+1}: La valeur du seuil '{value_str}' doit être un nombre.", parent=self)
                     return 0
                # Valider opérateur pour capteur
                if operator not in SENSOR_OPERATORS:
                     messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Opérateur '{operator}' invalide pour un capteur.", parent=self)
                     return 0

            elif cond_type == 'Heure':
                # Valider le format HH:MM
                if not TIME_REGEX.match(value_str):
                    messagebox.showwarning("Validation échouée", f"Ligne {i+1}: La valeur d'heure '{value_str}' doit être au format HH:MM (ex: 06:30, 22:00).", parent=self)
                    return 0
                condition_data['value'] = value_str
                condition_data['id'] = None # Pas d'ID pour l'heure

                # Valider opérateur pour heure
                if operator not in TIME_OPERATORS:
                     messagebox.showwarning("Validation échouée", f"Ligne {i+1}: Opérateur '{operator}' invalide pour une heure.", parent=self)
                     return 0

            # Ajouter la condition validée à la liste des résultats
            self.result_conditions.append(condition_data)

        # Si on arrive ici, tout est valide
        self.result_logic = logic
        logging.debug(f"Validation OK. Logique: {self.result_logic}, Conditions: {self.result_conditions}")
        return 1 # Permet la fermeture

    def apply(self):
        """Appelé si validate() retourne True (quand OK est cliqué)."""
        # Les résultats ont été stockés dans self.result_logic et self.result_conditions par validate()
        # On appelle la méthode de l'application principale pour mettre à jour la règle
        if self.result_logic is not None and self.result_conditions is not None:
             logging.info(f"Application des changements de l'éditeur pour règle {self.rule_id}, type {self.condition_type}")
             self.app.update_rule_conditions_from_editor(
                 self.rule_id,
                 self.condition_type,
                 self.result_logic,
                 self.result_conditions
             )
        else:
             # Ne devrait pas arriver si validate passe, mais sécurité
             logging.error("Erreur: apply() appelé mais les résultats de validation manquent.")

#--------------------------------------------------------------------------
# FIN CLASSE ConditionEditor
#--------------------------------------------------------------------------


class GreenhouseApp: # Reprise de la classe principale

    def __init__(self, root):
        self.root = root
        self.root.title("Gestionnaire de Serre")
        try: self.root.geometry("1300x800") # Taille peut être ajustée maintenant
        except tk.TclError as e: logging.warning(f"Geo init err: {e}")

        style = ttk.Style(self.root)
        style.configure("Red.TButton", foreground="red", background="white", font=('Helvetica', 10))
        style.map("Red.TButton", foreground=[('pressed', 'white'), ('active', 'white')], background=[('pressed', 'darkred'), ('active', 'red')])
        style.configure("RuleSummary.TLabel", font=('Helvetica', 8, 'italic'))

        self.log_queue = queue.Queue()
        setup_logging(self.log_queue)

        self.config = load_config(DEFAULT_CONFIG_FILE)
        self.aliases = self.config.get('aliases', {"sensors": {}, "devices": {}, "outlets": {}})
        loaded_rules = self.config.get('rules', [])
        self.rules = []
        rule_counter = 1
        for rule_data in loaded_rules:
            if not isinstance(rule_data, dict): continue
            if 'id' not in rule_data or not rule_data['id']: rule_data['id'] = str(uuid.uuid4())
            rule_data.setdefault('name', f"Règle {rule_counter}")
            rule_data.setdefault('trigger_logic', 'ET')
            rule_data.setdefault('conditions', [])
            rule_data.setdefault('until_logic', 'OU')
            rule_data.setdefault('until_conditions', [])
            # Nettoyage anciens champs
            rule_data.pop('sensor_id', None); rule_data.pop('operator', None); rule_data.pop('threshold', None); rule_data.pop('until_condition', None)
            self.rules.append(rule_data)
            rule_counter += 1
        logging.info(f"{len(self.rules)} règles chargées.")

        self.kasa_devices = {}
        self.temp_manager = TempSensorManager()
        self.light_manager = BH1750Manager()
        self.available_sensors = []
        self.available_kasa_strips = []
        self.available_outlets = {}
        self.monitoring_active = False
        self.monitoring_thread = None
        self.asyncio_loop = None
        self.ui_update_job = None
        self.live_kasa_states = {}
        self.rule_widgets = {}

        self.create_widgets()
        self.populate_initial_ui_data()
        self.update_log_display()
        self.discover_all_devices()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # --- Fonctions Alias (identiques) ---
    def get_alias(self, item_type, item_id, sub_id=None):
        try:
            if item_type == 'sensor': return self.aliases.get('sensors', {}).get(str(item_id), str(item_id))
            elif item_type == 'device': return self.aliases.get('devices', {}).get(str(item_id), str(item_id))
            elif item_type == 'outlet':
                device_outlets = self.aliases.get('outlets', {}).get(str(item_id), {})
                fallback_name = f"Prise {sub_id}"
                if str(item_id) in self.kasa_devices:
                    outlet_info = next((o for o in self.kasa_devices[str(item_id)].get('info',{}).get('outlets',[]) if o.get('index') == sub_id), None)
                    if outlet_info: fallback_name = outlet_info.get('alias', fallback_name)
                return device_outlets.get(str(sub_id), fallback_name)
        except KeyError: pass
        if sub_id is not None:
             if item_type == 'outlet' and str(item_id) in self.kasa_devices:
                 outlet_info = next((o for o in self.kasa_devices[str(item_id)].get('info',{}).get('outlets',[]) if o.get('index') == sub_id), None)
                 if outlet_info: return outlet_info.get('alias', f"Prise {sub_id}")
             return f"{item_id}-Prise {sub_id}"
        return str(item_id)

    def update_alias(self, item_type, item_id, new_alias, sub_id=None):
        if 'aliases' not in self.config: self.config['aliases'] = {"sensors": {}, "devices": {}, "outlets": {}}
        if item_type not in self.config['aliases']: self.config['aliases'][item_type] = {}
        if item_type == 'outlet':
            if 'outlets' not in self.config['aliases']: self.config['aliases']['outlets'] = {}
            if str(item_id) not in self.config['aliases']['outlets']: self.config['aliases']['outlets'][str(item_id)] = {}
            self.config['aliases']['outlets'][str(item_id)][str(sub_id)] = new_alias
        elif item_type == 'device':
             if 'devices' not in self.config['aliases']: self.config['aliases']['devices'] = {}
             self.config['aliases']['devices'][str(item_id)] = new_alias
        elif item_type == 'sensor':
            if 'sensors' not in self.config['aliases']: self.config['aliases']['sensors'] = {}
            self.config['aliases']['sensors'][str(item_id)] = new_alias
        else: logging.error(f"Type item inconnu pour alias: {item_type}"); return
        self.aliases = self.config['aliases']
        logging.info(f"Alias màj {item_type} {item_id}" + (f"[{sub_id}]" if sub_id else "") + f": '{new_alias}'")

    def edit_alias_dialog(self, item_type, item_id, current_name, sub_id=None):
        prompt = f"Nouveau nom pour {item_type} '{current_name}'"
        if item_type == 'outlet': prompt = f"Nouveau nom pour prise '{current_name}' (Barre: {self.get_alias('device', item_id)})"
        elif item_type == 'device': prompt = f"Nouveau nom pour appareil '{current_name}' (MAC: {item_id})"
        new_name = simpledialog.askstring("Modifier Alias", prompt, initialvalue=current_name, parent=self.root)
        if new_name and new_name != current_name:
            self.update_alias(item_type, item_id, new_name, sub_id)
            self.refresh_device_lists()
            self.repopulate_all_rule_dropdowns()
            self.update_status_display()
            self.root.update_idletasks()

    # --- Création Widgets ---
    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        rules_frame_container = ttk.LabelFrame(main_frame, text="Règles d'Automatisation", padding="10")
        rules_frame_container.pack(fill=tk.X, expand=False, pady=5)
        self.rules_canvas = tk.Canvas(rules_frame_container, borderwidth=0, highlightthickness=0)
        scrollbar = ttk.Scrollbar(rules_frame_container, orient="vertical", command=self.rules_canvas.yview)
        self.scrollable_rules_frame = ttk.Frame(self.rules_canvas)
        self.scrollable_rules_frame.bind("<Configure>", lambda e: self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all")))
        self.rules_canvas.create_window((0, 0), window=self.scrollable_rules_frame, anchor="nw")
        self.rules_canvas.configure(yscrollcommand=scrollbar.set)
        self.rules_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.rules_canvas.config(height=300)
        add_rule_button = ttk.Button(main_frame, text="➕ Ajouter une Règle", command=self.add_rule_ui)
        add_rule_button.pack(pady=5)
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.pack(fill=tk.X, expand=False, pady=5)
        self.start_button = ttk.Button(control_frame, text="🟢 Gérer ma Serre", command=self.start_monitoring)
        self.start_button.pack(side=tk.LEFT, padx=5)
        self.stop_button = ttk.Button(control_frame, text="🔴 Arrêter", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        save_button = ttk.Button(control_frame, text="💾 Sauvegarder Configuration", command=self.save_configuration)
        save_button.pack(side=tk.RIGHT, padx=5)
        status_log_pane = ttk.PanedWindow(main_frame, orient=tk.HORIZONTAL)
        status_log_pane.pack(fill=tk.BOTH, expand=True, pady=5)
        status_frame_container = ttk.LabelFrame(status_log_pane, text="Statut Actuel", padding="10")
        status_log_pane.add(status_frame_container, weight=1)
        status_canvas = tk.Canvas(status_frame_container, borderwidth=0, highlightthickness=0)
        status_scrollbar = ttk.Scrollbar(status_frame_container, orient="vertical", command=status_canvas.yview)
        self.scrollable_status_frame = ttk.Frame(status_canvas)
        self.scrollable_status_frame.bind("<Configure>", lambda e: status_canvas.configure(scrollregion=status_canvas.bbox("all")))
        status_canvas.create_window((0, 0), window=self.scrollable_status_frame, anchor="nw")
        status_canvas.configure(yscrollcommand=status_scrollbar.set)
        status_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True); status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        log_frame_container = ttk.LabelFrame(status_log_pane, text="Journal d'Événements", padding="10")
        status_log_pane.add(log_frame_container, weight=1)
        self.log_display = scrolledtext.ScrolledText(log_frame_container, wrap=tk.WORD, state=tk.DISABLED, height=15)
        self.log_display.pack(fill=tk.BOTH, expand=True)
        self.status_labels = {}; self.rule_widgets = {}

    # --- Peuplement Initial UI ---
    def populate_initial_ui_data(self):
        for rule_data in self.rules: self.add_rule_ui(rule_data=rule_data)

    # --- Gestion Règles UI ---
    def add_rule_ui(self, rule_data=None):
        is_new_rule = False
        if not rule_data:
            is_new_rule = True; rule_id = str(uuid.uuid4())
            rule_data = {'id': rule_id, 'name': f"Nouvelle Règle {len(self.rules) + 1}",
                         'trigger_logic': 'ET', 'conditions': [], 'target_device_mac': None,
                         'target_outlet_index': None, 'action': ACTIONS[0],
                         'until_logic': 'OU', 'until_conditions': []}
            self.rules.append(rule_data)
        else:
            rule_id = rule_data.get('id')
            if not rule_id: rule_id = str(uuid.uuid4()); rule_data['id'] = rule_id

        rule_frame = ttk.Frame(self.scrollable_rules_frame, padding="5", borderwidth=1, relief="groove")
        rule_frame.pack(fill=tk.X, pady=3, padx=2)
        widgets = {}

        name_frame = ttk.Frame(rule_frame)
        name_frame.pack(side=tk.TOP, fill=tk.X, expand=True)
        widgets['name_label'] = ttk.Label(name_frame, text=rule_data.get('name', 'Sans Nom'), font=('Helvetica', 10, 'bold'))
        widgets['name_label'].pack(side=tk.LEFT, padx=(0, 5), pady=(0, 3))
        widgets['edit_name_button'] = ttk.Button(name_frame, text="✎", width=2, command=lambda r_id=rule_id: self.edit_rule_name_dialog(r_id))
        widgets['edit_name_button'].pack(side=tk.LEFT, padx=(0, 15))
        delete_rule_button = ttk.Button(name_frame, text="❌", width=3, style="Red.TButton", command=lambda rid=rule_id: self.delete_rule(rid))
        delete_rule_button.pack(side=tk.RIGHT, padx=5)

        main_line_frame = ttk.Frame(rule_frame)
        main_line_frame.pack(side=tk.TOP, fill=tk.X, expand=True, pady=3)

        widgets['si_summary_label'] = ttk.Label(main_line_frame, text=self._generate_condition_summary(rule_data.get('conditions', []), rule_data.get('trigger_logic', 'ET')), style="RuleSummary.TLabel", anchor="w", width=40)
        widgets['si_summary_label'].pack(side=tk.LEFT, padx=(5, 0))
        widgets['edit_si_button'] = ttk.Button(main_line_frame, text="SI...", width=5, command=lambda r_id=rule_id: self.open_condition_editor(r_id, 'trigger'))
        widgets['edit_si_button'].pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(main_line_frame, text="ALORS").pack(side=tk.LEFT, padx=(10, 2))
        widgets['kasa_var'] = tk.StringVar(); widgets['kasa_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['kasa_var'], width=25, state="readonly"); widgets['kasa_combo']['values'] = [n for n, _m in self.available_kasa_strips]; widgets['kasa_combo'].pack(side=tk.LEFT, padx=2); widgets['kasa_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.update_outlet_options(rid))
        widgets['outlet_var'] = tk.StringVar(); widgets['outlet_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['outlet_var'], width=20, state="readonly"); widgets['outlet_combo']['values'] = []; widgets['outlet_combo'].pack(side=tk.LEFT, padx=2); widgets['outlet_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))
        widgets['action_var'] = tk.StringVar(); widgets['action_combo'] = ttk.Combobox(main_line_frame, textvariable=widgets['action_var'], values=ACTIONS, width=5, state="readonly"); widgets['action_combo'].pack(side=tk.LEFT, padx=2); widgets['action_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        until_frame = ttk.Frame(rule_frame)
        until_frame.pack(side=tk.TOP, fill=tk.X, expand=True, padx=(30, 0), pady=(0, 2))
        ttk.Label(until_frame, text="↳").pack(side=tk.LEFT, padx=(0, 5))
        widgets['until_summary_label'] = ttk.Label(until_frame, text=self._generate_condition_summary(rule_data.get('until_conditions', []), rule_data.get('until_logic', 'OU')), style="RuleSummary.TLabel", anchor="w", width=40)
        widgets['until_summary_label'].pack(side=tk.LEFT, padx=(0,0))
        widgets['edit_until_button'] = ttk.Button(until_frame, text="JUSQU'À...", width=10, command=lambda r_id=rule_id: self.open_condition_editor(r_id, 'until'))
        widgets['edit_until_button'].pack(side=tk.LEFT, padx=(5, 10))

        self.rule_widgets[rule_id] = {'frame': rule_frame, 'widgets': widgets}
        if not is_new_rule: self._populate_rule_ui_from_data(rule_id, rule_data)
        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))


    def _generate_condition_summary(self, conditions, logic):
        if not isinstance(conditions, list): conditions = []
        count = len(conditions)
        if count == 0: return "(Aucune condition)"
        elif count == 1: return "(1 condition)"
        else: logic_str = logic if logic in LOGIC_OPERATORS else 'ET'; return f"({count} conditions - {logic_str})"

    def edit_rule_name_dialog(self, rule_id):
        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data: logging.error(f"Edit name: Rule {rule_id} not found."); return
        current_name = rule_data.get('name', '')
        new_name = simpledialog.askstring("Modifier Nom de Règle", f"Nouveau nom pour '{current_name}'", initialvalue=current_name, parent=self.root)
        if new_name and new_name != current_name:
            rule_data['name'] = new_name
            if rule_id in self.rule_widgets and 'name_label' in self.rule_widgets[rule_id]['widgets']:
                 try: self.rule_widgets[rule_id]['widgets']['name_label'].config(text=new_name)
                 except tk.TclError: pass
            logging.info(f"Nom règle {rule_id} màj: '{new_name}'")

    def _populate_rule_ui_from_data(self, rule_id, rule_data):
        if rule_id not in self.rule_widgets: return
        widgets = self.rule_widgets[rule_id]['widgets']
        widgets['name_label'].config(text=rule_data.get('name', 'Sans Nom'))
        widgets['si_summary_label'].config(text=self._generate_condition_summary(rule_data.get('conditions', []), rule_data.get('trigger_logic', 'ET')))
        widgets['until_summary_label'].config(text=self._generate_condition_summary(rule_data.get('until_conditions', []), rule_data.get('until_logic', 'OU')))
        kasa_mac = rule_data.get('target_device_mac'); outlet_index = rule_data.get('target_outlet_index')
        if kasa_mac:
            widgets['kasa_var'].set(self.get_alias('device', kasa_mac))
            self.rule_widgets[rule_id]['desired_outlet_index'] = outlet_index
            widgets['outlet_var'].set(self.get_alias('outlet', kasa_mac, outlet_index) if outlet_index is not None else "")
        else: widgets['kasa_var'].set(''); widgets['outlet_var'].set('')
        widgets['action_var'].set(rule_data.get('action', ACTIONS[0]))

    def delete_rule(self, rule_id):
        if rule_id in self.rule_widgets:
            self.rule_widgets[rule_id]['frame'].destroy(); del self.rule_widgets[rule_id]
            initial_len = len(self.rules)
            self.rules = [rule for rule in self.rules if rule.get('id') != rule_id]
            if len(self.rules) < initial_len: logging.info(f"Règle {rule_id} supprimée.")
            else: logging.warning(f"Règle {rule_id} non trouvée pour suppression.")
            self.rules_canvas.update_idletasks(); self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))

    def update_outlet_options(self, rule_id, preselect_outlet_index=None):
        if rule_id not in self.rule_widgets: return
        widgets = self.rule_widgets[rule_id]['widgets']; selected_kasa_name = widgets['kasa_var'].get()
        selected_mac = next((mac for name, mac in self.available_kasa_strips if name == selected_kasa_name), None)
        outlet_options, current_outlet_alias = [], ""
        if selected_mac and selected_mac in self.available_outlets:
            outlet_options = [name for name, _index in self.available_outlets[selected_mac]]
            if preselect_outlet_index is not None:
                current_outlet_alias = next((name for name, index in self.available_outlets[selected_mac] if index == preselect_outlet_index), "")
        try:
            if widgets['outlet_combo'].winfo_exists():
                 widgets['outlet_combo']['values'] = outlet_options
                 if current_outlet_alias: widgets['outlet_var'].set(current_outlet_alias)
                 elif outlet_options: widgets['outlet_var'].set(outlet_options[0])
                 else: widgets['outlet_var'].set('')
        except tk.TclError: pass
        self.on_rule_change(rule_id)

    def on_rule_change(self, rule_id):
        if rule_id not in self.rule_widgets: return
        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data: return
        widgets = self.rule_widgets[rule_id]['widgets']
        kasa_name, outlet_name, action = widgets['kasa_var'].get(), widgets['outlet_var'].get(), widgets['action_var'].get()
        kasa_mac = next((m for n, m in self.available_kasa_strips if n == kasa_name), None)
        outlet_index = None
        if kasa_mac and kasa_mac in self.available_outlets:
             outlet_index = next((idx for name, idx in self.available_outlets[kasa_mac] if name == outlet_name), None)
        rule_data['target_device_mac'] = kasa_mac; rule_data['target_outlet_index'] = outlet_index; rule_data['action'] = action
        logging.debug(f"Partie ALORS règle {rule_id} màj.")

    def repopulate_all_rule_dropdowns(self):
        logging.debug("Repopulation dropdowns Kasa/Outlet.")
        kasa_names = [name for name, _mac in self.available_kasa_strips]
        for rule_id, data in self.rule_widgets.items():
            widgets = data['widgets']; rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
            if not rule_data: continue
            current_kasa_mac = rule_data.get('target_device_mac')
            current_kasa_name = self.get_alias('device', current_kasa_mac) if current_kasa_mac else ""
            try:
                 if widgets['kasa_combo'].winfo_exists():
                      widgets['kasa_combo']['values'] = kasa_names
                      if current_kasa_name in kasa_names:
                           widgets['kasa_var'].set(current_kasa_name)
                           desired_outlet_index = data.get('desired_outlet_index', rule_data.get('target_outlet_index'))
                           self.update_outlet_options(rule_id, preselect_outlet_index=desired_outlet_index)
                      else:
                           widgets['kasa_var'].set('')
                           if widgets['outlet_combo'].winfo_exists(): widgets['outlet_combo']['values'] = []; widgets['outlet_var'].set('')
            except tk.TclError: pass

    # --- Ouverture de l'éditeur de conditions ---
    def open_condition_editor(self, rule_id, condition_type):
        """Ouvre le pop-up pour éditer les conditions SI ou JUSQUÀ."""
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data: logging.error(f"Editeur: Règle {rule_id} non trouvée."); return

        if condition_type == 'trigger':
            logic = rule_data.get('trigger_logic', 'ET')
            conditions = list(rule_data.get('conditions', [])) # Copie
            title = f"Modifier Conditions SI - '{rule_data.get('name', rule_id)}'"
        elif condition_type == 'until':
            logic = rule_data.get('until_logic', 'OU')
            conditions = list(rule_data.get('until_conditions', [])) # Copie
            title = f"Modifier Conditions JUSQU'À - '{rule_data.get('name', rule_id)}'"
        else: logging.error(f"Type condition inconnu: {condition_type}"); return

        # Lancer l'éditeur (modal)
        editor = ConditionEditor(self.root, title, rule_id, condition_type, logic, conditions, self.available_sensors, self)
        # La méthode apply() de l'éditeur appellera self.update_rule_conditions_from_editor si OK

    # --- Méthode appelée par l'éditeur après clic sur OK ---
    def update_rule_conditions_from_editor(self, rule_id, condition_type, new_logic, new_conditions):
        """Met à jour les données de la règle et l'UI après édition via pop-up."""
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data: logging.error(f"Update Editeur: Règle {rule_id} non trouvée."); return

        logging.info(f"Màj conditions {condition_type} pour règle {rule_id}. Logique: {new_logic}, Conditions: {len(new_conditions)}")

        # Mettre à jour les données internes
        if condition_type == 'trigger':
            rule_data['trigger_logic'] = new_logic
            rule_data['conditions'] = new_conditions
            # Mettre à jour le label résumé dans l'UI
            if rule_id in self.rule_widgets and 'si_summary_label' in self.rule_widgets[rule_id]['widgets']:
                 try:
                     self.rule_widgets[rule_id]['widgets']['si_summary_label'].config(text=self._generate_condition_summary(new_conditions, new_logic))
                 except tk.TclError: pass # Widget peut avoir été détruit

        elif condition_type == 'until':
            rule_data['until_logic'] = new_logic
            rule_data['until_conditions'] = new_conditions
             # Mettre à jour le label résumé dans l'UI
            if rule_id in self.rule_widgets and 'until_summary_label' in self.rule_widgets[rule_id]['widgets']:
                 try:
                     self.rule_widgets[rule_id]['widgets']['until_summary_label'].config(text=self._generate_condition_summary(new_conditions, new_logic))
                 except tk.TclError: pass


    # --- Découverte / Rafraichissement (inchangées) ---
    def discover_all_devices(self):
        logging.info("Démarrage découverte...")
        try: self.temp_manager.discover_sensors()
        except Exception as e: logging.error(f"Err T° discovery: {e}")
        try: self.light_manager.scan_sensors()
        except Exception as e: logging.error(f"Err Lux discovery: {e}")
        threading.Thread(target=self._run_kasa_discovery_async, daemon=True).start()

    def _run_kasa_discovery_async(self):
        try: loop = asyncio.get_event_loop()
        except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        loop.run_until_complete(self._async_discover_kasa())

    async def _async_discover_kasa(self):
        discoverer = DeviceDiscoverer(); discovered_kasa = await discoverer.discover()
        new_kasa_devices = {}; tasks_turn_off = []
        for dev_info in discovered_kasa:
             ip, mac = dev_info.get('ip'), dev_info.get('mac')
             if not ip or not mac: logging.warning(f"Kasa sans IP/MAC: {dev_info.get('alias', 'N/A')}"); continue
             ctrl = DeviceController(ip, dev_info.get('is_strip'), dev_info.get('is_plug'))
             new_kasa_devices[mac] = {'info': dev_info, 'controller': ctrl, 'ip': ip }
             if not self.monitoring_active and (dev_info.get('is_strip') or dev_info.get('is_plug')):
                 tasks_turn_off.append(ctrl.turn_all_outlets_off())
        if tasks_turn_off:
             logging.info(f"Extinction initiale {len(tasks_turn_off)} Kasa..."); await asyncio.gather(*tasks_turn_off, return_exceptions=True)
        self.kasa_devices = new_kasa_devices
        logging.info(f"Découverte Kasa: {len(self.kasa_devices)} appareil(s).")
        self.root.after(100, self.refresh_device_lists)

    def refresh_device_lists(self):
        logging.info("Rafraîchissement listes périphériques UI...")
        t_ids=[s.id for s in self.temp_manager.sensors]; l_ids=[hex(a) for a in self.light_manager.get_active_sensors()]
        self.available_sensors = [(self.get_alias('sensor', sid), sid) for sid in t_ids+l_ids]
        self.available_kasa_strips = []; self.available_outlets = {}
        s_macs = sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m))
        for mac in s_macs:
            data = self.kasa_devices[mac]; d_alias = self.get_alias('device', mac)
            self.available_kasa_strips.append((d_alias, mac)); outlets = []
            if data['info'].get('is_strip') or data['info'].get('is_plug'):
                 for o_data in data['info'].get('outlets', []):
                     idx = o_data.get('index');
                     if idx is not None: outlets.append((self.get_alias('outlet', mac, idx), idx))
            self.available_outlets[mac] = sorted(outlets, key=lambda x: x[1])
        self.repopulate_all_rule_dropdowns(); self.update_status_display()
        logging.info("Listes périphériques UI màj.")

    # --- Fonctions Affichage Statut (inchangées) ---
    def update_status_display(self):
        logging.debug("MàJ affichage statut.")
        for w in self.scrollable_status_frame.winfo_children(): w.destroy()
        self.status_labels = {}; row_num = 0
        try: all_temp = self.temp_manager.read_all_temperatures()
        except Exception: all_temp = {}
        try: all_light = self.light_manager.read_all_sensors()
        except Exception: all_light = {}
        ttk.Label(self.scrollable_status_frame, text="Capteurs:",font=('H',10,'b')).grid(row=row_num,column=0,columnspan=4,sticky='w',pady=(5,2)); row_num+=1
        for alias, s_id in sorted(self.available_sensors, key=lambda x:x[0]):
            v_txt, unit = "N/A", ""
            is_t, is_l = s_id in all_temp, s_id in all_light
            if is_t: temp=all_temp.get(s_id); v_txt, unit=(f"{temp:.1f}","°C") if temp is not None else ("Err","")
            elif is_l: lux=all_light.get(s_id); v_txt, unit=(f"{lux:.1f}"," Lux") if lux is not None else ("Err","")
            frm=ttk.Frame(self.scrollable_status_frame); frm.grid(row=row_num,column=0,columnspan=4,sticky='w')
            n_lbl=ttk.Label(frm,text=f"{alias}:",width=25); n_lbl.pack(side=tk.LEFT,padx=5)
            v_lbl=ttk.Label(frm,text=f"{v_txt}{unit}",width=15); v_lbl.pack(side=tk.LEFT,padx=5)
            e_btn=ttk.Button(frm,text="✎",width=2,command=lambda i=s_id,n=alias:self.edit_alias_dialog('sensor',i,n)); e_btn.pack(side=tk.LEFT,padx=2)
            self.status_labels[s_id]={'type':'sensor','label_name':n_lbl,'label_value':v_lbl,'button_edit':e_btn}; row_num+=1
        ttk.Label(self.scrollable_status_frame, text="Prises Kasa:",font=('H',10,'b')).grid(row=row_num,column=0,columnspan=4,sticky='w',pady=(10,2)); row_num+=1
        for mac in sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device',m)):
            data=self.kasa_devices[mac]; d_alias=self.get_alias('device',mac); ip=data.get('ip','?.?.?.?')
            frm_d=ttk.Frame(self.scrollable_status_frame); frm_d.grid(row=row_num,column=0,columnspan=4,sticky='w')
            d_n_lbl=ttk.Label(frm_d, text=f"{d_alias} ({ip}) [{mac}]"); d_n_lbl.pack(side=tk.LEFT,padx=5)
            d_e_btn=ttk.Button(frm_d,text="✎",width=2,command=lambda m=mac, n=d_alias:self.edit_alias_dialog('device',m,n)); d_e_btn.pack(side=tk.LEFT,padx=2)
            self.status_labels[mac]={'type':'device','label_name':d_n_lbl,'button_edit':d_e_btn}; row_num+=1
            if mac in self.available_outlets:
                for o_alias, idx in self.available_outlets[mac]:
                    state=self._get_shared_kasa_state(mac, idx)
                    if state=="Inconnu": o_info=next((o for o in data['info'].get('outlets',[]) if o.get('index')==idx),None); state="ON" if o_info and o_info.get('is_on') else "OFF"
                    frm_o=ttk.Frame(self.scrollable_status_frame); frm_o.grid(row=row_num,column=1,columnspan=3,sticky='w',padx=(20,0))
                    o_n_lbl=ttk.Label(frm_o,text=f"└─ {o_alias}:",width=23); o_n_lbl.pack(side=tk.LEFT,padx=5)
                    o_v_lbl=ttk.Label(frm_o,text=state,width=10); o_v_lbl.pack(side=tk.LEFT,padx=5)
                    o_e_btn=ttk.Button(frm_o,text="✎",width=2,command=lambda m=mac,i=idx,n=o_alias:self.edit_alias_dialog('outlet',m,n,sub_id=i)); o_e_btn.pack(side=tk.LEFT,padx=2)
                    o_key=f"{mac}_{idx}"; self.status_labels[o_key]={'type':'outlet','mac':mac,'index':idx,'label_name':o_n_lbl,'label_value':o_v_lbl,'button_edit':o_e_btn}; row_num+=1
        self.scrollable_status_frame.update_idletasks(); status_canvas=self.scrollable_status_frame.master; status_canvas.configure(scrollregion=status_canvas.bbox("all"))

    def schedule_periodic_updates(self): self.update_live_status(); self.ui_update_job = self.root.after(5000, self.schedule_periodic_updates)
    
    def cancel_periodic_updates(self):
        """Annule la mise à jour périodique de l'UI."""
        if self.ui_update_job:
            try:
                # Essayer d'annuler la tâche planifiée
                self.root.after_cancel(self.ui_update_job)
                logging.debug(f"Tâche UI périodique {self.ui_update_job} annulée.")
            except tk.TclError as e:
                # Peut arriver si l'ID n'est plus valide (rare)
                logging.warning(f"Erreur lors de l'annulation de la tâche UI {self.ui_update_job}: {e}")
            finally:
                 # Assurer que la variable est réinitialisée même en cas d'erreur d'annulation
                self.ui_update_job = None
    
    def update_live_status(self):
        if not self.monitoring_active: return
        logging.debug("MàJ live status UI...")
        temps=self.temp_manager.read_all_temperatures(); lights=self.light_manager.read_all_sensors()
        for s_id,data in self.status_labels.items():
            if data['type']=='sensor':
                val,unit=None,""; is_t,is_l=s_id in temps,s_id in lights
                if is_t: val,unit=temps.get(s_id),"°C"
                elif is_l: val,unit=lights.get(s_id)," Lux"
                if data['label_value'].winfo_exists(): data['label_value'].config(text=f"{val:.1f}{unit}" if val is not None else "Err/NA")
            elif data['type']=='outlet':
                 if data['label_value'].winfo_exists(): data['label_value'].config(text=self._get_shared_kasa_state(data['mac'],data['index']))

    def _get_shared_kasa_state(self, mac, index):
        try: return "ON" if self.live_kasa_states[mac][index] else "OFF"
        except(AttributeError, KeyError): return "Inconnu"

    # --- Logs ---
    def update_log_display(self):
        while True:
            try: record = self.log_queue.get_nowait()
            except queue.Empty: break
            else: self.log_display.config(state=tk.NORMAL); self.log_display.insert(tk.END, record + '\n'); self.log_display.config(state=tk.DISABLED); self.log_display.see(tk.END)
        self.root.after(100, self.update_log_display)

    # --- Démarrage / Arrêt Monitoring ---
    def start_monitoring(self):
        if self.monitoring_active: logging.warning("Monitoring déjà actif."); return
        logging.info("Démarrage monitoring..."); self.monitoring_active = True
        self.start_button.config(state=tk.DISABLED); self.stop_button.config(state=tk.NORMAL)
        self._set_rules_ui_state(tk.DISABLED)
        self.live_kasa_states = {}
        self.monitoring_thread = threading.Thread(target=self._run_monitoring_loop, daemon=True); self.monitoring_thread.start()
        self.schedule_periodic_updates()

    def stop_monitoring(self):
        if not self.monitoring_active: logging.warning("Monitoring non actif."); return
        logging.info("Arrêt monitoring..."); self.monitoring_active = False
        if self.monitoring_thread and self.monitoring_thread.is_alive():
            logging.info("Attente fin thread monitoring (max 5s)..."); self.monitoring_thread.join(timeout=5.0)
            if self.monitoring_thread.is_alive(): logging.warning("Timeout attente thread monitoring.")
            else: logging.info("Thread monitoring terminé.")
        self.start_button.config(state=tk.NORMAL); self.stop_button.config(state=tk.DISABLED)
        self._set_rules_ui_state(tk.NORMAL)
        self.cancel_periodic_updates()
        logging.info("Extinction sécurité Kasa..."); threading.Thread(target=self._turn_off_all_kasa_safely, daemon=True).start()
        logging.info("Processus arrêt monitoring terminé.")

    def _set_rules_ui_state(self, state):
        try:
            main_frame=self.root.winfo_children()[0]; add_btn=next(w for w in main_frame.winfo_children() if isinstance(w,ttk.Button) and "Ajouter" in w.cget("text")); add_btn.config(state=state)
        except Exception: pass
        for rule_id, data in self.rule_widgets.items():
            widgets = data.get('widgets',{}); rule_frame = data.get('frame')
            if not rule_frame or not rule_frame.winfo_exists(): continue
            try: # Bouton Supprimer Regle
                name_frame = next(iter(w for w in rule_frame.winfo_children() if isinstance(w, ttk.Frame)))
                del_btn = next(w for w in name_frame.winfo_children() if isinstance(w, ttk.Button) and w.cget('text') == "❌")
                del_btn.config(state=state)
            except Exception: pass
            for btn_key in ['edit_name_button', 'edit_si_button', 'edit_until_button']:
                if btn_key in widgets: try: widgets[btn_key].config(state=state)
                except tk.TclError: pass
            for w_key in ['kasa_combo', 'outlet_combo', 'action_combo']:
                 if w_key in widgets: try: widgets[w_key].config(state=state if state==tk.DISABLED else 'readonly')
                 except tk.TclError: pass

    def _run_monitoring_loop(self):
        try: self.asyncio_loop = asyncio.get_event_loop()
        except RuntimeError: self.asyncio_loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.asyncio_loop)
        try: self.asyncio_loop.run_until_complete(self._async_monitoring_task())
        except Exception as e: logging.critical(f"Err boucle asyncio: {e}", exc_info=True)
        finally: logging.info("Boucle asyncio finie."); self.root.after(0, self.stop_monitoring) if self.monitoring_active else None

    async def _update_live_kasa_states_task(self):
         logging.debug("MàJ états Kasa..."); new_states = {}
         tasks = [self._fetch_one_kasa_state(m, d['controller']) for m, d in self.kasa_devices.items()]
         results = await asyncio.gather(*tasks, return_exceptions=True)
         for res in results:
             if isinstance(res, Exception): logging.error(f"Err lecture Kasa: {res}")
             elif isinstance(res, dict) and res: new_states.update(res)
         self.live_kasa_states = new_states; logging.debug(f"États Kasa màj: {len(new_states)} dev")

    async def _fetch_one_kasa_state(self, mac, controller):
         try:
             await controller._connect()
             if controller._device:
                 states = await controller.get_outlet_state()
                 if states is not None: return {mac: {o['index']: o['is_on'] for o in states if 'i' in o and 'o' in o}} # Shortened keys for debug
                 else: logging.warning(f"État None pour {mac}")
             else: logging.warning(f"Pas co/refresh {mac}")
         except Exception as e: logging.error(f"Err fetch {mac}: {e}"); raise e
         return {}

    # --- Logique d'évaluation ---
    async def _async_monitoring_task(self):
        active_until_rules = {}
        last_kasa_update = datetime.min
        kasa_interval = timedelta(seconds=10)

        while self.monitoring_active:
            now_dt = datetime.now(); now_time = now_dt.time()
            logging.debug(f"--- Cycle Mon {now_dt:%H:%M:%S} ---")
            try:
                t_val = await self.asyncio_loop.run_in_executor(None, self.temp_manager.read_all_temperatures)
                l_val = await self.asyncio_loop.run_in_executor(None, self.light_manager.read_all_sensors)
                sensors = {k: v for k, v in {**t_val, **l_val}.items() if v is not None}
            except Exception as e: logging.error(f"Err lecture capteurs: {e}"); sensors = {}
            if now_dt - last_kasa_update >= kasa_interval:
                 try: await self._update_live_kasa_states_task(); last_kasa_update = now_dt
                 except Exception as e: logging.error(f"Échec màj Kasa: {e}")

            tasks_to_run = []; rules = list(self.rules); desired_states = {}
            active_until_copy = dict(active_until_rules)

            # Eval UNTIL
            for rule_id, until_info in active_until_copy.items():
                rule = next((r for r in rules if r.get('id') == rule_id), None)
                if not rule: del active_until_rules[rule_id]; continue
                mac, idx = rule.get('target_device_mac'), rule.get('target_outlet_index')
                if mac is None or idx is None: continue
                key = (mac, idx); logic = rule.get('until_logic', 'OU'); conditions = rule.get('until_conditions', [])
                if not conditions: del active_until_rules[rule_id]; continue
                until_met = False
                if logic == 'ET': all_t = True; if not conditions: all_t=False;
                else:
                    for cond in conditions:
                        if not self._check_condition(cond, sensors, now_time): all_t=False; logging.debug(f"R{rule_id} UNTIL(ET) échoue: {cond}"); break
                    until_met = all_t
                elif logic == 'OU': any_t = False
                else:
                    for cond in conditions:
                        if self._check_condition(cond, sensors, now_time): any_t=True; logging.debug(f"R{rule_id} UNTIL(OU) réussit: {cond}"); break
                    until_met = any_t
                else: logging.error(f"Logique UNTIL? {logic} R{rule_id}")
                if until_met: desired_states[key] = until_info['revert_action']; del active_until_rules[rule_id]; logging.info(f"R{rule_id} UNTIL({logic}) MET. Retour: {until_info['revert_action']}")

            # Eval SI
            for rule in rules:
                r_id = rule.get('id'); mac, idx = rule.get('target_device_mac'), rule.get('target_outlet_index'); action = rule.get('action')
                if not r_id or mac is None or idx is None or not action: continue
                key = (mac, idx)
                if key in desired_states and r_id not in active_until_rules: continue # Revert prioritaire
                logic = rule.get('trigger_logic', 'ET'); conditions = rule.get('conditions', [])
                if not conditions: continue
                trigger_met = False
                if logic == 'ET': all_t=True; if not conditions: all_t=False;
                else:
                    for cond in conditions:
                        if not self._check_condition(cond, sensors, now_time): all_t=False; logging.debug(f"R{r_id} SI(ET) échoue: {cond}"); break
                    trigger_met = all_t
                elif logic == 'OU': any_t=False;
                else:
                    for cond in conditions:
                        if self._check_condition(cond, sensors, now_time): any_t=True; logging.debug(f"R{r_id} SI(OU) réussit: {cond}"); break
                    trigger_met = any_t
                else: logging.error(f"Logique SI? {logic} R{r_id}")

                if trigger_met:
                    logging.debug(f"R{r_id} SI({logic}) MET. Action: {action}")
                    if key not in desired_states:
                         desired_states[key] = action
                         if r_id not in active_until_rules and rule.get('until_conditions'):
                             rev_act = 'OFF' if action == 'ON' else 'ON'
                             logging.info(f"R{r_id} Activation UNTIL({rule.get('until_logic','OU')}). Retour: {rev_act}")
                             active_until_rules[r_id] = {'revert_action': rev_act}

            # Appliquer changements Kasa (inchangé)
            all_managed = set((r.get('target_device_mac'), r.get('target_outlet_index')) for r in rules if r.get('target_device_mac') and r.get('target_outlet_index') is not None)
            for key, state in desired_states.items():
                 mac, idx = key; live = self.live_kasa_states.get(mac, {}).get(idx)
                 needed, func = False, None
                 if state=='ON' and live is not True: needed,func=True,'turn_outlet_on'
                 elif state=='OFF' and live is not False: needed,func=True,'turn_outlet_off'
                 if needed:
                     if mac in self.kasa_devices: ctrl=self.kasa_devices[mac]['controller']; logging.info(f"Action: {self.get_alias('dev',mac)} P{self.get_alias('out',mac,idx)} -> {func}"); tasks_to_run.append(getattr(ctrl,func)(idx)); self.live_kasa_states.setdefault(mac,{})[idx]=(state=='ON')
                     else: logging.error(f"{mac} inconnu pour {func}.")
            for mac, outs in self.live_kasa_states.items():
                 for idx, is_on in outs.items():
                     key=(mac,idx)
                     if key in all_managed and key not in desired_states and is_on:
                         if mac in self.kasa_devices: ctrl=self.kasa_devices[mac]['controller']; logging.info(f"Action implicite: OFF {self.get_alias('dev',mac)} P{self.get_alias('out',mac,idx)}"); tasks_to_run.append(ctrl.turn_outlet_off(idx)); self.live_kasa_states[mac][idx]=False
                         else: logging.error(f"{mac} inconnu pour OFF implicite.")
            if tasks_to_run: logging.debug(f"Exec {len(tasks_to_run)} tâches Kasa..."); await asyncio.gather(*tasks_to_run, return_exceptions=True); logging.debug("Tâches Kasa finies.")
            await asyncio.sleep(2)

    # --- Fonction de Vérification de Condition ---
    def _check_condition(self, condition_data, current_sensor_values, current_time_obj):
        cond_type = condition_data.get('type'); operator = condition_data.get('operator'); c_id = condition_data.get('condition_id', 'N/A')
        if not cond_type or not operator: logging.warning(f"Cond invalide (ID:{c_id}): {condition_data}"); return False
        try:
            if cond_type == 'Capteur':
                s_id = condition_data.get('id'); thresh = condition_data.get('threshold')
                if s_id is None or thresh is None or operator not in SENSOR_OPERATORS: logging.warning(f"Cond Capteur invalide (ID:{c_id})"); return False
                if s_id not in current_sensor_values: logging.debug(f"Val manquante {s_id} (Cond ID:{c_id})"); return False
                return self._compare(current_sensor_values[s_id], operator, float(thresh))
            elif cond_type == 'Heure':
                t_str = condition_data.get('value')
                if not t_str or operator not in TIME_OPERATORS: logging.warning(f"Cond Heure invalide (ID:{c_id})"); return False
                target_t = datetime.strptime(t_str, '%H:%M').time()
                logging.debug(f"Comp Temps (Cond ID:{c_id}): {current_time_obj:%H:%M:%S} {operator} {target_t:%H:%M}")
                if operator=='<': return current_time_obj < target_t; elif operator=='>': return current_time_obj > target_t
                elif operator=='<=': return current_time_obj <= target_t; elif operator=='>=': return current_time_obj >= target_t
                curr_min=current_time_obj.hour*60+current_time_obj.minute; targ_min=target_t.hour*60+target_t.minute
                if operator=='=': return curr_min == targ_min; elif operator=='!=': return curr_min != targ_min
                return False
            else: logging.error(f"Type cond inconnu (ID:{c_id}): {cond_type}"); return False
        except ValueError as e: logging.error(f"Err val (ID:{c_id}) cond {condition_data}: {e}"); return False
        except Exception as e: logging.error(f"Err eval cond (ID:{c_id}) {condition_data}: {e}", exc_info=True); return False

    # --- Fonction de Comparaison Numérique ---
    def _compare(self, value1, operator, value2):
        try:
            v1, v2 = float(value1), float(value2)
            logging.debug(f"Comp Num: {v1} {operator} {v2}")
            if operator=='<': return v1 < v2; elif operator=='>': return v1 > v2
            elif operator=='=': return abs(v1-v2)<1e-9; elif operator=='!=': return abs(v1-v2)>=1e-9
            elif operator=='<=': return v1 <= v2; elif operator=='>=': return v1 >= v2
        except(ValueError, TypeError) as e: logging.error(f"Err comp num: {value1} {operator} {value2}-{e}"); return False
        return False

    # --- Fonctions Extinction / Sauvegarde / Fermeture (inchangées) ---
    def _turn_off_all_kasa_safely(self):
        try: loop=asyncio.get_event_loop(); loop.run_until_complete(self._async_turn_off_all()) if loop.is_running() else asyncio.run(self._async_turn_off_all())
        except RuntimeError as e: logging.info(f"RuntimeErr extinction ({e}), use asyncio.run."); asyncio.run(self._async_turn_off_all())
        except Exception as e: logging.error(f"Err finale _async_turn_off_all: {e}")

    async def _async_turn_off_all(self):
        tasks={}; logging.info(f"Extinction Kasa: {len(self.kasa_devices)} dev...")
        for m,d in self.kasa_devices.items(): c=d['controller']; a=self.get_alias('device',m); tasks[f"{a}({m})"]=c.turn_all_outlets_off() if d['info'].get('is_strip') or d['info'].get('is_plug') else asyncio.sleep(0) # Sleep(0) for non-plugs/strips
        if tasks:
            logging.info(f"Exec {len(tasks)} tâches extinction..."); keys=list(tasks.keys()); coros=list(tasks.values()); results=await asyncio.gather(*coros, return_exceptions=True)
            s_c, f_c = 0, 0
            for i, res in enumerate(results): key=keys[i]; (s_c:=s_c+1) if not isinstance(res, Exception) else (logging.error(f"Err extinction '{key}': {res}"), f_c:=f_c+1)
            logging.info(f"Extinction finie. OK:{s_c}, Err:{f_c}.")
        else: logging.info("Aucune prise Kasa à éteindre.")

    def save_configuration(self):
        logging.info("Prépa sauvegarde...");
        for r_id in list(self.rule_widgets.keys()):
             if r_id in self.rule_widgets: try: self.on_rule_change(r_id) # Màj partie ALORS
             except Exception as e: logging.error(f"Err on_rule_change avant save {r_id}: {e}")
        config_to_save = {"aliases": self.aliases, "rules": self.rules }
        logging.debug(f"Data pour save: {config_to_save}")
        if save_config(config_to_save, DEFAULT_CONFIG_FILE): messagebox.showinfo("Sauvegarde", "Config sauvegardée.", parent=self.root)
        else: messagebox.showerror("Sauvegarde", "Erreur sauvegarde.", parent=self.root)

    def on_closing(self):
        if self.monitoring_active:
            if messagebox.askyesno("Quitter", "Monitoring actif. Arrêter et quitter ?", parent=self.root):
                logging.info("Arrêt monitoring & fermeture..."); self.stop_monitoring(); logging.info("Fermeture app dans 1 sec..."); self.root.after(1000, self.root.destroy)
            else: return
        else:
            if messagebox.askyesno("Quitter", "Êtes-vous sûr de vouloir quitter ?", parent=self.root):
                logging.info("Fermeture (monitoring inactif)..."); logging.info("Extinction Kasa...")
                threading.Thread(target=self._turn_off_all_kasa_safely, daemon=True).start()
                logging.info("Fermeture app dans 1 sec..."); self.root.after(1000, self.root.destroy)

# --- Main Execution ---
if __name__ == "__main__":
    # Décommenter pour voir les logs DEBUG dans la console
    # logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
    root = tk.Tk()
    app = GreenhouseApp(root)
    root.mainloop()