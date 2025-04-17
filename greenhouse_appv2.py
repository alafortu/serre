# greenhouse_appv2.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog, font as tkFont
import asyncio
import threading
import queue
import logging
import uuid
from datetime import datetime, timedelta, time
import copy # Pour deepcopy

# Importer les modules personnalisés (supposés existants et compatibles)
from logger_setup import setup_logging
from discover_device import DeviceDiscoverer
from device_control import DeviceController
from temp_sensor_wrapper import TempSensorManager
from light_sensor import BH1750Manager
from config_manager import load_config, save_config

# --- Constantes ---
OPERATORS = ['<', '>', '=', '!=', '<=', '>=']
TIME_OPERATORS = ['=', '!=', '>', '<', '>=', '<='] # Gardé pour référence, mais utilise OPERATORS
# Types de conditions possibles
CONDITION_TYPES = ['Capteur', 'Heure', 'Timer'] # Ajout de Timer
ACTIONS = ['ON', 'OFF']
LOGIC_OPERATORS = ['ET', 'OU']
DEFAULT_CONFIG_FILE = 'config_v3.yaml' # Nouveau nom

# Tolerance pour comparaison float
FLOAT_TOLERANCE = 1e-7

class GreenhouseApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gestionnaire de Serre (v3 - Logique Imbriquée)")
        try:
            self.root.geometry("1600x900")
        except tk.TclError as e:
            logging.warning(f"Impossible de définir la géométrie initiale: {e}")

        # --- Style ---
        style = ttk.Style(self.root)
        style.configure("Red.TButton", foreground="red", background="white", font=('Helvetica', 8))
        style.map("Red.TButton", foreground=[('pressed', 'white'), ('active', 'white')], background=[('pressed', 'darkred'), ('active', 'red')])
        style.configure("Small.TButton", font=('Helvetica', 8))
        style.configure("AndGroup.TFrame", background="#E0E0FF", borderwidth=1, relief="sunken")
        style.configure("OrGroup.TFrame", background="#FFE0E0", borderwidth=1, relief="sunken")
        style.configure("Condition.TFrame", borderwidth=1, relief="groove")
        style.configure("Action.TFrame", borderwidth=1, relief="flat", padding=5) # Style pour bloc action
        # Configure le style par défaut des LabelFrame pour utiliser une police plus petite pour le titre
        default_font = tkFont.nametofont("TkDefaultFont")
        label_frame_font = default_font.copy()
        label_frame_font.config(size=9, weight='bold') # Ajuster taille/poids si besoin
        style.configure("TLabelframe.Label", font=label_frame_font)
        style.configure("TLabelframe", padding=5)


        # --- Initialisation Backend ---
        self.log_queue = queue.Queue()
        setup_logging(self.log_queue)

        self.config = load_config(DEFAULT_CONFIG_FILE)
        self.aliases = self.config.get('aliases', {"sensors": {}, "devices": {}, "outlets": {}})
        self.rules = self.config.get('rules', [])

        # --- Vérification et Nettoyage Initial des Règles Chargées ---
        self._validate_and_clean_rules()


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
        # Stocke les infos des règles dont le UNTIL est en cours d'évaluation
        self.active_until_triggers = {} # {rule_id: {'revert_action': 'ON'|'OFF', 'start_time': datetime, 'original_action_block': dict}}

        self.rule_widgets = {}

        # --- Interface Utilisateur ---
        self.create_widgets()
        self.update_device_lists_for_ui()
        self.populate_initial_ui_data()
        self.update_log_display()

        self.discover_all_devices()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _validate_and_clean_rules(self):
        """Vérifie la structure des règles chargées et ajoute les clés manquantes avec des valeurs par défaut."""
        rules_to_keep = []
        for i, rule in enumerate(self.rules):
            if not isinstance(rule, dict):
                logging.warning(f"Règle invalide (non dict) à l'index {i}, ignorée.")
                continue

            rule_id = rule.get('id')
            if not rule_id:
                 rule['id'] = str(uuid.uuid4()) # Donner un ID si manquant
                 logging.warning(f"Règle sans ID à l'index {i}, ID généré: {rule['id']}")

            # Valeurs par défaut pour les clés principales
            rule['name'] = rule.get('name', f"Règle importée {rule['id'][:4]}")
            rule['enabled'] = rule.get('enabled', True)

            # Valider/Nettoyer récursivement les blocs de conditions
            rule['trigger_conditions'] = self._validate_clean_condition_block(rule.get('trigger_conditions'), default_logic='ET')
            rule['until_conditions'] = self._validate_clean_condition_block(rule.get('until_conditions'), default_logic='OU')

            # Valider bloc action
            action_block = rule.get('action_block', {})
            action_block['target_device_mac'] = action_block.get('target_device_mac') # Garde None si absent
            action_block['target_outlet_index'] = action_block.get('target_outlet_index') # Garde None si absent
            action_block['action'] = action_block.get('action', 'ON') # Défaut ON
            if action_block['action'] not in ACTIONS: action_block['action'] = 'ON'
            rule['action_block'] = action_block

            rules_to_keep.append(rule)

        if len(rules_to_keep) != len(self.rules):
             logging.info(f"{len(self.rules) - len(rules_to_keep)} règle(s) invalide(s) ont été supprimée(s) au chargement.")
        self.rules = rules_to_keep


    def _validate_clean_condition_block(self, block_data, default_logic='ET'):
        """Nettoie et valide récursivement un bloc de conditions."""
        if not isinstance(block_data, dict):
            # Si ce n'est pas un dict, on crée une structure vide par défaut
            return {'logic': default_logic, 'conditions': []}

        # Assurer que 'logic' et 'conditions' existent
        block_data['logic'] = block_data.get('logic', default_logic)
        if block_data['logic'] not in LOGIC_OPERATORS:
            block_data['logic'] = default_logic

        conditions = block_data.get('conditions', [])
        if not isinstance(conditions, list):
            conditions = [] # Réinitialiser si ce n'est pas une liste

        cleaned_conditions = []
        for cond in conditions:
            if isinstance(cond, dict):
                if 'logic' in cond: # C'est un sous-groupe
                    cleaned_sub_group = self._validate_clean_condition_block(cond, default_logic=cond.get('logic', 'ET'))
                    # Ne pas ajouter un groupe vide sauf si c'est la racine (géré par l'appelant)
                    if cleaned_sub_group.get('conditions'):
                         cleaned_conditions.append(cleaned_sub_group)
                    else:
                         logging.debug(f"Sous-groupe vide ignoré: {cond}")
                else: # C'est une condition simple
                    cleaned_simple_cond = self._validate_clean_simple_condition(cond)
                    if cleaned_simple_cond: # Ajouter seulement si valide
                        cleaned_conditions.append(cleaned_simple_cond)
                    else:
                        logging.warning(f"Condition simple invalide ignorée: {cond}")
            else:
                logging.warning(f"Élément de condition invalide (non dict) ignoré: {cond}")

        block_data['conditions'] = cleaned_conditions
        return block_data


    def _validate_clean_simple_condition(self, cond_data):
        """Nettoie et valide une condition simple, retourne None si invalide."""
        if not isinstance(cond_data, dict): return None

        cond_type = cond_data.get('type')
        operator = cond_data.get('operator')
        value = cond_data.get('value') # Ne pas vérifier None ici car type 'Timer' n'a pas 'value'

        if cond_type not in CONDITION_TYPES: return None
        if operator not in OPERATORS: return None # Tous les types utilisent les mêmes opérateurs pour l'instant

        cleaned_cond = {'type': cond_type, 'operator': operator}

        if cond_type == 'Capteur':
            sensor_id = cond_data.get('id')
            if sensor_id is None: return None # ID Requis
            try:
                cleaned_cond['value'] = float(str(value).replace(',', '.'))
                cleaned_cond['id'] = str(sensor_id) # Assurer string
            except (ValueError, TypeError): return None # Valeur invalide
        elif cond_type == 'Heure':
            try:
                # Valider format HH:MM
                datetime.strptime(str(value), '%H:%M')
                cleaned_cond['value'] = str(value)
            except (ValueError, TypeError): return None # Format invalide
        elif cond_type == 'Timer':
            try:
                duration = int(value)
                if duration <= 0: return None # Durée doit être positive
                cleaned_cond['value'] = duration # Stocker la durée comme valeur
                # L'opérateur n'est pas pertinent pour le Timer, mais la clé doit exister
                cleaned_cond['operator'] = cond_data.get('operator', '=') # Mettre un opérateur par défaut
            except (ValueError, TypeError): return None # Doit être un entier

        return cleaned_cond


    # --- Fonctions Alias (inchangées ou mineures adaptations) ---
    # ... (get_alias, update_alias, edit_alias_dialog, refresh_ui_after_alias_change comme avant) ...
    def get_alias(self, item_type, item_id, sub_id=None):
        try:
            if item_type == 'sensor':
                return self.aliases.get('sensors', {}).get(str(item_id), str(item_id))
            elif item_type == 'device':
                return self.aliases.get('devices', {}).get(str(item_id), str(item_id))
            elif item_type == 'outlet':
                device_outlets = self.aliases.get('outlets', {}).get(str(item_id), {})
                fallback_name = f"Prise {sub_id}"
                if str(item_id) in self.kasa_devices:
                    outlet_info = next((o for o in self.kasa_devices[str(item_id)].get('info',{}).get('outlets',[]) if o.get('index') == sub_id), None)
                    if outlet_info:
                        fallback_name = outlet_info.get('alias', fallback_name)
                return device_outlets.get(str(sub_id), fallback_name)
        except KeyError:
            logging.warning(f"Clé manquante dans get_alias pour {item_type} {item_id} {sub_id}")
            pass

        if sub_id is not None:
            if item_type == 'outlet' and str(item_id) in self.kasa_devices:
                outlet_info = next((o for o in self.kasa_devices[str(item_id)].get('info',{}).get('outlets',[]) if o.get('index') == sub_id), None)
                if outlet_info:
                    return outlet_info.get('alias', f"Prise {sub_id}")
            return f"{item_id} - Prise {sub_id}"
        return str(item_id)


    def update_alias(self, item_type, item_id, new_alias, sub_id=None):
        if 'aliases' not in self.config: self.config['aliases'] = {"sensors": {}, "devices": {}, "outlets": {}}
        for key in ['sensors', 'devices', 'outlets']:
             if key not in self.config['aliases']: self.config['aliases'][key] = {}

        item_id_str = str(item_id)
        sub_id_str = str(sub_id) if sub_id is not None else None

        if item_type == 'outlet':
            if item_id_str not in self.config['aliases']['outlets']:
                 self.config['aliases']['outlets'][item_id_str] = {}
            self.config['aliases']['outlets'][item_id_str][sub_id_str] = new_alias
        elif item_type == 'device':
             self.config['aliases']['devices'][item_id_str] = new_alias
        elif item_type == 'sensor':
             self.config['aliases']['sensors'][item_id_str] = new_alias
        else:
            logging.error(f"Type d'item inconnu pour l'alias: {item_type}")
            return

        self.aliases = self.config['aliases']
        logging.info(f"Alias mis à jour pour {item_type} {item_id_str}" + (f"[{sub_id_str}]" if sub_id is not None else "") + f": '{new_alias}'")

    def edit_alias_dialog(self, item_type, item_id, current_name, sub_id=None):
        prompt = f"Entrez un nouveau nom pour {item_type} '{current_name}'"
        if item_type == 'outlet':
             device_name = self.get_alias('device', item_id)
             prompt = f"Entrez un nouveau nom pour la prise '{current_name}'\n(Appareil: {device_name} / ID: {item_id}, Index: {sub_id})"
        elif item_type == 'device':
             prompt = f"Entrez un nouveau nom pour l'appareil '{current_name}'\n(MAC: {item_id})"
        elif item_type == 'sensor':
             prompt = f"Entrez un nouveau nom pour le capteur '{current_name}'\n(ID: {item_id})"


        new_name = simpledialog.askstring("Modifier Alias", prompt, initialvalue=current_name, parent=self.root)

        if new_name and new_name != current_name:
            self.update_alias(item_type, item_id, new_name, sub_id)
            self.refresh_ui_after_alias_change() # Fonction pour regrouper les rafraîchissements

    def refresh_ui_after_alias_change(self):
        """Met à jour tous les éléments UI affectés par un changement d'alias."""
        self.update_device_lists_for_ui() # Met à jour available_*
        self.repopulate_all_rule_dropdowns() # Met à jour les dropdowns dans les règles existantes
        self.update_status_display() # Met à jour les labels dans la section statut
        self.root.update_idletasks()


    # --- Création Widgets (Structure générale similaire) ---
    def create_widgets(self):
        """Crée tous les widgets principaux de l'interface utilisateur."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Cadre des Règles (Haut) ---
        # Utilise un LabelFrame stylisé
        rules_frame_container = ttk.LabelFrame(main_frame, text="Règles d'Automatisation", padding="10")
        rules_frame_container.pack(fill=tk.X, expand=False, pady=5)

        # Canvas et Scrollbar pour les règles
        self.rules_canvas = tk.Canvas(rules_frame_container, borderwidth=0, background="#FFFFFF") # Fond blanc pour le canvas
        scrollbar = ttk.Scrollbar(rules_frame_container, orient="vertical", command=self.rules_canvas.yview)
        self.scrollable_rules_frame = ttk.Frame(self.rules_canvas) # Frame à l'intérieur du canvas
        self.scrollable_rules_frame.bind("<Configure>", lambda e: self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all")))

        self.rules_canvas.create_window((0, 0), window=self.scrollable_rules_frame, anchor="nw")
        self.rules_canvas.configure(yscrollcommand=scrollbar.set)

        self.rules_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.rules_canvas.config(height=350) # Hauteur initiale pour le canvas des règles

        # Bouton Ajouter Règle (placé sous le cadre des règles)
        add_rule_button = ttk.Button(main_frame, text="➕ Ajouter une Règle", command=self.add_rule_ui)
        add_rule_button.pack(pady=5)

        # --- Cadre des Contrôles (Milieu) ---
        control_frame = ttk.Frame(main_frame, padding="10")
        control_frame.pack(fill=tk.X, expand=False, pady=5)

        self.start_button = ttk.Button(control_frame, text="🟢 Gérer ma Serre", command=self.start_monitoring)
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = ttk.Button(control_frame, text="🔴 Arrêter", command=self.stop_monitoring, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        save_button = ttk.Button(control_frame, text="💾 Sauvegarder Configuration", command=self.save_configuration)
        save_button.pack(side=tk.RIGHT, padx=5)


        # --- Cadre Statut et Logs (Bas) ---
        status_log_frame = ttk.Frame(main_frame, padding="10")
        status_log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        # --- Configuration de la grille pour status_log_frame ---
        # La colonne 0 (status) aura un poids de 1 et une taille minimale de 400
        status_log_frame.columnconfigure(0, weight=1, minsize=400)
        # La colonne 1 (log) aura un poids de 1 (partage l'espace restant)
        status_log_frame.columnconfigure(1, weight=1)
        # La rangée 0 prendra toute la hauteur disponible
        status_log_frame.rowconfigure(0, weight=1)

        # --- Section Statut ---
        status_frame = ttk.LabelFrame(status_log_frame, text="Statut Actuel", padding="10")
        # Utiliser grid() au lieu de pack()
        status_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=0) # nsew = fill BOTH

        # Canvas et Scrollbar pour le statut (placés DANS status_frame, leur géométrie interne ne change pas)
        # Mettre un fond au canvas pour voir sa zone
        status_canvas = tk.Canvas(status_frame, borderwidth=0, background="#F0F0F0")
        status_scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=status_canvas.yview)
        self.scrollable_status_frame = ttk.Frame(status_canvas) # Le frame qui contient les labels de statut
        self.scrollable_status_frame.bind("<Configure>", lambda e: status_canvas.configure(scrollregion=status_canvas.bbox("all")))

        status_canvas.create_window((0, 0), window=self.scrollable_status_frame, anchor="nw")
        status_canvas.configure(yscrollcommand=status_scrollbar.set)
        # Pack le canvas et la scrollbar pour qu'ils remplissent le status_frame
        status_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_labels = {} # Réinitialisé dans update_status_display

        # --- Section Logs ---
        log_frame = ttk.LabelFrame(status_log_frame, text="Journal d'Événements", padding="10")
        # Utiliser grid() au lieu de pack()
        log_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=0) # nsew = fill BOTH

        self.log_display = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=15)
        # Pack le widget de log pour qu'il remplisse le log_frame
        self.log_display.pack(fill=tk.BOTH, expand=True)

    # --- Peuplement Initial UI ---
    def populate_initial_ui_data(self):
        """Remplit l'UI avec les règles complexes chargées depuis self.rules."""
        logging.debug(f"Population initiale UI depuis self.rules: {len(self.rules)} règle(s)")
        for widget in self.scrollable_rules_frame.winfo_children():
            widget.destroy()
        self.rule_widgets = {}

        for rule_data in self.rules:
             # S'assurer que la règle est valide avant de tenter de créer l'UI
             if isinstance(rule_data, dict) and rule_data.get('id'):
                 self.add_rule_ui(rule_data=rule_data)
             else:
                 logging.error(f"Données de règle invalides lors de la population initiale: {rule_data}")


        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))

    # --- Gestion Règles UI (Refonte Majeure) ---

    def _get_default_rule_structure(self, rule_id):
         """Retourne un dictionnaire représentant une règle vide."""
         return {
            'id': rule_id,
            'name': f"Nouvelle Règle", # Nom simple par défaut
            'enabled': True,
            'trigger_conditions': {'logic': 'ET', 'conditions': []},
            'action_block': {'target_device_mac': None, 'target_outlet_index': None, 'action': 'ON'},
            'until_conditions': {'logic': 'OU', 'conditions': []}
        }

    def add_rule_ui(self, rule_data=None):
        """Ajoute une règle complète (potentiellement complexe) à l'UI."""
        is_new_rule = False
        if not rule_data:
            is_new_rule = True
            rule_id = str(uuid.uuid4())
            rule_data = self._get_default_rule_structure(rule_id)
            rule_data['name'] = f"Nouvelle Règle {rule_id[:4]}" # Nom unique par défaut
            self.rules.append(rule_data) # Ajouter aux données internes
            logging.info(f"Ajout d'une nouvelle règle vide: {rule_id}")
        else:
            rule_id = rule_data.get('id')
            if not rule_id:
                logging.error("Tentative d'ajout de règle UI sans ID dans rule_data.")
                return
            # S'assurer qu'on a la bonne référence si la règle existe déjà
            found = False
            for i, r in enumerate(self.rules):
                 if r.get('id') == rule_id:
                     rule_data = r # Utiliser la référence existante
                     found = True
                     break
            if not found:
                 # Ne devrait pas arriver si populate_initial_ui_data est correct
                 logging.warning(f"Données pour règle {rule_id} non trouvées dans self.rules, ajout.")
                 self.rules.append(rule_data)


        # --- Cadre principal pour la règle ---
        rule_frame = ttk.LabelFrame(self.scrollable_rules_frame, text=rule_data.get('name', 'Sans Nom'), padding=5)
        rule_frame.rule_id = rule_id
        rule_frame.pack(fill=tk.X, pady=5, padx=5, anchor="nw")

        rule_data_ref = rule_data # Référence directe

        # --- Ligne d'en-tête ---
        header_frame = ttk.Frame(rule_frame)
        header_frame.pack(fill=tk.X, pady=(0, 5))

        ttk.Label(header_frame, text="Nom:", anchor='w').pack(side=tk.LEFT, padx=(0, 2))
        name_var = tk.StringVar(value=rule_data.get('name', ''))
        name_entry = ttk.Entry(header_frame, textvariable=name_var, width=30)
        name_entry.pack(side=tk.LEFT, padx=(0, 10))
        # Mettre à jour le titre du LabelFrame et les données en perdant le focus ou Entrée
        name_entry.bind("<FocusOut>", lambda e, r_frame=rule_frame, rid=rule_id, var=name_var: self._update_rule_name(r_frame, rid, var.get()))
        name_entry.bind("<Return>", lambda e, r_frame=rule_frame, rid=rule_id, var=name_var: self._update_rule_name(r_frame, rid, var.get()))

        enabled_var = tk.BooleanVar(value=rule_data.get('enabled', True))
        enabled_check = ttk.Checkbutton(header_frame, text="Activée", variable=enabled_var,
                                        command=lambda rid=rule_id, var=enabled_var: self._update_rule_data_value(rid, 'enabled', var.get()))
        enabled_check.pack(side=tk.LEFT, padx=5)

        delete_rule_button = ttk.Button(header_frame, text="❌ Supprimer Règle", style="Red.TButton", width=18,
                                       command=lambda rid=rule_id: self._delete_rule_clicked(rid))
        delete_rule_button.pack(side=tk.RIGHT, padx=5)

        header_widgets = {'name_var': name_var, 'name_entry': name_entry, 'enabled_var': enabled_var, 'enabled_check': enabled_check}

        # --- Bloc SI (Trigger Conditions) ---
        trigger_frame = ttk.LabelFrame(rule_frame, text="SI (Conditions de Déclenchement)", padding=5)
        trigger_frame.pack(fill=tk.X, expand=True, pady=3, padx=3)
        # S'assurer que la clé existe dans les données
        if 'trigger_conditions' not in rule_data_ref: rule_data_ref['trigger_conditions'] = {'logic': 'ET', 'conditions': []}
        self._populate_condition_block_ui(trigger_frame, rule_data_ref['trigger_conditions'], rule_id, 'trigger_conditions')

        # --- Bloc ALORS (Action) ---
        action_frame = ttk.LabelFrame(rule_frame, text="ALORS (Action)", padding=5)
        action_frame.pack(fill=tk.X, expand=True, pady=3, padx=3)
        if 'action_block' not in rule_data_ref: rule_data_ref['action_block'] = {'action': 'ON'} # Init si manque
        self._create_action_block_ui(action_frame, rule_data_ref['action_block'], rule_id)

        # --- Bloc JUSQU'À (Until Conditions) ---
        until_frame = ttk.LabelFrame(rule_frame, text="JUSQU'À (Optionnel - Condition d'Arrêt)", padding=5)
        until_frame.pack(fill=tk.X, expand=True, pady=3, padx=3)
        if 'until_conditions' not in rule_data_ref: rule_data_ref['until_conditions'] = {'logic': 'OU', 'conditions': []}
        self._populate_condition_block_ui(until_frame, rule_data_ref['until_conditions'], rule_id, 'until_conditions')


        # Stocker les références importantes
        self.rule_widgets[rule_id] = {
            'frame': rule_frame,
            'data_ref': rule_data_ref,
            'header_widgets': header_widgets,
            'trigger_frame': trigger_frame,
            'action_frame': action_frame,
            'until_frame': until_frame
        }

        if is_new_rule: # Si nouvelle règle, scroller vers elle
            self.root.update_idletasks() # Pour que bbox soit correct
            self.rules_canvas.yview_moveto(1.0) # Aller à la fin

        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))

    def _update_rule_name(self, rule_frame, rule_id, new_name):
         """Met à jour le nom de la règle dans les données et l'UI."""
         rule_frame.config(text=new_name) # Met à jour le titre du LabelFrame
         self._update_rule_data_value(rule_id, 'name', new_name)


    def _update_rule_data_value(self, rule_id, key, value):
         """Met à jour une valeur simple (name, enabled) dans les données de la règle."""
         if rule_id in self.rule_widgets:
             rule_data = self.rule_widgets[rule_id]['data_ref']
             if rule_data.get(key) != value:
                 rule_data[key] = value
                 logging.debug(f"Données règle {rule_id} mises à jour: {key} = {value}")
         else:
             logging.error(f"Tentative de mise à jour de '{key}' pour règle {rule_id} non trouvée.")


    def _populate_condition_block_ui(self, parent_frame, block_data, rule_id, block_key, indent_level=0):
        """Popule l'UI pour un bloc de conditions (trigger ou until)."""
        for widget in parent_frame.winfo_children():
            widget.destroy()

        if not block_data or not isinstance(block_data, dict) or 'logic' not in block_data:
             logging.error(f"Données de bloc invalides pour {rule_id}/{block_key}: {block_data}")
             # Créer une structure par défaut pour permettre l'ajout
             block_data = {'logic': 'ET' if block_key == 'trigger_conditions' else 'OU', 'conditions': []}
             # Mettre à jour la référence dans la règle parente !
             if rule_id in self.rule_widgets:
                 self.rule_widgets[rule_id]['data_ref'][block_key] = block_data
             else: # Ne devrait pas arriver
                 logging.error(f"Impossible de corriger les données du bloc car règle {rule_id} non trouvée dans rule_widgets.")


        # On assume que block_data est maintenant un dict valide avec 'logic' et 'conditions'
        # Créer l'UI pour le groupe logique racine du bloc. C'est toujours un groupe logique.
        self._create_logic_group_ui(parent_frame, block_data, rule_id, block_key, is_root=True, indent_level=indent_level)


    def _create_logic_group_ui(self, parent_widget, group_data, rule_id, block_key, is_root=False, indent_level=0):
        """Crée l'UI pour un groupe logique (ET/OU)."""
        logic = group_data.get('logic', 'ET')
        style = "AndGroup.TFrame" if logic == 'ET' else "OrGroup.TFrame"

        group_frame = ttk.Frame(parent_widget, padding=5, style=style)
        group_frame.pack(fill=tk.X, expand=True, pady=3, padx=(indent_level * 15, 5)) # Applique l'indentation ici
        # --- Stocker les références ---
        group_frame.logic_data_ref = group_data
        group_frame.rule_id = rule_id
        group_frame.block_key = block_key
        group_frame.is_root = is_root
        group_frame.indent_level = indent_level # <-- Stocker le niveau d'indentation

        # --- Toolbar ---
        toolbar = ttk.Frame(group_frame)
        toolbar.pack(fill=tk.X)

        logic_label_text = f" {logic} "
        logic_label = ttk.Label(toolbar, text=logic_label_text, relief="raised", padding=(5, 1))
        logic_label.pack(side=tk.LEFT, padx=5)

        # Bouton pour changer ET/OU
        switch_logic_button = ttk.Button(toolbar, text="🔄", style="Small.TButton", width=3,
                                         command=lambda g_frame=group_frame, g_data=group_data: self._switch_logic_clicked(g_frame, g_data))
        switch_logic_button.pack(side=tk.LEFT, padx=(0,5))

        # Boutons pour ajouter DANS ce groupe
        add_cond_button = ttk.Button(toolbar, text="➕ Condition", style="Small.TButton",
                                   command=lambda p_frame=group_frame, g_data=group_data, r_id=rule_id, b_key=block_key: self._add_condition_or_group_clicked(p_frame, g_data, r_id, b_key, 'condition'))
        add_cond_button.pack(side=tk.LEFT, padx=5)

        add_and_group_button = ttk.Button(toolbar, text="➕ Groupe ET", style="Small.TButton",
                                       command=lambda p_frame=group_frame, g_data=group_data, r_id=rule_id, b_key=block_key: self._add_condition_or_group_clicked(p_frame, g_data, r_id, b_key, 'group_and'))
        add_and_group_button.pack(side=tk.LEFT, padx=5)

        add_or_group_button = ttk.Button(toolbar, text="➕ Groupe OU", style="Small.TButton",
                                      command=lambda p_frame=group_frame, g_data=group_data, r_id=rule_id, b_key=block_key: self._add_condition_or_group_clicked(p_frame, g_data, r_id, b_key, 'group_or'))
        add_or_group_button.pack(side=tk.LEFT, padx=5)

        # Bouton supprimer groupe (sauf racine)
        if not is_root:
            delete_button = ttk.Button(toolbar, text="🗑️", style="Red.TButton", width=3,
                                       command=lambda g_frame=group_frame: self._delete_condition_or_group_clicked(g_frame))
            delete_button.pack(side=tk.RIGHT, padx=5)

        # --- Conteneur pour les conditions/sous-groupes internes ---
        conditions_container = ttk.Frame(group_frame, padding=(0, 5, 0, 0))
        conditions_container.pack(fill=tk.X, expand=True)
        group_frame.conditions_container = conditions_container # Référence pour ajouter dedans

        # Créer récursivement l'UI pour les éléments internes
        conditions_list = group_data.get('conditions', [])
        for item_data in conditions_list:
            if isinstance(item_data, dict): # Vérifier que c'est bien un dict
                 if 'logic' in item_data: # C'est un sous-groupe
                     self._create_logic_group_ui(conditions_container, item_data, rule_id, block_key, is_root=False, indent_level=indent_level + 1)
                 else: # C'est une condition simple
                     self._create_condition_ui(conditions_container, item_data, rule_id, block_key, indent_level=indent_level + 1)
            else:
                 logging.warning(f"Item invalide dans la liste de conditions pour {rule_id}/{block_key}: {item_data}")

    def _create_condition_ui(self, parent_widget, condition_data, rule_id, block_key, indent_level=0):
        """Crée l'UI pour une condition simple (Capteur, Heure, Timer)."""
        condition_frame = ttk.Frame(parent_widget, padding=5, style="Condition.TFrame")
        condition_frame.pack(fill=tk.X, expand=True, pady=2, padx=(indent_level * 15, 5)) # Applique l'indentation ici
        # --- Stocker les références ---
        condition_frame.condition_data_ref = condition_data
        condition_frame.rule_id = rule_id
        condition_frame.block_key = block_key
        condition_frame.indent_level = indent_level # <-- Stocker le niveau d'indentation

        condition_type = condition_data.get('type', 'Capteur') # Défaut Capteur

        # --- Toolbar pour Type et Suppression ---
        toolbar = ttk.Frame(condition_frame)
        toolbar.pack(fill=tk.X)

        ttk.Label(toolbar, text="Type:").pack(side=tk.LEFT, padx=(0, 2))
        type_var = tk.StringVar(value=condition_type)
        # Filtrer 'Timer' pour le bloc SI ? Pour l'instant on le laisse partout.
        # Si vous voulez le filtrer: values = [t for t in CONDITION_TYPES if t != 'Timer'] if block_key == 'trigger_conditions' else CONDITION_TYPES
        values_for_combo = CONDITION_TYPES
        type_combo = ttk.Combobox(toolbar, textvariable=type_var, values=values_for_combo, width=8, state="readonly")
        type_combo.pack(side=tk.LEFT, padx=2)
        # Le changement de type doit recréer les widgets spécifiques ci-dessous
        type_combo.bind('<<ComboboxSelected>>', lambda e, c_frame=condition_frame, c_data=condition_data, t_var=type_var: \
                        self._condition_type_changed(c_frame, c_data, t_var.get()))

        # Bouton Supprimer cette condition (déplacé sur la même ligne que type)
        delete_button = ttk.Button(toolbar, text="🗑️", style="Red.TButton", width=3,
                                   command=lambda c_frame=condition_frame: self._delete_condition_or_group_clicked(c_frame))
        delete_button.pack(side=tk.RIGHT, padx=5)

        # --- Conteneur pour les widgets spécifiques au type ---
        # On met un frame pour pouvoir le vider et le remplir facilement
        specific_widgets_frame = ttk.Frame(condition_frame)
        specific_widgets_frame.pack(fill=tk.X, pady=3)
        condition_frame.specific_widgets_frame = specific_widgets_frame # Sauver référence

        # Peupler les widgets spécifiques initialement
        self._populate_specific_condition_widgets(condition_frame, condition_data, condition_type)


    def _populate_specific_condition_widgets(self, condition_frame, condition_data, condition_type):
        """Remplit le specific_widgets_frame avec les widgets pour le type donné."""
        container = condition_frame.specific_widgets_frame
        for widget in container.winfo_children(): widget.destroy()

        rule_id = condition_frame.rule_id

        if condition_type == 'Capteur':
            ttk.Label(container, text="Capteur:").pack(side=tk.LEFT, padx=(0, 2))
            sensor_var = tk.StringVar()
            sensor_combo = ttk.Combobox(container, textvariable=sensor_var, width=25, state="readonly",
                                        values=[name for name, _id in self.available_sensors])
            sensor_combo.pack(side=tk.LEFT, padx=2)
            current_sensor_id = condition_data.get('id')
            current_sensor_alias = self.get_alias('sensor', current_sensor_id) if current_sensor_id else ""
            if current_sensor_alias in [name for name, _id in self.available_sensors]: sensor_var.set(current_sensor_alias)
            sensor_combo.bind('<<ComboboxSelected>>', lambda e, c_data=condition_data, s_var=sensor_var: \
                              self._update_condition_data_sensor_id(c_data, s_var.get()))

            ttk.Label(container, text="Op:").pack(side=tk.LEFT, padx=(5, 2))
            operator_var = tk.StringVar(value=condition_data.get('operator', '>'))
            operator_combo = ttk.Combobox(container, textvariable=operator_var, values=OPERATORS, width=4, state="readonly")
            operator_combo.pack(side=tk.LEFT, padx=2)
            operator_combo.bind('<<ComboboxSelected>>', lambda e, c_data=condition_data, var=operator_var: self._update_condition_data(c_data, 'operator', var.get()))

            ttk.Label(container, text="Valeur:").pack(side=tk.LEFT, padx=(5, 2))
            value_var = tk.StringVar(value=str(condition_data.get('value', '0.0')))
            value_entry = ttk.Entry(container, textvariable=value_var, width=8)
            value_entry.pack(side=tk.LEFT, padx=2)
            value_entry.bind('<FocusOut>', lambda e, c_data=condition_data, var=value_var: self._update_condition_data_numeric(c_data, 'value', var.get(), is_float=True))
            value_entry.bind('<Return>', lambda e, c_data=condition_data, var=value_var: self._update_condition_data_numeric(c_data, 'value', var.get(), is_float=True))


        elif condition_type == 'Heure':
            ttk.Label(container, text="Op:").pack(side=tk.LEFT, padx=(5, 2))
            time_op_var = tk.StringVar(value=condition_data.get('operator', '>='))
            time_op_combo = ttk.Combobox(container, textvariable=time_op_var, values=OPERATORS, width=4, state="readonly")
            time_op_combo.pack(side=tk.LEFT, padx=2)
            time_op_combo.bind('<<ComboboxSelected>>', lambda e, c_data=condition_data, var=time_op_var: self._update_condition_data(c_data, 'operator', var.get()))

            ttk.Label(container, text="Heure (HH:MM):").pack(side=tk.LEFT, padx=(5, 2))
            current_value_str = str(condition_data.get('value', '08:00'))
            try: current_h, current_m = map(int, current_value_str.split(':'))
            except: current_h, current_m = 8, 0

            hour_var = tk.StringVar(value=f"{current_h:02d}")
            hour_spin = ttk.Spinbox(container, from_=0, to=23, textvariable=hour_var, wrap=True, width=3, format="%02.0f",
                                   command=lambda c_data=condition_data, h_var=hour_var, m_var=None: self._update_condition_data_time_wrapper(c_data, h_var, m_var)
                                   )
            hour_spin.pack(side=tk.LEFT)
            ttk.Label(container, text=":").pack(side=tk.LEFT)
            minute_var = tk.StringVar(value=f"{current_m:02d}")
            minute_spin = ttk.Spinbox(container, from_=0, to=59, textvariable=minute_var, wrap=True, width=3, format="%02.0f",
                                     command=lambda c_data=condition_data, h_var=hour_var, m_var=minute_var: self._update_condition_data_time(c_data, h_var, m_var)
                                     )
            minute_spin.pack(side=tk.LEFT, padx=(0, 5))
            # Lier la commande de hour_spin pour qu'elle ait accès à minute_var
            hour_spin.config(command=lambda c_data=condition_data, h_var=hour_var, m_var=minute_var: self._update_condition_data_time(c_data, h_var, m_var))
            hour_spin.bind('<FocusOut>', lambda e, c_data=condition_data, h_var=hour_var, m_var=minute_var: self._update_condition_data_time(c_data, h_var, m_var))
            minute_spin.bind('<FocusOut>', lambda e, c_data=condition_data, h_var=hour_var, m_var=minute_var: self._update_condition_data_time(c_data, h_var, m_var))

            # Stocker les références aux vars pour _update_condition_data_time_wrapper
            hour_spin.minute_var_ref = minute_var # Stocker référence var minute sur spinbox heure
            minute_spin.hour_var_ref = hour_var # Et inversement

        elif condition_type == 'Timer':
             # Le Timer est principalement utilisé dans JUSQU'À.
             # L'opérateur n'a pas vraiment de sens ici, mais on le garde pour la structure.
             ttk.Label(container, text="Op:").pack(side=tk.LEFT, padx=(5, 2))
             timer_op_var = tk.StringVar(value=condition_data.get('operator', '='))
             timer_op_combo = ttk.Combobox(container, textvariable=timer_op_var, values=['='], width=4, state="readonly") # Seul '=' a du sens
             timer_op_combo.pack(side=tk.LEFT, padx=2)
             timer_op_combo.bind('<<ComboboxSelected>>', lambda e, c_data=condition_data, var=timer_op_var: self._update_condition_data(c_data, 'operator', var.get()))

             ttk.Label(container, text="Durée (secondes):").pack(side=tk.LEFT, padx=(5, 2))
             duration_var = tk.StringVar(value=str(condition_data.get('value', 60))) # Valeur = durée en secondes
             duration_entry = ttk.Entry(container, textvariable=duration_var, width=8)
             duration_entry.pack(side=tk.LEFT, padx=2)
             # Mettre à jour la 'value' (durée) en perdant focus ou Entrée
             duration_entry.bind('<FocusOut>', lambda e, c_data=condition_data, var=duration_var: self._update_condition_data_numeric(c_data, 'value', var.get(), is_float=False))
             duration_entry.bind('<Return>', lambda e, c_data=condition_data, var=duration_var: self._update_condition_data_numeric(c_data, 'value', var.get(), is_float=False))

        else:
            ttk.Label(container, text=f"Type '{condition_type}' non implémenté.").pack()


    # Wrapper pour la commande du spinbox heure qui n'a pas accès à la var minute au moment du lambda
    def _update_condition_data_time_wrapper(self, condition_data_ref, hour_var, minute_var_ref_widget):
         """Wrapper pour obtenir la variable minute depuis le widget heure."""
         if hasattr(minute_var_ref_widget, 'hour_var_ref'): # Vérifier si la référence est prête
             minute_var = minute_var_ref_widget.hour_var_ref.master.minute_var_ref # Accéder via l'autre spinbox
             self._update_condition_data_time(condition_data_ref, hour_var, minute_var)


    def _create_action_block_ui(self, parent_frame, action_data, rule_id):
        """Crée l'UI pour le bloc ALORS."""
        # ... (Comme avant, s'assure que les widgets sont créés et liés) ...
        for widget in parent_frame.winfo_children(): widget.destroy()
        row1 = ttk.Frame(parent_frame)
        row1.pack(fill=tk.X, pady=2)

        ttk.Label(row1, text="Appareil Kasa:").pack(side=tk.LEFT, padx=(0, 2))
        kasa_var = tk.StringVar()
        kasa_combo = ttk.Combobox(row1, textvariable=kasa_var, width=30, state="readonly",
                                  values=[name for name, _mac in self.available_kasa_strips])
        kasa_combo.pack(side=tk.LEFT, padx=2)
        kasa_combo.rule_id = rule_id # Stocker pour référence dans callbacks

        ttk.Label(row1, text="Prise:").pack(side=tk.LEFT, padx=(5, 2))
        outlet_var = tk.StringVar()
        outlet_combo = ttk.Combobox(row1, textvariable=outlet_var, width=25, state="readonly")
        outlet_combo.pack(side=tk.LEFT, padx=2)
        outlet_combo.rule_id = rule_id

        ttk.Label(row1, text="Action:").pack(side=tk.LEFT, padx=(5, 2))
        action_var = tk.StringVar(value=action_data.get('action', 'ON'))
        action_combo = ttk.Combobox(row1, textvariable=action_var, values=ACTIONS, width=5, state="readonly")
        action_combo.pack(side=tk.LEFT, padx=2)
        action_combo.rule_id = rule_id

        current_kasa_mac = action_data.get('target_device_mac')
        current_outlet_index = action_data.get('target_outlet_index')
        current_kasa_alias = self.get_alias('device', current_kasa_mac) if current_kasa_mac else ""

        if current_kasa_alias in kasa_combo['values']:
            kasa_var.set(current_kasa_alias)
            self._update_action_outlet_options(action_data, outlet_combo, outlet_var, current_kasa_mac, current_outlet_index)
        else:
             # Si la MAC sauvegardée ne correspond à aucun alias connu (appareil déconnecté?), vider
             kasa_var.set("")
             action_data['target_device_mac'] = None # Nettoyer donnée interne
             action_data['target_outlet_index'] = None
             outlet_combo['values'] = []
             outlet_var.set('')

        # Liaisons
        kasa_combo.bind('<<ComboboxSelected>>', lambda e, a_data=action_data, k_var=kasa_var, o_combo=outlet_combo, o_var=outlet_var: \
                        self._update_action_kasa_selection(a_data, k_var.get(), o_combo, o_var))
        outlet_combo.bind('<<ComboboxSelected>>', lambda e, a_data=action_data, k_var=kasa_var, o_var=outlet_var: \
                          self._update_action_outlet_selection(a_data, k_var.get(), o_var.get()))
        action_combo.bind('<<ComboboxSelected>>', lambda e, a_data=action_data, var=action_var: self._update_action_data(a_data, 'action', var.get()))


    # --- Fonctions Helper pour UI Règles ---
    # ... (_update_action_data, _update_action_kasa_selection, _update_action_outlet_options, _update_action_outlet_selection)
    # ... (_get_mac_from_alias, _get_sensor_id_from_alias)
    # ... (_condition_type_changed, _update_condition_data, _update_condition_data_sensor_id)
    # ... (_update_condition_data_numeric, _update_condition_data_time)
    # ... (_switch_logic_clicked, _add_condition_or_group_clicked)
    # ... (_find_parent_data_and_remove, _delete_condition_or_group_clicked, _delete_rule_clicked)
    # --- Toutes ces fonctions helpers restent globalement les mêmes que dans la v2/ébauche précédente ---
    # --- mais elles modifient directement les 'data_ref' attachées aux widgets ---
    def _update_action_data(self, action_data_ref, key, value):
         if action_data_ref.get(key) != value:
             action_data_ref[key] = value
             logging.debug(f"Action data updated: {key} = {value}")

    def _update_action_kasa_selection(self, action_data_ref, selected_kasa_alias, outlet_combo_widget, outlet_var):
         selected_mac = self._get_mac_from_alias(selected_kasa_alias)
         action_data_ref['target_device_mac'] = selected_mac
         action_data_ref['target_outlet_index'] = None
         self._update_action_outlet_options(action_data_ref, outlet_combo_widget, outlet_var, selected_mac, None)
         logging.debug(f"Action data updated: target_device_mac = {selected_mac}, outlet_index reset")

    def _update_action_outlet_options(self, action_data_ref, outlet_combo_widget, outlet_var, selected_mac, preselect_outlet_index):
        outlet_options = []
        current_outlet_alias = ""
        if selected_mac and selected_mac in self.available_outlets:
            outlet_options = [name for name, _index in self.available_outlets[selected_mac]]
            if preselect_outlet_index is not None:
                 current_outlet_alias = next((name for name, index in self.available_outlets[selected_mac] if index == preselect_outlet_index), "")

        outlet_combo_widget['values'] = outlet_options
        if current_outlet_alias:
            outlet_var.set(current_outlet_alias)
        else:
            outlet_var.set('')
            # Important: si on ne présélectionne pas, il faut aussi vider l'index dans les données
            if preselect_outlet_index is None:
                 action_data_ref['target_outlet_index'] = None


    def _update_action_outlet_selection(self, action_data_ref, selected_kasa_alias, selected_outlet_alias):
         selected_mac = self._get_mac_from_alias(selected_kasa_alias)
         selected_index = None
         if selected_mac and selected_mac in self.available_outlets:
              selected_index = next((index for name, index in self.available_outlets[selected_mac] if name == selected_outlet_alias), None)
         action_data_ref['target_outlet_index'] = selected_index
         logging.debug(f"Action data updated: target_outlet_index = {selected_index}")

    def _get_mac_from_alias(self, alias):
         return next((mac for name, mac in self.available_kasa_strips if name == alias), None)

    def _get_sensor_id_from_alias(self, alias):
         return next((sid for name, sid in self.available_sensors if name == alias), None)

    def _condition_type_changed(self, condition_frame, condition_data_ref, new_type):
        logging.debug(f"Type condition changé en '{new_type}' pour {condition_data_ref}")
        condition_data_ref['type'] = new_type
        # Réinitialiser les champs spécifiques (garder opérateur si possible?)
        op = condition_data_ref.get('operator', '=') # Garder l'opérateur
        condition_data_ref.clear() # Vider le dict
        condition_data_ref['type'] = new_type # Remettre le type
        condition_data_ref['operator'] = op # Remettre l'opérateur

        if new_type == 'Capteur':
            condition_data_ref['value'] = 0.0
            condition_data_ref['id'] = None
        elif new_type == 'Heure':
            condition_data_ref['value'] = '00:00'
        elif new_type == 'Timer':
            condition_data_ref['value'] = 60 # Durée par défaut
            # Pas d'ID pour Timer

        self._populate_specific_condition_widgets(condition_frame, condition_data_ref, new_type)

    def _update_condition_data(self, condition_data_ref, key, value):
         if condition_data_ref.get(key) != value:
             condition_data_ref[key] = value
             logging.debug(f"Condition data updated: {key} = {value} in {condition_data_ref}")

    def _update_condition_data_sensor_id(self, condition_data_ref, selected_sensor_alias):
         sensor_id = self._get_sensor_id_from_alias(selected_sensor_alias)
         if condition_data_ref.get('id') != sensor_id:
             condition_data_ref['id'] = sensor_id
             logging.debug(f"Condition data updated: id = {sensor_id} in {condition_data_ref}")

    def _update_condition_data_numeric(self, condition_data_ref, key, value_str, is_float=True):
         try:
             value = float(value_str.replace(',', '.')) if is_float else int(value_str)
             # Vérification additionnelle pour Timer (durée > 0)
             if condition_data_ref.get('type') == 'Timer' and value <= 0:
                  logging.warning(f"Durée Timer invalide '{value}'. Doit être > 0.")
                  # Optionnel: remettre la valeur précédente dans le widget?
                  return
             if condition_data_ref.get(key) != value:
                  condition_data_ref[key] = value
                  logging.debug(f"Condition data updated: {key} = {value} in {condition_data_ref}")
         except (ValueError, TypeError):
              logging.warning(f"Valeur numérique invalide '{value_str}' pour {key} dans {condition_data_ref}. Non mis à jour.")

    def _update_condition_data_time(self, condition_data_ref, hour_var, minute_var):
         try:
             # Assurer que minute_var n'est pas None (peut arriver au premier appel de la commande du spinbox heure)
             if minute_var is None:
                  return
             h = int(hour_var.get())
             m = int(minute_var.get())
             if 0 <= h <= 23 and 0 <= m <= 59:
                 value = f"{h:02d}:{m:02d}"
                 if condition_data_ref.get('value') != value:
                      condition_data_ref['value'] = value
                      logging.debug(f"Condition data updated: value = {value} in {condition_data_ref}")
             else:
                  logging.warning(f"Heure invalide {h:02d}:{m:02d} dans {condition_data_ref}. Non mis à jour.")
         except (ValueError, TypeError, AttributeError) as e:
              logging.warning(f"Erreur lecture heure/minute dans {condition_data_ref}: {e}. Non mis à jour.")

    def _switch_logic_clicked(self, group_frame, group_data):
         current_logic = group_data.get('logic', 'ET')
         new_logic = 'OU' if current_logic == 'ET' else 'ET'
         group_data['logic'] = new_logic
         logging.debug(f"Logique changée en '{new_logic}' pour {group_data}")
         style = "AndGroup.TFrame" if new_logic == 'ET' else "OrGroup.TFrame"
         group_frame.config(style=style)
         toolbar = group_frame.winfo_children()[0]
         logic_label = next((w for w in toolbar.winfo_children() if isinstance(w, ttk.Label) and w.cget('relief') == "raised"), None)
         if logic_label: logic_label.config(text=f" {new_logic} ")


    def _add_condition_or_group_clicked(self, parent_conditions_frame, parent_group_data, rule_id, block_key, item_type):
        """Ajoute une nouvelle condition ou un nouveau groupe logique."""
        logging.debug(f"Ajout '{item_type}' demandé dans parent UI {parent_conditions_frame} / parent data id {id(parent_group_data)}")
        # S'assurer que parent_group_data est un dict et a 'conditions'
        if not isinstance(parent_group_data, dict):
             logging.error(f"Erreur ajout: parent_group_data n'est pas un dict valide pour règle {rule_id}/{block_key}")
             return
        if 'conditions' not in parent_group_data or not isinstance(parent_group_data['conditions'], list):
             parent_group_data['conditions'] = [] # Initialiser/Réinitialiser si nécessaire

        new_item_data = None
        if item_type == 'condition':
            # Valeurs par défaut pour une nouvelle condition
            new_item_data = {'type': 'Capteur', 'operator': '>', 'value': 0.0, 'id': None}
        elif item_type == 'group_and':
            new_item_data = {'logic': 'ET', 'conditions': []}
        elif item_type == 'group_or':
            new_item_data = {'logic': 'OU', 'conditions': []}

        if new_item_data:
            # Ajouter les nouvelles données à la liste 'conditions' du parent
            parent_group_data['conditions'].append(new_item_data)
            logging.debug(f"Nouvelles données ajoutées à parent data id {id(parent_group_data)}: {new_item_data}")

            # Trouver le conteneur UI où ajouter le nouvel élément graphique
            # Le parent_conditions_frame est le cadre du groupe logique parent.
            # Il devrait avoir une référence à son 'conditions_container'
            ui_container = getattr(parent_conditions_frame, 'conditions_container', parent_conditions_frame)
            if not ui_container:
                 logging.error(f"Impossible de trouver le conteneur UI pour ajouter dans {parent_conditions_frame}")
                 # Tenter de retirer les données ajoutées? Complexe. Mieux vaut logger l'erreur.
                 return

            # --- Calcul de l'indentation ---
            # Récupérer le niveau d'indentation du parent depuis l'attribut stocké
            parent_indent_level = getattr(parent_conditions_frame, 'indent_level', -1) # -1 pour détecter erreur potentielle
            if parent_indent_level == -1:
                 logging.warning(f"Attribut 'indent_level' non trouvé sur le parent UI {parent_conditions_frame}. Utilisation de 0.")
                 parent_indent_level = 0
            new_indent_level = parent_indent_level + 1
            # -------------------------------

            logging.debug(f"Ajout UI avec indent_level={new_indent_level} dans container {ui_container}")

            # Créer l'UI pour le nouvel élément avec le bon niveau d'indentation
            try:
                 if 'logic' in new_item_data:
                     self._create_logic_group_ui(ui_container, new_item_data, rule_id, block_key, is_root=False, indent_level=new_indent_level)
                 else:
                     self._create_condition_ui(ui_container, new_item_data, rule_id, block_key, indent_level=new_indent_level)

                 # Mettre à jour la scrollregion après l'ajout effectif des widgets
                 self.root.after(50, lambda: self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))) # Léger délai
                 logging.debug(f"Ajout UI réussi pour {item_type}")

            except Exception as e:
                 logging.error(f"Erreur lors de la création de l'UI pour le nouvel item {item_type}: {e}", exc_info=True)
                 # Essayer de retirer les données ajoutées si l'UI échoue?
                 try:
                     parent_group_data['conditions'].remove(new_item_data)
                     logging.info("Données correspondantes à l'UI échouée ont été retirées.")
                 except ValueError:
                     logging.error("Impossible de retirer les données après échec création UI.")

        else:
            logging.warning(f"Type d'item inconnu '{item_type}' pour ajout.")

    def _find_parent_data_and_remove(self, target_data_ref, search_root):
        """Cherche récursivement target_data_ref dans search_root et le supprime de la liste parente."""
        if isinstance(search_root, dict) and 'conditions' in search_root:
             conditions_list = search_root['conditions']
             # Itérer sur une copie de la liste d'index pour pouvoir supprimer pendant l'itération
             for i in range(len(conditions_list) -1, -1, -1): # Itérer à l'envers
                 item = conditions_list[i]
                 if item is target_data_ref:
                     del conditions_list[i]
                     logging.debug(f"Item {id(target_data_ref)} supprimé de parent {id(search_root)}")
                     return True
                 elif isinstance(item, dict) and 'logic' in item:
                     if self._find_parent_data_and_remove(target_data_ref, item):
                         return True
        return False


    def _delete_condition_or_group_clicked(self, item_frame):
        """Supprime une condition ou un groupe logique de l'UI et des données."""
        rule_id = getattr(item_frame, 'rule_id', None)
        block_key = getattr(item_frame, 'block_key', None)
        data_ref = getattr(item_frame, 'logic_data_ref', getattr(item_frame, 'condition_data_ref', None))

        if not all([rule_id, block_key, data_ref]):
            logging.error("Impossible de supprimer : références manquantes sur le frame.")
            return

        rule_data = next((r for r in self.rules if r.get('id') == rule_id), None)
        if not rule_data:
             logging.error(f"Règle {rule_id} non trouvée pour suppression d'item.")
             return
        block_root_data = rule_data.get(block_key)
        if not block_root_data:
             logging.error(f"Bloc {block_key} non trouvé dans règle {rule_id}.")
             return

        if self._find_parent_data_and_remove(data_ref, block_root_data):
             item_frame.destroy()
             logging.info(f"Condition/Groupe UI supprimé (ref data: {id(data_ref)}).")
             self.root.after(50, lambda: self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all")))
        else:
             logging.error(f"Impossible de trouver/supprimer les données (ref: {id(data_ref)}) dans {block_key} de règle {rule_id}.")


    def _delete_rule_clicked(self, rule_id):
        """Supprime une règle entière."""
        if rule_id in self.rule_widgets:
             frame_to_destroy = self.rule_widgets[rule_id]['frame']
             frame_to_destroy.destroy()
             del self.rule_widgets[rule_id]
             initial_len = len(self.rules)
             self.rules = [rule for rule in self.rules if rule.get('id') != rule_id]
             if len(self.rules) < initial_len: logging.info(f"Règle {rule_id} supprimée des données.")
             else: logging.warning(f"Règle {rule_id} non trouvée dans self.rules lors de suppression.")
             self.rules_canvas.update_idletasks()
             self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))
        else:
            logging.warning(f"Tentative de suppression règle UI {rule_id} non trouvée.")


    # --- Mise à jour Dropdowns ---
    def repopulate_all_rule_dropdowns(self):
        """Met à jour toutes les listes déroulantes dans toutes les règles."""
        logging.debug("Repopulation des listes déroulantes des règles...")
        for rule_id, rule_widget_data in self.rule_widgets.items():
            rule_frame = rule_widget_data['frame']
            # On doit parcourir récursivement les widgets DANS rule_frame
            self._repopulate_dropdowns_recursive(rule_frame)

    def _repopulate_dropdowns_recursive(self, widget):
        """Parcourt les widgets et met à jour les combobox Capteur et Kasa/Prise."""
        if isinstance(widget, ttk.Combobox):
            # Identifier le type de Combobox (méthode plus robuste serait d'ajouter une propriété)
            widget_type = None
            try:
                 # Accéder aux données de la condition/action parente pour identifier
                 parent_frame = widget.master
                 while parent_frame and not hasattr(parent_frame, 'condition_data_ref') and not hasattr(parent_frame, 'action_data_ref'):
                      parent_frame = parent_frame.master
                      if parent_frame == self.root: # Eviter boucle infinie
                           parent_frame = None
                           break

                 if hasattr(parent_frame, 'condition_data_ref'):
                      # C'est un combo dans une condition (Type ou Capteur)
                      if widget.cget('width') == 25: widget_type = 'sensor_select'
                      elif widget.cget('width') == 8: widget_type = 'condition_type_select' # Le combo Type
                      # Les combos Opérateur n'ont pas besoin d'être repopulés
                 elif hasattr(parent_frame, 'action_data_ref'):
                      # C'est un combo dans le bloc action (Kasa ou Prise)
                       if widget.cget('width') == 30: widget_type = 'kasa_select'
                       elif widget.cget('width') == 25: widget_type = 'outlet_select'
                       # Le combo Action n'a pas besoin d'être repopulé
            except Exception as e:
                 # logging.warning(f"Erreur identification combobox: {e}")
                 pass # On continue sans type si échec

            # --- Repopulation basée sur le type identifié ---
            if widget_type == 'sensor_select':
                 current_value = widget.get() # Alias actuel
                 new_values = [name for name, _id in self.available_sensors]
                 widget['values'] = new_values
                 if current_value not in new_values:
                     # Essayer de retrouver l'ID et voir si un nouvel alias existe
                     sensor_id = getattr(parent_frame, 'condition_data_ref', {}).get('id')
                     new_alias = self.get_alias('sensor', sensor_id) if sensor_id else None
                     if new_alias and new_alias in new_values:
                          widget.set(new_alias)
                     else:
                          widget.set('')
                          if hasattr(parent_frame, 'condition_data_ref'): parent_frame.condition_data_ref['id'] = None # Nettoyer data
                 else:
                     widget.set(current_value) # Remettre l'alias

            elif widget_type == 'kasa_select':
                 current_value = widget.get() # Alias actuel
                 new_values = [name for name, _mac in self.available_kasa_strips]
                 widget['values'] = new_values
                 if current_value not in new_values:
                      kasa_mac = getattr(parent_frame, 'action_data_ref', {}).get('target_device_mac')
                      new_alias = self.get_alias('device', kasa_mac) if kasa_mac else None
                      if new_alias and new_alias in new_values:
                           widget.set(new_alias)
                           # IMPORTANT: Rafraichir les prises associées
                           outlet_combo = next((w for w in widget.master.winfo_children() if isinstance(w, ttk.Combobox) and w.cget('width') == 25), None)
                           outlet_var = outlet_combo.cget('textvariable') if outlet_combo else None
                           if outlet_combo and outlet_var:
                               self._update_action_outlet_options(parent_frame.action_data_ref, outlet_combo, outlet_var, kasa_mac, None) # Reset prise
                      else:
                           widget.set('')
                           if hasattr(parent_frame, 'action_data_ref'): parent_frame.action_data_ref['target_device_mac'] = None # Nettoyer data
                           # Vider aussi les prises
                           outlet_combo = next((w for w in widget.master.winfo_children() if isinstance(w, ttk.Combobox) and w.cget('width') == 25), None)
                           if outlet_combo:
                                outlet_combo['values'] = []
                                outlet_combo.set('')
                                if hasattr(parent_frame, 'action_data_ref'): parent_frame.action_data_ref['target_outlet_index'] = None

                 else:
                     widget.set(current_value) # Remettre l'alias
                     # Assurer que les prises sont à jour même si l'alias n'a pas changé
                     kasa_mac = self._get_mac_from_alias(current_value)
                     outlet_combo = next((w for w in widget.master.winfo_children() if isinstance(w, ttk.Combobox) and w.cget('width') == 25), None)
                     outlet_var = outlet_combo.cget('textvariable') if outlet_combo else None
                     if outlet_combo and outlet_var and hasattr(parent_frame, 'action_data_ref'):
                          current_outlet_index = parent_frame.action_data_ref.get('target_outlet_index')
                          self._update_action_outlet_options(parent_frame.action_data_ref, outlet_combo, outlet_var, kasa_mac, current_outlet_index)


            elif widget_type == 'outlet_select':
                # Ce combobox est mis à jour via _update_action_outlet_options déclenché
                # par la sélection/mise à jour du combobox Kasa parent.
                pass

        # Appel récursif pour les enfants
        for child in widget.winfo_children():
            self._repopulate_dropdowns_recursive(child)


    # --- Découverte et Rafraîchissement (inchangé) ---
    def discover_all_devices(self):
        logging.info("Démarrage de la découverte des périphériques...")
        try: self.temp_manager.discover_sensors()
        except Exception as e: logging.error(f"Erreur découverte temp: {e}")
        try: self.light_manager.scan_sensors()
        except Exception as e: logging.error(f"Erreur découverte light: {e}")
        threading.Thread(target=self._run_kasa_discovery_async, daemon=True).start()

    def _run_kasa_discovery_async(self):
          try: loop = asyncio.get_event_loop()
          except RuntimeError: loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
          loop.run_until_complete(self._async_discover_kasa())

    async def _async_discover_kasa(self):
        discoverer = DeviceDiscoverer()
        discovered_kasa = await discoverer.discover()
        new_kasa_devices = {}
        tasks_turn_off = []
        for device_info in discovered_kasa:
             ip, mac = device_info.get('ip'), device_info.get('mac')
             if not ip or not mac: continue
             controller = DeviceController(ip, is_strip=device_info.get('is_strip'), is_plug=device_info.get('is_plug'))
             new_kasa_devices[mac] = {'info': device_info, 'controller': controller, 'ip': ip}
             if not self.monitoring_active and (device_info.get('is_strip') or device_info.get('is_plug')):
                 tasks_turn_off.append(controller.turn_all_outlets_off())
        if tasks_turn_off:
             logging.info(f"Exécution de {len(tasks_turn_off)} tâches d'extinction initiale...")
             await asyncio.gather(*tasks_turn_off, return_exceptions=True) # Gérer erreurs si besoin

        self.kasa_devices = new_kasa_devices
        logging.info(f"Découverte Kasa terminée. {len(self.kasa_devices)} appareil(s) trouvé(s).")
        self.root.after(100, self.refresh_device_lists)

    def refresh_device_lists(self):
        """Met à jour les listes internes et l'UI après découverte."""
        self.update_device_lists_for_ui()
        self.repopulate_all_rule_dropdowns()
        self.update_status_display()
        logging.info("Listes de périphériques et UI mises à jour après découverte.")

    def update_device_lists_for_ui(self):
        """Met à jour self.available_sensors, self.available_kasa_strips, self.available_outlets."""
        # ... (Logique inchangée depuis v2/ébauche) ...
        logging.debug("Mise à jour des listes internes de périphériques (available_*)")
        temp_ids = self.temp_manager.get_sensor_ids()
        light_ids_int = self.light_manager.get_active_sensors()
        light_ids_hex = [hex(addr) for addr in light_ids_int]
        sensors = []
        for tid in temp_ids: sensors.append((self.get_alias('sensor', tid), tid))
        for addr_hex in light_ids_hex: sensors.append((self.get_alias('sensor', addr_hex), addr_hex))
        self.available_sensors = sorted(sensors, key=lambda x: x[0])

        kasa_strips = []
        outlets_dict = {}
        sorted_macs = sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m))
        for mac in sorted_macs:
            data = self.kasa_devices[mac]
            device_info = data['info']
            device_alias = self.get_alias('device', mac)
            kasa_strips.append((device_alias, mac))
            outlets = []
            if device_info.get('is_strip') or device_info.get('is_plug'):
                discovered_outlets = device_info.get('outlets', [])
                for outlet_data in discovered_outlets:
                    index = outlet_data.get('index')
                    if index is not None:
                        outlet_alias = self.get_alias('outlet', mac, sub_id=index)
                        outlets.append((outlet_alias, index))
            if outlets: outlets_dict[mac] = sorted(outlets, key=lambda x: x[1])
        self.available_kasa_strips = kasa_strips
        self.available_outlets = outlets_dict


    # --- Affichage Statut (inchangé) ---
    def update_status_display(self):
        # ... (Logique inchangée depuis v2/ébauche) ...
        logging.debug("Mise à jour de l'affichage du statut.")
        for widget in self.scrollable_status_frame.winfo_children(): widget.destroy()
        self.status_labels = {}
        row_num = 0
        try: all_temp_readings = self.temp_manager.read_all_temperatures()
        except Exception as e: logging.error(f"Err temp status: {e}"); all_temp_readings = {}
        try: all_light_readings = self.light_manager.read_all_sensors()
        except Exception as e: logging.error(f"Err light status: {e}"); all_light_readings = {}

        ttk.Label(self.scrollable_status_frame, text="Capteurs:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(5, 2)); row_num += 1
        for alias, sensor_id in self.available_sensors:
             value_text, unit = "N/A", ""
             is_temp = sensor_id in all_temp_readings
             is_light = sensor_id in all_light_readings
             if is_temp: temp = all_temp_readings.get(sensor_id); value_text = f"{temp:.1f}" if temp is not None else "Erreur"; unit = "°C"
             elif is_light: lux = all_light_readings.get(sensor_id); value_text = f"{lux:.1f}" if lux is not None else "Erreur"; unit = " Lux"
             else: value_text = "Inconnu"
             frame = ttk.Frame(self.scrollable_status_frame); frame.grid(row=row_num, column=0, columnspan=4, sticky='w')
             name_label = ttk.Label(frame, text=f"{alias}:", width=25); name_label.pack(side=tk.LEFT, padx=5)
             value_label = ttk.Label(frame, text=f"{value_text}{unit}", width=15); value_label.pack(side=tk.LEFT, padx=5)
             edit_button = ttk.Button(frame, text="✎", width=2, command=lambda s_id=sensor_id, s_name=alias: self.edit_alias_dialog('sensor', s_id, s_name)); edit_button.pack(side=tk.LEFT, padx=2)
             self.status_labels[sensor_id] = {'type': 'sensor', 'label_name': name_label, 'label_value': value_label, 'button_edit': edit_button}; row_num += 1

        ttk.Label(self.scrollable_status_frame, text="Prises Kasa:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(10, 2)); row_num += 1
        for device_alias, mac in self.available_kasa_strips:
             data = self.kasa_devices.get(mac); ip_addr = data.get('ip', '?.?.?.?') if data else 'N/A'
             frame_dev = ttk.Frame(self.scrollable_status_frame); frame_dev.grid(row=row_num, column=0, columnspan=4, sticky='w')
             dev_display_text = f"{device_alias} ({ip_addr}) [{mac}]"; dev_name_label = ttk.Label(frame_dev, text=dev_display_text, width=50); dev_name_label.pack(side=tk.LEFT, padx=5)
             dev_edit_button = ttk.Button(frame_dev, text="✎", width=2, command=lambda d_mac=mac, d_name=device_alias: self.edit_alias_dialog('device', d_mac, d_name)); dev_edit_button.pack(side=tk.LEFT, padx=2)
             self.status_labels[mac] = {'type': 'device', 'label_name': dev_name_label, 'button_edit': dev_edit_button}; row_num += 1
             if mac in self.available_outlets:
                 for outlet_alias, index in self.available_outlets[mac]:
                     current_state = self._get_shared_kasa_state(mac, index)
                     frame_outlet = ttk.Frame(self.scrollable_status_frame); frame_outlet.grid(row=row_num, column=1, columnspan=3, sticky='w', padx=(20,0))
                     outlet_name_label = ttk.Label(frame_outlet, text=f"└─ {outlet_alias}:", width=23); outlet_name_label.pack(side=tk.LEFT, padx=5)
                     outlet_value_label = ttk.Label(frame_outlet, text=current_state, width=10); outlet_value_label.pack(side=tk.LEFT, padx=5)
                     outlet_edit_button = ttk.Button(frame_outlet, text="✎", width=2, command=lambda d_mac=mac, o_idx=index, o_name=outlet_alias: self.edit_alias_dialog('outlet', d_mac, o_name, sub_id=o_idx)); outlet_edit_button.pack(side=tk.LEFT, padx=2)
                     outlet_key = f"{mac}_{index}"; self.status_labels[outlet_key] = {'type': 'outlet', 'mac': mac, 'index': index, 'label_name': outlet_name_label, 'label_value': outlet_value_label, 'button_edit': outlet_edit_button}; row_num += 1

        self.scrollable_status_frame.update_idletasks()
        status_canvas = self.scrollable_status_frame.master
        status_canvas.configure(scrollregion=status_canvas.bbox("all"))


    # --- Mise à Jour Périodique UI (inchangé) ---
    def schedule_periodic_updates(self):
        # ... (Comme avant) ...
        self.update_live_status()
        self.ui_update_job = self.root.after(5000, self.schedule_periodic_updates)

    def cancel_periodic_updates(self):
         # ... (Comme avant) ...
         if self.ui_update_job: self.root.after_cancel(self.ui_update_job); self.ui_update_job = None

    def update_live_status(self):
         # ... (Comme avant) ...
         if not self.monitoring_active: return
         logging.debug("Mise à jour des valeurs de statut en direct.")
         try: temp_readings = self.temp_manager.read_all_temperatures(); light_readings = self.light_manager.read_all_sensors()
         except Exception as e: logging.warning(f"Err lecture capteurs live: {e}"); return

         for sensor_id, data in self.status_labels.items():
             if data['type'] == 'sensor' and data['label_value'].winfo_exists():
                 value, unit = None, ""
                 is_temp = sensor_id in temp_readings; is_light = sensor_id in light_readings
                 if is_temp: value = temp_readings.get(sensor_id); unit = "°C"
                 elif is_light: value = light_readings.get(sensor_id); unit = " Lux"
                 if value is not None: display_text = f"{value:.1f}{unit}"; data['label_value'].config(text=display_text)
                 else: data['label_value'].config(text="Erreur/N/A")

         for key, data in self.status_labels.items():
             if data['type'] == 'outlet' and data['label_value'].winfo_exists():
                 mac, index = data['mac'], data['index']
                 current_state = self._get_shared_kasa_state(mac, index)
                 data['label_value'].config(text=current_state)


    def _get_shared_kasa_state(self, mac, index):
        # ... (Comme avant) ...
        try: return "ON" if self.live_kasa_states[mac][index] else "OFF"
        except (AttributeError, KeyError): return "Inconnu"


    # --- Logs (Inchangé) ---
    def update_log_display(self):
        # ... (Comme avant) ...
        while True:
            try: record = self.log_queue.get_nowait()
            except queue.Empty: break
            else:
                self.log_display.config(state=tk.NORMAL)
                self.log_display.insert(tk.END, record + '\n')
                self.log_display.config(state=tk.DISABLED)
                self.log_display.see(tk.END)
        self.root.after(100, self.update_log_display)


    # --- Démarrage / Arrêt Monitoring ---
    def start_monitoring(self):
        if self.monitoring_active: return
        # Optionnel: reconstruire/valider les données avant start
        # self._rebuild_all_rules_data_from_ui()
        logging.info("Démarrage du monitoring des règles...")
        self.monitoring_active = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self._set_rules_ui_state(tk.DISABLED)
        self.live_kasa_states = {}
        self.active_until_triggers = {}
        self.monitoring_thread = threading.Thread(target=self._run_monitoring_loop, daemon=True)
        self.monitoring_thread.start()
        self.schedule_periodic_updates()

    def stop_monitoring(self):
        if not self.monitoring_active: return
        logging.info("Arrêt du monitoring des règles...")
        self.monitoring_active = False
        # Attendre un peu si besoin: self.monitoring_thread.join(timeout=2)
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self._set_rules_ui_state(tk.NORMAL)
        self.cancel_periodic_updates()
        logging.info("Tentative d'extinction de toutes les prises Kasa par sécurité...")
        # Utilisation de _turn_off_all_kasa_safely qui gère l'appel async
        self._turn_off_all_kasa_safely()
        logging.info("Monitoring arrêté.")

    def _set_rules_ui_state(self, state):
        # ... (Comme avant, utilise _set_widget_state_recursive) ...
        try: # Bouton global Ajouter
             main_frame = self.root.winfo_children()[0]
             add_button = next(w for w in main_frame.winfo_children() if isinstance(w, ttk.Button) and "Ajouter une Règle" in w.cget("text"))
             add_button.config(state=state)
        except: pass
        # Widgets internes des règles
        for rule_id, rule_widget_data in self.rule_widgets.items():
             rule_frame = rule_widget_data['frame']
             self._set_widget_state_recursive(rule_frame, state)

    def _set_widget_state_recursive(self, widget, state):
        # ... (Comme avant) ...
         widget_state = state
         read_only_state = 'readonly' if state == tk.NORMAL else tk.DISABLED
         try:
             if isinstance(widget, (ttk.Button, ttk.Checkbutton, ttk.Entry)): widget.config(state=widget_state)
             elif isinstance(widget, (ttk.Spinbox, ttk.Combobox)): widget.config(state=read_only_state)
         except tk.TclError: pass
         for child in widget.winfo_children(): self._set_widget_state_recursive(child, state)


    # --- Boucle de Monitoring (Adaptée pour Nouvelle Logique et Timer) ---
    def _run_monitoring_loop(self):
        # ... (Setup asyncio loop comme avant) ...
        try: self.asyncio_loop = asyncio.get_event_loop()
        except RuntimeError: self.asyncio_loop = asyncio.new_event_loop(); asyncio.set_event_loop(self.asyncio_loop)
        try: self.asyncio_loop.run_until_complete(self._async_monitoring_task())
        except Exception as e: logging.critical(f"Erreur boucle monitoring asyncio: {e}", exc_info=True)
        finally:
             logging.info("Boucle monitoring asyncio terminée.")
             if self.monitoring_active: self.root.after(0, self.stop_monitoring)


    async def _update_live_kasa_states_task(self):
        # ... (Inchangé) ...
        logging.debug("MAJ états Kasa live...")
        new_live_states = {}
        tasks = [self._fetch_one_kasa_state(mac, data['controller']) for mac, data in self.kasa_devices.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception): logging.error(f"Err lecture état Kasa live: {result}")
            elif isinstance(result, dict) and result: new_live_states.update(result)
        self.live_kasa_states = new_live_states


    async def _fetch_one_kasa_state(self, mac, controller):
        # ... (Inchangé) ...
        try:
            await controller._connect()
            if controller._device:
                states = await controller.get_outlet_state()
                if states is not None: return {mac: {o['index']: o['is_on'] for o in states if 'index' in o and 'is_on' in o}}
                else: logging.warning(f"État prises non obtenu pour {mac}")
            else: logging.warning(f"Connexion échouée pour {mac}")
        except Exception as e: logging.error(f"Err lecture état {mac}: {e}"); raise e
        return {}


    async def _async_monitoring_task(self):
        """Tâche principale de monitoring avec évaluation récursive et timer UNTIL."""
        last_kasa_update_time = datetime.min
        kasa_update_interval = timedelta(seconds=10)

        while self.monitoring_active:
            current_time = datetime.now()
            logging.debug(f"--- Cycle Monitoring {current_time.strftime('%H:%M:%S')} ---")

            # --- 1. Lire Capteurs ---
            try:
                 temp_values = await self.asyncio_loop.run_in_executor(None, self.temp_manager.read_all_temperatures)
                 light_values = await self.asyncio_loop.run_in_executor(None, self.light_manager.read_all_sensors)
                 sensor_values = {**temp_values, **light_values}
                 valid_sensor_values = {k: v for k, v in sensor_values.items() if v is not None}
            except Exception as e: logging.error(f"Err lecture capteurs: {e}"); valid_sensor_values = {}

            # --- 2. Lire État Kasa ---
            if current_time - last_kasa_update_time >= kasa_update_interval:
                 try: await self._update_live_kasa_states_task(); last_kasa_update_time = current_time
                 except Exception as e: logging.error(f"Échec MAJ états Kasa: {e}")

            # --- 3. Évaluer Règles ---
            tasks_to_run = []
            desired_outlet_states = {}
            # Utiliser une copie profonde pour éviter modif pendant itération ET pour que les refs dans active_until soient stables
            rules_to_evaluate = [copy.deepcopy(rule) for rule in self.rules if rule.get('enabled', True)]

            # --- 3a. Évaluer UNTIL ---
            active_until_triggers_copy = dict(self.active_until_triggers)
            for rule_id, until_info in active_until_triggers_copy.items():
                 rule = next((r for r in rules_to_evaluate if r.get('id') == rule_id), None)
                 if not rule:
                     logging.warning(f"UNTIL actif pour règle {rule_id} non trouvée/désactivée. Annulation UNTIL.")
                     if rule_id in self.active_until_triggers: del self.active_until_triggers[rule_id]
                     continue

                 until_conditions_data = rule.get('until_conditions')
                 action_block = until_info['original_action_block']
                 target_mac, target_index = action_block.get('target_device_mac'), action_block.get('target_outlet_index')

                 if not until_conditions_data or not target_mac or target_index is None:
                     logging.warning(f"Données UNTIL/Action invalides pour règle active {rule_id}. Annulation UNTIL.")
                     if rule_id in self.active_until_triggers: del self.active_until_triggers[rule_id]
                     continue

                 outlet_key = (target_mac, target_index)
                 revert_action_needed = False
                 try:
                     # Passer rule_id pour l'évaluation du Timer
                     until_condition_met = await self.evaluate_condition_group(until_conditions_data, valid_sensor_values, current_time, rule_id)
                     if until_condition_met:
                         revert_action_needed = True
                         logging.info(f"Règle {rule_id}: Condition(s) 'UNTIL' remplie(s). Action retour: {until_info['revert_action']}")
                 except Exception as e: logging.error(f"Erreur éval UNTIL pour {rule_id}: {e}")

                 if revert_action_needed:
                     desired_outlet_states[outlet_key] = until_info['revert_action']
                     if rule_id in self.active_until_triggers: del self.active_until_triggers[rule_id] # Désactiver


            # --- 3b. Évaluer SI ---
            for rule in rules_to_evaluate:
                 rule_id = rule.get('id')
                 trigger_conditions_data = rule.get('trigger_conditions')
                 action_block = rule.get('action_block')
                 if not rule_id or not trigger_conditions_data or not action_block: continue

                 target_mac, target_index = action_block.get('target_device_mac'), action_block.get('target_outlet_index')
                 primary_action = action_block.get('action')
                 if not all([target_mac, target_index is not None, primary_action]): continue

                 outlet_key = (target_mac, target_index)
                 is_until_active = rule_id in self.active_until_triggers
                 # Vérifier si l'action désirée est déjà le revert (prioritaire)
                 is_revert_set = outlet_key in desired_outlet_states and \
                                  rule_id in active_until_triggers_copy and \
                                  desired_outlet_states[outlet_key] == active_until_triggers_copy[rule_id]['revert_action']

                 if is_until_active or is_revert_set: continue # Skip SI

                 try:
                     # Passer rule_id (même si non utilisé par SI pour l'instant)
                     trigger_condition_met = await self.evaluate_condition_group(trigger_conditions_data, valid_sensor_values, current_time, rule_id)
                     if trigger_condition_met:
                         if outlet_key not in desired_outlet_states: # Ne pas écraser un revert
                             desired_outlet_states[outlet_key] = primary_action

                             # Activer UNTIL si nécessaire et si conditions existent
                             until_conditions = rule.get('until_conditions')
                             if until_conditions and until_conditions.get('conditions'):
                                 revert_action = 'OFF' if primary_action == 'ON' else 'ON'
                                 self.active_until_triggers[rule_id] = {
                                     'revert_action': revert_action,
                                     'start_time': current_time,
                                     'original_action_block': copy.deepcopy(action_block)
                                 }
                                 logging.info(f"Règle {rule_id}: SI remplie. Activation 'UNTIL'. Action={primary_action}, Revert={revert_action}.")
                 except Exception as e: logging.error(f"Erreur éval SI pour {rule_id}: {e}")


            # --- 4. Appliquer Changements Kasa ---
            processed_outlets = set()
            # 4a. Actions explicites
            for outlet_key, desired_state in desired_outlet_states.items():
                 target_mac, target_index = outlet_key
                 processed_outlets.add(outlet_key)
                 current_state_bool = self.live_kasa_states.get(target_mac, {}).get(target_index)
                 action_needed, action_func_name = False, None
                 if desired_state == 'ON' and current_state_bool is not True: action_needed, action_func_name = True, 'turn_outlet_on'
                 elif desired_state == 'OFF' and current_state_bool is not False: action_needed, action_func_name = True, 'turn_outlet_off'

                 if action_needed:
                      if target_mac in self.kasa_devices:
                          controller = self.kasa_devices[target_mac]['controller']
                          log_alias_dev = self.get_alias('device', target_mac); log_alias_outlet = self.get_alias('outlet', target_mac, target_index)
                          logging.info(f"Action: {action_func_name} pour {log_alias_dev}/{log_alias_outlet} ({target_mac}/{target_index})")
                          tasks_to_run.append(getattr(controller, action_func_name)(target_index))
                          if target_mac not in self.live_kasa_states: self.live_kasa_states[target_mac] = {}
                          self.live_kasa_states[target_mac][target_index] = (desired_state == 'ON')
                      else: logging.error(f"Appareil Kasa inconnu {target_mac} pour action.")

            # 4b. Extinction implicite
            all_managed_outlets = set()
            for rule in rules_to_evaluate:
                 ab = rule.get('action_block', {})
                 mac, idx = ab.get('target_device_mac'), ab.get('target_outlet_index')
                 if mac and idx is not None: all_managed_outlets.add((mac, idx))

            for mac, outlets in self.live_kasa_states.items():
                 for index, is_on in outlets.items():
                     outlet_key = (mac, index)
                     if outlet_key in all_managed_outlets and outlet_key not in processed_outlets and is_on:
                          if mac in self.kasa_devices:
                              controller = self.kasa_devices[mac]['controller']
                              log_alias_dev = self.get_alias('device', mac); log_alias_outlet = self.get_alias('outlet', mac, index)
                              logging.info(f"Action implicite OFF: {log_alias_dev}/{log_alias_outlet} ({mac}/{index})")
                              tasks_to_run.append(controller.turn_outlet_off(index))
                              self.live_kasa_states[mac][index] = False
                          else: logging.error(f"Appareil Kasa inconnu {mac} pour OFF implicite.")

            # --- 5. Exécuter Tâches Kasa ---
            if tasks_to_run:
                 logging.debug(f"Exécution {len(tasks_to_run)} tâche(s) Kasa...")
                 await asyncio.gather(*tasks_to_run, return_exceptions=True) # Gérer erreurs si besoin

            # --- 6. Attendre ---
            await asyncio.sleep(2)


    # --- Fonctions d'évaluation récursives (avec rule_id) ---

    async def evaluate_condition_group(self, group_data, sensor_values, current_time, rule_id):
        """Évalue un groupe de conditions (AND/OR) de manière récursive."""
        logic = group_data.get('logic', 'ET')
        conditions = group_data.get('conditions', [])
        if not conditions: return False # Groupe vide échoue

        results = []
        for condition in conditions:
             # Vérifier si condition est bien un dict avant de chercher 'logic' ou d'appeler eval
             if not isinstance(condition, dict):
                 logging.warning(f"Item invalide (non-dict) dans groupe de conditions pour règle {rule_id}: {condition}")
                 continue # Ignorer cet item

             if 'logic' in condition: # Sous-groupe
                sub_result = await self.evaluate_condition_group(condition, sensor_values, current_time, rule_id)
                results.append(sub_result)
             else: # Condition simple
                single_result = await self.evaluate_single_condition(condition, sensor_values, current_time, rule_id)
                results.append(single_result)

        if not results: return False # Si toutes les conditions étaient invalides

        if logic == 'ET': return all(results)
        elif logic == 'OU': return any(results)
        else: logging.warning(f"Logique inconnue: {logic}"); return False


    async def evaluate_single_condition(self, condition_data, sensor_values, current_time, rule_id):
        """Évalue une condition simple (sensor, time, timer)."""
        cond_type = condition_data.get('type')
        operator = condition_data.get('operator')
        target_value_config = condition_data.get('value') # Peut être float, str 'HH:MM', int (duration)

        # Timer est spécial, il n'a pas besoin d'opérateur ou de valeur cible au sens classique
        if cond_type == 'Timer':
             if rule_id not in self.active_until_triggers:
                 # Le timer ne peut être évalué que si la règle est dans le contexte "UNTIL" actif
                 # logging.debug(f"Timer pour règle {rule_id} non évalué (pas dans active_until_triggers)")
                 return False
             try:
                 duration = int(target_value_config) # La durée est dans 'value'
                 start_time = self.active_until_triggers[rule_id]['start_time']
                 end_time = start_time + timedelta(seconds=duration)
                 # La condition Timer est VRAIE si le temps actuel est >= end_time
                 result = current_time >= end_time
                 # logging.debug(f"Eval Timer: rule={rule_id}, start={start_time}, duration={duration}, end={end_time}, now={current_time}, result={result}")
                 return result
             except (ValueError, TypeError, KeyError) as e:
                 logging.error(f"Erreur évaluation Timer pour règle {rule_id}: {e}, data={condition_data}")
                 return False

        # Pour les autres types, opérateur et valeur sont requis
        if not all([cond_type, operator]) or target_value_config is None:
             logging.warning(f"Condition simple invalide/incomplète: {condition_data}")
             return False

        current_value_for_eval, target_value_for_eval = None, None

        if cond_type == 'Capteur':
            sensor_id = condition_data.get('id')
            if sensor_id is None: return False
            if sensor_id in sensor_values:
                current_value_for_eval = sensor_values[sensor_id]
                try: target_value_for_eval = float(target_value_config)
                except (ValueError, TypeError): logging.error(f"Cible capteur invalide '{target_value_config}' pour {condition_data}"); return False
            else: return False # Capteur non dispo

        elif cond_type == 'Heure':
             try:
                 target_value_for_eval = datetime.strptime(str(target_value_config), '%H:%M').time()
                 current_value_for_eval = current_time.time()
             except (ValueError, TypeError): logging.error(f"Format/valeur heure invalide '{target_value_config}' pour {condition_data}"); return False
        else:
            logging.warning(f"Type de condition non supporté pour évaluation: {cond_type}"); return False

        # Comparaison finale
        # logging.debug(f"Comparaison: {current_value_for_eval} {operator} {target_value_for_eval}")
        return self._compare(current_value_for_eval, operator, target_value_for_eval)


    def _compare(self, value1, operator, value2):
        """Effectue une comparaison en gérant nombres et datetime.time."""
        try:
            if isinstance(value1, time) and isinstance(value2, time):
                if operator == '<': return value1 < value2
                if operator == '>': return value1 > value2
                if operator == '=': return value1 == value2
                if operator == '!=': return value1 != value2
                if operator == '<=': return value1 <= value2
                if operator == '>=': return value1 >= value2
            else:
                v1, v2 = float(value1), float(value2)
                if operator == '<': return v1 < v2
                if operator == '>': return v1 > v2
                if operator == '=': return abs(v1 - v2) < FLOAT_TOLERANCE
                if operator == '!=': return abs(v1 - v2) >= FLOAT_TOLERANCE
                if operator == '<=': return v1 <= v2
                if operator == '>=': return v1 >= v2
        except (ValueError, TypeError) as e:
             logging.error(f"Erreur comparaison: {value1} {operator} {value2} - {e}"); return False
        logging.warning(f"Opérateur comparaison inconnu/incompatible: {operator}"); return False


    # --- Sauvegarde / Fermeture ---

    def _rebuild_all_rules_data_from_ui(self):
         """Appelle la reconstruction des données pour toutes les règles affichées."""
         logging.debug("Reconstruction des données de toutes les règles depuis l'UI...")
         all_valid = True
         # Itérer sur les ID des règles actuellement affichées
         for rule_id in list(self.rule_widgets.keys()):
              if not self._rebuild_rule_data_from_ui(rule_id):
                   all_valid = False
                   logging.error(f"Échec de la reconstruction des données pour règle {rule_id}.")
                   # Que faire? Arrêter la sauvegarde? Continuer?
         return all_valid


    def _rebuild_rule_data_from_ui(self, rule_id):
        """Reconstruit le dictionnaire de données pour rule_id en parcourant l'UI."""
        if rule_id not in self.rule_widgets:
             logging.error(f"Impossible de reconstruire : Règle UI {rule_id} non trouvée.")
             return False # Indiquer échec

        rule_ui_elements = self.rule_widgets[rule_id]
        target_data_dict = self.rule_widgets[rule_id]['data_ref'] # Le dict à mettre à jour
        rule_frame = rule_ui_elements['frame']
        header_widgets = rule_ui_elements['header_widgets']
        trigger_frame_ui = rule_ui_elements['trigger_frame']
        action_frame_ui = rule_ui_elements['action_frame']
        until_frame_ui = rule_ui_elements['until_frame']

        logging.debug(f"Début reconstruction données pour règle {rule_id}")

        try:
            # 1. Reconstruire l'en-tête (Nom, Enabled) - Déjà à jour via callbacks normalement
            target_data_dict['name'] = header_widgets['name_var'].get()
            target_data_dict['enabled'] = header_widgets['enabled_var'].get()

            # 2. Reconstruire le bloc action - Déjà à jour via callbacks normalement
            # On pourrait ajouter une validation ici si nécessaire

            # 3. Reconstruire récursivement les blocs conditions
            # Le premier enfant de trigger/until_frame est le groupe racine UI
            trigger_root_group_ui = trigger_frame_ui.winfo_children()[0] if trigger_frame_ui.winfo_children() else None
            if trigger_root_group_ui:
                 target_data_dict['trigger_conditions'] = self._rebuild_conditions_recursive(trigger_root_group_ui)
            else: # Pas de groupe racine UI -> bloc vide
                 target_data_dict['trigger_conditions'] = {'logic': 'ET', 'conditions': []} # Ou lire la logique par défaut?

            until_root_group_ui = until_frame_ui.winfo_children()[0] if until_frame_ui.winfo_children() else None
            if until_root_group_ui:
                 target_data_dict['until_conditions'] = self._rebuild_conditions_recursive(until_root_group_ui)
            else: # Pas de groupe racine UI -> bloc vide
                 target_data_dict['until_conditions'] = {'logic': 'OU', 'conditions': []}

            logging.debug(f"Fin reconstruction données pour règle {rule_id}. Données: {target_data_dict}")
            return True # Succès

        except Exception as e:
            logging.error(f"Erreur pendant reconstruction données pour règle {rule_id}: {e}", exc_info=True)
            return False # Échec


    def _rebuild_conditions_recursive(self, current_ui_frame):
        """Helper récursif pour reconstruire la structure de données conditions depuis l'UI."""
        rebuilt_data = {}

        # Est-ce un groupe logique ou une condition simple? Vérifier la présence de logic_data_ref
        if hasattr(current_ui_frame, 'logic_data_ref'):
            # C'est un groupe logique
            group_data = current_ui_frame.logic_data_ref # Récupérer les données actuelles (pour la logique ET/OU)
            rebuilt_data['logic'] = group_data.get('logic', 'ET') # Lire la logique
            rebuilt_data['conditions'] = []
            # Le conteneur UI des enfants est dans conditions_container
            conditions_container_ui = getattr(current_ui_frame, 'conditions_container', None)
            if conditions_container_ui:
                for child_frame in conditions_container_ui.winfo_children():
                     # Appel récursif pour chaque enfant (qui peut être groupe ou condition)
                     child_data = self._rebuild_conditions_recursive(child_frame)
                     if child_data: # Ajouter seulement si la reconstruction enfant a réussi
                         rebuilt_data['conditions'].append(child_data)

        elif hasattr(current_ui_frame, 'condition_data_ref'):
            # C'est une condition simple
            # Les données simples (type, op, value, id) SONT CENSÉES être à jour dans
            # condition_data_ref grâce aux callbacks (_update_condition_data, etc.)
            # On retourne simplement une copie de ces données pour la reconstruction.
            # On pourrait ajouter une validation/relecture des widgets ici par sécurité.
            rebuilt_data = copy.deepcopy(current_ui_frame.condition_data_ref)
            # --- Validation/Relecture optionnelle ---
            # Ex: Lire type_combo, op_combo, value_entry/spinbox et revérifier/mettre à jour rebuilt_data
            # Cela rendrait les callbacks _update moins critiques mais plus lourd ici.

        else:
            logging.warning(f"Widget UI inconnu rencontré pendant reconstruction: {current_ui_frame}")
            return None # Ignorer ce widget

        return rebuilt_data


    def save_configuration(self):
        """Sauvegarde la configuration (nouvelle structure)."""
        logging.info("Préparation de la sauvegarde...")

        # --- Reconstruire TOUTES les données depuis l'UI ---
        if not self._rebuild_all_rules_data_from_ui():
             messagebox.showerror("Erreur Sauvegarde", "Impossible de lire l'état actuel de toutes les règles depuis l'interface. Sauvegarde annulée.", parent=self.root)
             return

        # À ce point, self.rules DEVRAIT contenir l'état exact de l'UI.
        # Ajouter une étape de validation finale sur self.rules?
        self._validate_and_clean_rules() # Re-nettoyer au cas où la reconstruction aurait laissé des invalides

        config_to_save = {
            "aliases": self.aliases,
            "rules": self.rules
        }
        logging.debug(f"Données finales pour sauvegarde : {len(self.rules)} règles")

        if save_config(config_to_save, DEFAULT_CONFIG_FILE):
            messagebox.showinfo("Sauvegarde", f"Configuration sauvegardée avec succès dans\n{DEFAULT_CONFIG_FILE}", parent=self.root)
        else:
            messagebox.showerror("Sauvegarde", f"Erreur lors de la sauvegarde dans\n{DEFAULT_CONFIG_FILE}", parent=self.root)


    def _turn_off_all_kasa_safely(self):
        """Tente d'éteindre toutes les prises Kasa connues."""
        # ... (Inchangé - utilise asyncio.run dans un thread si besoin) ...
        try:
            try: loop = asyncio.get_running_loop(); is_running = True
            except RuntimeError: loop, is_running = None, False

            if is_running and loop:
                 # Lancer comme tâche si boucle active
                 # asyncio.run_coroutine_threadsafe(self._async_turn_off_all(), loop) # Alternative
                 threading.Thread(target=lambda: asyncio.run(self._async_turn_off_all()), daemon=True).start() # Plus simple?
            else:
                 # Lancer avec asyncio.run si pas de boucle active
                 logging.info("Lancement asyncio.run pour extinction.")
                 # Exécuter dans un thread pour ne pas bloquer si appelé depuis on_closing
                 threading.Thread(target=lambda: asyncio.run(self._async_turn_off_all()), daemon=True).start()
        except Exception as final_e:
            logging.error(f"Erreur finale tentative extinction : {final_e}")


    async def _async_turn_off_all(self):
         """Tâche asynchrone pour éteindre toutes les prises."""
         # ... (Inchangé) ...
         tasks = []
         logging.info(f"Préparation extinction sécurité pour {len(self.kasa_devices)} appareils...")
         for mac, data in self.kasa_devices.items():
             controller = data['controller']
             device_info = data['info']
             if device_info.get('is_strip') or device_info.get('is_plug'):
                 log_alias = self.get_alias('device', mac)
                 logging.info(f"Extinction planifiée pour: {log_alias} ({mac})")
                 tasks.append(controller.turn_all_outlets_off())
         if tasks:
             logging.info(f"Exécution {len(tasks)} tâches extinction...")
             results = await asyncio.gather(*tasks, return_exceptions=True)
             success_count = sum(1 for r in results if not isinstance(r, Exception))
             fail_count = len(results) - success_count
             for result in results:
                 if isinstance(result, Exception): logging.error(f"Err extinction: {result}")
             logging.info(f"Extinction terminée. Succès: {success_count}, Échecs: {fail_count}.")
         else: logging.info("Aucune prise/barre Kasa à éteindre.")


    def on_closing(self):
        """Gère la fermeture de l'application."""
        # ... (Inchangé - demande confirmation, arrête monitoring, éteint prises) ...
        quit_confirmed = False
        delay_ms = 1000 # Délai par défaut
        if self.monitoring_active:
             if messagebox.askyesno("Quitter", "Le monitoring est actif. Voulez-vous l'arrêter, sauvegarder et quitter ?", parent=self.root):
                 logging.info("Arrêt monitoring, sauvegarde et fermeture demandés...")
                 self.stop_monitoring() # Appelle _turn_off_all_kasa_safely
                 # Sauvegarder la configuration en quittant ?
                 # self.save_configuration() # Décommenter pour sauvegarder
                 quit_confirmed = True
                 delay_ms = 1500 # Un peu plus de temps pour arrêt + sauvegarde
             else: return
        else:
             if messagebox.askyesno("Quitter", "Voulez-vous sauvegarder avant de quitter ?", parent=self.root, default=messagebox.NO):
                  self.save_configuration() # Sauvegarder si 'Oui'

             if messagebox.askyesno("Quitter", "Êtes-vous sûr de vouloir quitter ?", parent=self.root):
                 logging.info("Fermeture demandée (monitoring inactif).")
                 self._turn_off_all_kasa_safely()
                 quit_confirmed = True
                 delay_ms = 1000
             else: return

        if quit_confirmed:
            logging.info(f"Fermeture de l'application dans {delay_ms / 1000} secondes...")
            self.root.after(delay_ms, self.root.destroy)


if __name__ == "__main__":
    root = tk.Tk()
    app = GreenhouseApp(root)
    root.mainloop()