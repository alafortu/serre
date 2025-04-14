# greenhouse_app.py
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, simpledialog
import asyncio
import threading
import queue
import logging
import uuid
from datetime import datetime, timedelta

# Importer les modules personnalisés
from logger_setup import setup_logging
from discover_device import DeviceDiscoverer # Votre classe
from device_control import DeviceController   # Votre classe
from temp_sensor_wrapper import TempSensorManager
from light_sensor import BH1750Manager # Votre classe
from config_manager import load_config, save_config

# --- Constantes ---
OPERATORS = ['<', '>', '=', '!=', '<=', '>=']
ACTIONS = ['ON', 'OFF']
UNTIL_TYPES = ['Aucun', 'Timer (secondes)', 'Capteur']
DEFAULT_CONFIG_FILE = 'config.yaml'

class GreenhouseApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Gestionnaire de Serre")
        # Ajuster la taille initiale si nécessaire
        # self.root.geometry("1000x800")

        # --- Initialisation Backend ---
        self.log_queue = queue.Queue()
        setup_logging(self.log_queue)

        self.config = load_config(DEFAULT_CONFIG_FILE)
        self.aliases = self.config.get('aliases', {"sensors": {}, "devices": {}, "outlets": {}})
        self.rules = self.config.get('rules', [])

        # Gestionnaires de Périphériques (seront initialisés après la découverte)
        self.kasa_devices = {} # dict: {ip: {'info': dict, 'controller': DeviceController}}
        self.temp_manager = TempSensorManager()
        self.light_manager = BH1750Manager() # Utilise les adresses par défaut [0x23, 0x5C]

        # Listes pour les dropdowns (seront peuplées après découverte)
        self.available_sensors = [] # Liste de tuples (display_name, internal_id)
        self.available_kasa_strips = [] # Liste de tuples (display_name, ip)
        self.available_outlets = {} # Dict: {kasa_ip: [(display_name, index), ...]}

        # État de l'application
        self.monitoring_active = False
        self.monitoring_thread = None
        self.asyncio_loop = None
        self.ui_update_job = None

        # --- Interface Utilisateur ---
        self.create_widgets()
        self.populate_initial_ui_data() # Peuple l'UI avec les données chargées
        self.update_log_display() # Démarrer la vérification de la queue de logs

        # --- Démarrage Découverte ---
        self.discover_all_devices()

        # --- Gestion Fermeture ---
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def get_alias(self, item_type, item_id, sub_id=None):
        """Récupère un alias ou retourne l'ID si non trouvé."""
        try:
            if item_type == 'sensor':
                return self.aliases['sensors'].get(str(item_id), str(item_id))
            elif item_type == 'device':
                return self.aliases['devices'].get(str(item_id), str(item_id))
            elif item_type == 'outlet':
                # item_id est l'IP de la barre, sub_id est l'index de la prise
                return self.aliases['outlets'].get(str(item_id), {}).get(str(sub_id), f"Prise {sub_id}")
        except KeyError:
            # Gérer le cas où la structure d'alias n'est pas complète
            if sub_id is not None:
                return f"{item_id} - Prise {sub_id}"
            return str(item_id)
        return str(item_id) # Fallback final

    def update_alias(self, item_type, item_id, new_alias, sub_id=None):
        """Met à jour un alias dans la structure et prépare la sauvegarde."""
        # Assurer que les dictionnaires imbriqués existent
        if 'aliases' not in self.config: self.config['aliases'] = {}
        if item_type not in self.config['aliases']: self.config['aliases'][item_type] = {}
        if item_type == 'outlets':
             if 'outlets' not in self.config['aliases']: self.config['aliases']['outlets'] = {}
             if str(item_id) not in self.config['aliases']['outlets']: self.config['aliases']['outlets'][str(item_id)] = {}
             self.config['aliases']['outlets'][str(item_id)][str(sub_id)] = new_alias
        else:
             self.config['aliases'][item_type][str(item_id)] = new_alias

        self.aliases = self.config['aliases'] # Mettre à jour la copie locale utilisée par get_alias
        logging.info(f"Alias mis à jour pour {item_type} {item_id}" + (f"[{sub_id}]" if sub_id is not None else "") + f": '{new_alias}'")
        # Pourrait déclencher une sauvegarde automatique ou juste marquer comme 'modifié'
        # self.save_configuration() # Optionnel: sauvegarder immédiatement


    def edit_alias_dialog(self, item_type, item_id, current_name, sub_id=None):
        """Ouvre une popup pour éditer un alias."""
        prompt = f"Entrez un nouveau nom pour {item_type} '{current_name}'"
        if item_type == 'outlet':
            prompt = f"Entrez un nouveau nom pour la prise '{current_name}' (Barre: {self.get_alias('device', item_id)})"

        new_name = simpledialog.askstring("Modifier Alias", prompt, initialvalue=current_name, parent=self.root)

        if new_name and new_name != current_name:
            self.update_alias(item_type, item_id, new_name, sub_id)
            # Rafraîchir l'UI où cet alias est utilisé (dropdowns, labels, etc.)
            self.refresh_device_lists() # Rafraîchit les listes internes
            self.repopulate_all_rule_dropdowns() # Met à jour les dropdowns dans les règles existantes
            self.update_status_display() # Met à jour les labels dans la section statut
            self.root.update_idletasks() # Forcer Tkinter à traiter les changements d'UI

    # --- Création des Widgets ---
    def create_widgets(self):
        # --- Cadre Principal ---
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- Cadre des Règles (Haut) ---
        rules_frame_container = ttk.LabelFrame(main_frame, text="Règles d'Automatisation", padding="10")
        rules_frame_container.pack(fill=tk.X, expand=False, pady=5)

        # Canvas et Scrollbar pour les règles
        self.rules_canvas = tk.Canvas(rules_frame_container)
        scrollbar = ttk.Scrollbar(rules_frame_container, orient="vertical", command=self.rules_canvas.yview)
        self.scrollable_rules_frame = ttk.Frame(self.rules_canvas) # Frame à l'intérieur du canvas

        self.scrollable_rules_frame.bind(
            "<Configure>",
            lambda e: self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))
        )

        self.rules_canvas.create_window((0, 0), window=self.scrollable_rules_frame, anchor="nw")
        self.rules_canvas.configure(yscrollcommand=scrollbar.set)

        # Empaquetage du canvas et de la scrollbar
        self.rules_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Ajuster la hauteur initiale du canvas si nécessaire
        self.rules_canvas.config(height=250) # Hauteur initiale pour ~4-5 règles

        # Bouton Ajouter Règle
        add_rule_button = ttk.Button(main_frame, text="➕ Ajouter une Règle", command=self.add_rule_ui)
        add_rule_button.pack(pady=5)

        # Dictionnaire pour garder une trace des widgets de chaque règle
        self.rule_widgets = {} # {rule_id: {'frame': tk.Frame, 'widgets': dict_of_widgets}}

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

        # --- Section Statut ---
        status_frame = ttk.LabelFrame(status_log_frame, text="Statut Actuel", padding="10")
        status_frame.pack(fill=tk.BOTH, expand=True, side=tk.LEFT, padx=5)

        # Canvas et Scrollbar pour le statut
        status_canvas = tk.Canvas(status_frame)
        status_scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=status_canvas.yview)
        self.scrollable_status_frame = ttk.Frame(status_canvas) # Frame à l'intérieur du canvas status

        self.scrollable_status_frame.bind(
            "<Configure>",
            lambda e: status_canvas.configure(scrollregion=status_canvas.bbox("all"))
        )
        status_canvas.create_window((0, 0), window=self.scrollable_status_frame, anchor="nw")
        status_canvas.configure(yscrollcommand=status_scrollbar.set)
        status_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        status_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Labels pour les capteurs et états (seront créés dynamiquement)
        self.status_labels = {} # {id: {'label_name': tk.Label, 'label_value': tk.Label, 'button_edit': tk.Button}}

        # --- Section Logs ---
        log_frame = ttk.LabelFrame(status_log_frame, text="Journal d'Événements", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, side=tk.RIGHT, padx=5)

        self.log_display = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=15)
        self.log_display.pack(fill=tk.BOTH, expand=True)

    # --- Peuplement Initial de l'UI ---
    def populate_initial_ui_data(self):
        """Remplit l'UI avec les règles chargées depuis la config."""
        for rule_data in self.rules:
            self.add_rule_ui(rule_data=rule_data)
        # Le rafraîchissement des listes de périphériques se fait après la découverte

    # --- Gestion des Règles dans l'UI ---
    def add_rule_ui(self, rule_data=None):
        """Ajoute une ligne de règle à l'interface utilisateur."""
        rule_id = rule_data.get('id', str(uuid.uuid4())) if rule_data else str(uuid.uuid4())
        if not rule_data: # Si c'est une nouvelle règle, l'ajouter à notre liste interne
             rule_data = {'id': rule_id} # Init avec seulement l'id
             self.rules.append(rule_data) # Ajouter à la liste interne
        elif not any(r.get('id') == rule_id for r in self.rules):
             # Si rule_data vient du chargement, s'assurer qu'elle est dans self.rules
             self.rules.append(rule_data)

        rule_frame = ttk.Frame(self.scrollable_rules_frame, padding="5", borderwidth=1, relief="groove")
        rule_frame.pack(fill=tk.X, pady=2, padx=2)

        widgets = {}

        # --- Condition "SI" ---
        ttk.Label(rule_frame, text="SI").pack(side=tk.LEFT, padx=2)
        widgets['sensor_var'] = tk.StringVar()
        widgets['sensor_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['sensor_var'], width=20, state="readonly")
        widgets['sensor_combo']['values'] = [name for name, _id in self.available_sensors]
        widgets['sensor_combo'].pack(side=tk.LEFT, padx=2)
        widgets['sensor_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['operator_var'] = tk.StringVar()
        widgets['operator_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['operator_var'], values=OPERATORS, width=4, state="readonly")
        widgets['operator_combo'].pack(side=tk.LEFT, padx=2)
        widgets['operator_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['value_var'] = tk.StringVar()
        widgets['value_entry'] = ttk.Entry(rule_frame, textvariable=widgets['value_var'], width=6)
        widgets['value_entry'].pack(side=tk.LEFT, padx=2)
        widgets['value_entry'].bind('<KeyRelease>', lambda e, rid=rule_id: self.on_rule_change(rid)) # Update on key release

        # --- Action "ALORS" ---
        ttk.Label(rule_frame, text="ALORS").pack(side=tk.LEFT, padx=(10, 2))
        widgets['kasa_var'] = tk.StringVar()
        widgets['kasa_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['kasa_var'], width=20, state="readonly")
        widgets['kasa_combo']['values'] = [name for name, _ip in self.available_kasa_strips]
        widgets['kasa_combo'].pack(side=tk.LEFT, padx=2)
        widgets['kasa_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.update_outlet_options(rid)) # Mise à jour des prises

        widgets['outlet_var'] = tk.StringVar()
        widgets['outlet_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['outlet_var'], width=15, state="readonly")
        # Les valeurs des prises sont définies par update_outlet_options
        widgets['outlet_combo'].pack(side=tk.LEFT, padx=2)
        widgets['outlet_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))


        widgets['action_var'] = tk.StringVar()
        widgets['action_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['action_var'], values=ACTIONS, width=5, state="readonly")
        widgets['action_combo'].pack(side=tk.LEFT, padx=2)
        widgets['action_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # --- Condition "JUSQU'À" (Optionnel) ---
        ttk.Label(rule_frame, text="JUSQU'À").pack(side=tk.LEFT, padx=(10, 2))
        widgets['until_type_var'] = tk.StringVar()
        widgets['until_type_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['until_type_var'], values=UNTIL_TYPES, width=15, state="readonly")
        widgets['until_type_combo'].pack(side=tk.LEFT, padx=2)
        widgets['until_type_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.toggle_until_fields(rid))

        # Champs pour 'Timer' (initiallement cachés)
        widgets['until_timer_frame'] = ttk.Frame(rule_frame) # Frame pour grouper timer
        widgets['until_timer_value_var'] = tk.StringVar()
        widgets['until_timer_value_entry'] = ttk.Entry(widgets['until_timer_frame'], textvariable=widgets['until_timer_value_var'], width=6)
        widgets['until_timer_value_entry'].pack(side=tk.LEFT)
        widgets['until_timer_value_entry'].bind('<KeyRelease>', lambda e, rid=rule_id: self.on_rule_change(rid))
        ttk.Label(widgets['until_timer_frame'], text="secs").pack(side=tk.LEFT, padx=1)


        # Champs pour 'Capteur' (initiallement cachés)
        widgets['until_sensor_frame'] = ttk.Frame(rule_frame) # Frame pour grouper capteur
        widgets['until_sensor_var'] = tk.StringVar()
        widgets['until_sensor_combo'] = ttk.Combobox(widgets['until_sensor_frame'], textvariable=widgets['until_sensor_var'], width=20, state="readonly")
        widgets['until_sensor_combo']['values'] = [name for name, _id in self.available_sensors]
        widgets['until_sensor_combo'].pack(side=tk.LEFT, padx=2)
        widgets['until_sensor_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['until_operator_var'] = tk.StringVar()
        widgets['until_operator_combo'] = ttk.Combobox(widgets['until_sensor_frame'], textvariable=widgets['until_operator_var'], values=OPERATORS, width=4, state="readonly")
        widgets['until_operator_combo'].pack(side=tk.LEFT, padx=2)
        widgets['until_operator_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['until_sensor_value_var'] = tk.StringVar()
        widgets['until_sensor_value_entry'] = ttk.Entry(widgets['until_sensor_frame'], textvariable=widgets['until_sensor_value_var'], width=6)
        widgets['until_sensor_value_entry'].pack(side=tk.LEFT, padx=2)
        widgets['until_sensor_value_entry'].bind('<KeyRelease>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # Empaqueter les frames 'until' (mais ne pas les afficher encore)
        widgets['until_timer_frame'].pack(side=tk.LEFT, padx=2)
        widgets['until_sensor_frame'].pack(side=tk.LEFT, padx=2)
        widgets['until_timer_frame'].pack_forget() # Cacher par défaut
        widgets['until_sensor_frame'].pack_forget() # Cacher par défaut

        # Bouton Supprimer
        delete_button = ttk.Button(rule_frame, text="🗑️", width=3, command=lambda rid=rule_id: self.delete_rule(rid))
        delete_button.pack(side=tk.RIGHT, padx=5)

        # Stocker les widgets pour accès futur
        self.rule_widgets[rule_id] = {'frame': rule_frame, 'widgets': widgets}

        # Peupler les widgets si des données existent (chargement)
        if rule_data and rule_id in self.rule_widgets:
             self._populate_rule_ui_from_data(rule_id, rule_data)

        # Mettre à jour la barre de défilement
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))

    def _populate_rule_ui_from_data(self, rule_id, rule_data):
        """Remplit les widgets d'une règle spécifique avec les données chargées."""
        widgets = self.rule_widgets[rule_id]['widgets']

        # SI
        sensor_id = rule_data.get('sensor_id')
        if sensor_id:
             widgets['sensor_var'].set(self.get_alias('sensor', sensor_id))
        widgets['operator_var'].set(rule_data.get('operator', ''))
        widgets['value_var'].set(rule_data.get('threshold', ''))

        # ALORS
        kasa_ip = rule_data.get('target_device_ip')
        if kasa_ip:
            widgets['kasa_var'].set(self.get_alias('device', kasa_ip))
            self.update_outlet_options(rule_id, preselect_outlet_index=rule_data.get('target_outlet_index')) # Ceci va aussi définir outlet_var

        widgets['action_var'].set(rule_data.get('action', ''))

        # JUSQU'À
        until_data = rule_data.get('until_condition', {})
        until_type = until_data.get('type', 'Aucun') # Utilise 'Aucun' comme défaut
        widgets['until_type_var'].set(until_type)

        if until_type == 'Timer (secondes)':
            widgets['until_timer_value_var'].set(until_data.get('duration', ''))
        elif until_type == 'Capteur':
            until_sensor_id = until_data.get('sensor_id')
            if until_sensor_id:
                widgets['until_sensor_var'].set(self.get_alias('sensor', until_sensor_id))
            widgets['until_operator_var'].set(until_data.get('operator', ''))
            widgets['until_sensor_value_var'].set(until_data.get('threshold', ''))

        # Afficher/Cacher les champs JUSQU'À appropriés
        self.toggle_until_fields(rule_id)


    def delete_rule(self, rule_id):
        """Supprime une règle de l'UI et de la liste interne."""
        if rule_id in self.rule_widgets:
            self.rule_widgets[rule_id]['frame'].destroy()
            del self.rule_widgets[rule_id]
            self.rules = [rule for rule in self.rules if rule.get('id') != rule_id]
            logging.info(f"Règle {rule_id} supprimée.")
             # Mettre à jour la barre de défilement
            self.rules_canvas.update_idletasks() # S'assurer que la destruction est traitée
            self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))


    def update_outlet_options(self, rule_id, preselect_outlet_index=None):
        """Met à jour les options de prise basées sur la barre Kasa sélectionnée."""
        if rule_id not in self.rule_widgets: return

        widgets = self.rule_widgets[rule_id]['widgets']
        selected_kasa_name = widgets['kasa_var'].get()

        # Trouver l'IP correspondant au nom sélectionné
        selected_ip = None
        for name, ip in self.available_kasa_strips:
            if name == selected_kasa_name:
                selected_ip = ip
                break

        outlet_options = []
        current_outlet_alias = "" # Pour la présélection
        if selected_ip and selected_ip in self.available_outlets:
            outlet_options = [name for name, _index in self.available_outlets[selected_ip]]
            if preselect_outlet_index is not None:
                 for name, index in self.available_outlets[selected_ip]:
                     if index == preselect_outlet_index:
                         current_outlet_alias = name
                         break

        widgets['outlet_combo']['values'] = outlet_options
        if current_outlet_alias:
             widgets['outlet_var'].set(current_outlet_alias)
        elif outlet_options: # S'il y a des options mais pas de présélection, choisir la première
             widgets['outlet_var'].set(outlet_options[0])
        else:
            widgets['outlet_var'].set('') # Vider si aucune option

        # Mise à jour de la règle interne après le changement de Kasa/Prise
        self.on_rule_change(rule_id)


    def toggle_until_fields(self, rule_id):
        """Affiche ou cache les champs 'Jusqu'à' en fonction du type sélectionné."""
        if rule_id not in self.rule_widgets: return

        widgets = self.rule_widgets[rule_id]['widgets']
        until_type = widgets['until_type_var'].get()

        # Cacher les deux frames spécifiques d'abord
        widgets['until_timer_frame'].pack_forget()
        widgets['until_sensor_frame'].pack_forget()

        # Afficher la frame appropriée
        if until_type == 'Timer (secondes)':
            widgets['until_timer_frame'].pack(side=tk.LEFT, padx=2)
        elif until_type == 'Capteur':
            widgets['until_sensor_frame'].pack(side=tk.LEFT, padx=2)

        # Mise à jour de la règle interne
        self.on_rule_change(rule_id)


    def on_rule_change(self, rule_id):
        """Met à jour la structure de données de la règle lorsque l'UI change."""
        if rule_id not in self.rule_widgets: return
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data: return # Ne devrait pas arriver

        widgets = self.rule_widgets[rule_id]['widgets']

        # Trouver les IDs internes à partir des noms affichés (alias)
        sensor_name = widgets['sensor_var'].get()
        kasa_name = widgets['kasa_var'].get()
        outlet_name = widgets['outlet_var'].get()
        until_sensor_name = widgets['until_sensor_var'].get()

        sensor_id = next((sid for name, sid in self.available_sensors if name == sensor_name), None)
        kasa_ip = next((kip for name, kip in self.available_kasa_strips if name == kasa_name), None)
        outlet_index = None
        if kasa_ip and kasa_ip in self.available_outlets:
            outlet_index = next((idx for name, idx in self.available_outlets[kasa_ip] if name == outlet_name), None)
        until_sensor_id = next((sid for name, sid in self.available_sensors if name == until_sensor_name), None)

        # --- Mise à jour de rule_data ---
        rule_data['sensor_id'] = sensor_id
        rule_data['operator'] = widgets['operator_var'].get()
        try:
            rule_data['threshold'] = float(widgets['value_var'].get()) if widgets['value_var'].get() else None
        except ValueError:
            rule_data['threshold'] = None # Ou garder l'ancienne valeur? Ou logger une erreur?

        rule_data['target_device_ip'] = kasa_ip
        rule_data['target_outlet_index'] = outlet_index
        rule_data['action'] = widgets['action_var'].get()

        # --- Mise à jour de 'until_condition' ---
        until_type = widgets['until_type_var'].get()
        if until_type == 'Aucun':
            if 'until_condition' in rule_data:
                del rule_data['until_condition']
        else:
            if 'until_condition' not in rule_data:
                rule_data['until_condition'] = {}

            rule_data['until_condition']['type'] = until_type

            if until_type == 'Timer (secondes)':
                try:
                    rule_data['until_condition']['duration'] = int(widgets['until_timer_value_var'].get()) if widgets['until_timer_value_var'].get() else None
                except ValueError:
                    rule_data['until_condition']['duration'] = None
                # Supprimer les clés de capteur si elles existent
                rule_data['until_condition'].pop('sensor_id', None)
                rule_data['until_condition'].pop('operator', None)
                rule_data['until_condition'].pop('threshold', None)

            elif until_type == 'Capteur':
                rule_data['until_condition']['sensor_id'] = until_sensor_id
                rule_data['until_condition']['operator'] = widgets['until_operator_var'].get()
                try:
                    rule_data['until_condition']['threshold'] = float(widgets['until_sensor_value_var'].get()) if widgets['until_sensor_value_var'].get() else None
                except ValueError:
                     rule_data['until_condition']['threshold'] = None
                # Supprimer la clé de durée si elle existe
                rule_data['until_condition'].pop('duration', None)

        # logging.debug(f"Données de la règle {rule_id} mises à jour: {rule_data}")


    def repopulate_all_rule_dropdowns(self):
        """Met à jour toutes les listes déroulantes dans toutes les règles."""
        sensor_names = [name for name, _id in self.available_sensors]
        kasa_names = [name for name, _ip in self.available_kasa_strips]

        for rule_id, data in self.rule_widgets.items():
            widgets = data['widgets']
            current_sensor = widgets['sensor_var'].get()
            current_kasa = widgets['kasa_var'].get()
            current_until_sensor = widgets['until_sensor_var'].get()

            widgets['sensor_combo']['values'] = sensor_names
            widgets['kasa_combo']['values'] = kasa_names
            widgets['until_sensor_combo']['values'] = sensor_names

            # Réessayer de sélectionner les valeurs actuelles si elles existent toujours
            if current_sensor in sensor_names: widgets['sensor_var'].set(current_sensor)
            else: widgets['sensor_var'].set('')

            if current_kasa in kasa_names:
                widgets['kasa_var'].set(current_kasa)
                # Important: Rafraîchir les options de prises pour cette barre Kasa
                self.update_outlet_options(rule_id)
            else:
                widgets['kasa_var'].set('')
                widgets['outlet_combo']['values'] = []
                widgets['outlet_var'].set('')

            if current_until_sensor in sensor_names: widgets['until_sensor_var'].set(current_until_sensor)
            else: widgets['until_sensor_var'].set('')


    # --- Découverte et Rafraîchissement des Périphériques ---
    def discover_all_devices(self):
        """Lance la découverte des capteurs et appareils Kasa."""
        logging.info("Démarrage de la découverte des périphériques...")
        # --- Découverte Capteurs (Synchrone mais rapide en général) ---
        try:
            self.temp_manager.discover_sensors() # Utilise la méthode de la classe wrapper
        except Exception as e:
             logging.error(f"Erreur pendant la découverte des capteurs de température: {e}")
        try:
             self.light_manager.scan_sensors() # Méthode de BH1750Manager
        except Exception as e:
            logging.error(f"Erreur pendant la découverte des capteurs de lumière: {e}")

        # --- Découverte Kasa (Asynchrone) ---
        # Lance la découverte Kasa dans un thread séparé pour ne pas bloquer l'UI
        threading.Thread(target=self._run_kasa_discovery_async, daemon=True).start()

    def _run_kasa_discovery_async(self):
        """Exécute la découverte Kasa dans la boucle asyncio."""
        # Créer une nouvelle boucle d'événements pour ce thread si nécessaire
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(self._async_discover_kasa())
        # Pas besoin de loop.close() si on réutilise la boucle pour le monitoring

    async def _async_discover_kasa(self):
        """Tâche asynchrone pour découvrir les appareils Kasa."""
        discoverer = DeviceDiscoverer()
        discovered_kasa = await discoverer.discover() # Appelle votre méthode discover

        # Mettre à jour l'état de l'application depuis le thread principal via la queue
        # Ou utiliser root.after si la fonction est appelée depuis le thread principal
        # Ici, comme on est dans un autre thread, on ne peut pas utiliser root.after directement
        # On peut passer les résultats via la queue ou appeler une fonction thread-safe de Tkinter
        # Simplifions: on met à jour directement self.kasa_devices (attention aux race conditions si accès concurrentiel)
        # Une meilleure approche serait d'utiliser `loop.call_soon_threadsafe` ou une queue.

        new_kasa_devices = {}
        for device_info in discovered_kasa:
            ip = device_info['ip']
            # Utiliser les hints pour créer le DeviceController
            controller = DeviceController(
                 ip,
                 is_strip=device_info.get('is_strip'),
                 is_plug=device_info.get('is_plug')
            )
            new_kasa_devices[ip] = {'info': device_info, 'controller': controller}
            # Éteindre toutes les prises lors de la découverte initiale
            if self.monitoring_active is False: # Seulement si le monitoring n'est pas actif
                logging.info(f"Découverte: Tentative d'extinction de toutes les prises de {ip}")
                try:
                    # Il faut exécuter les commandes de contrôle dans la boucle asyncio
                    if device_info.get('is_strip') or device_info.get('is_plug'):
                         await controller.turn_all_outlets_off() # Utilise la méthode de votre contrôleur
                except Exception as e:
                    logging.error(f"Erreur lors de l'extinction initiale des prises de {ip}: {e}")


        self.kasa_devices = new_kasa_devices
        logging.info(f"Découverte Kasa terminée. {len(self.kasa_devices)} appareil(s) trouvé(s).")

        # Planifier la mise à jour de l'UI dans le thread principal Tkinter
        self.root.after(100, self.refresh_device_lists)


    def refresh_device_lists(self):
        """Met à jour les listes internes et les dropdowns après découverte."""
        logging.info("Rafraîchissement des listes de périphériques dans l'UI.")
        # --- Capteurs ---
        temp_ids = self.temp_manager.get_sensor_ids()
        light_ids_int = self.light_manager.get_active_sensors() # Retourne des int
        light_ids_hex = [hex(addr) for addr in light_ids_int]

        self.available_sensors = []
        for tid in temp_ids:
            self.available_sensors.append((self.get_alias('sensor', tid), tid))
        for i, addr_int in enumerate(light_ids_int):
            addr_hex = light_ids_hex[i]
            self.available_sensors.append((self.get_alias('sensor', addr_hex), addr_hex))

        # --- Appareils Kasa et Prises ---
        self.available_kasa_strips = []
        self.available_outlets = {}
        for ip, data in self.kasa_devices.items():
            device_info = data['info']
            device_alias = self.get_alias('device', ip)
            self.available_kasa_strips.append((device_alias, ip))

            outlets = []
            if device_info.get('is_strip') or device_info.get('is_plug'):
                 # Utiliser les infos de prise de la découverte initiale
                 discovered_outlets = device_info.get('outlets', [])
                 for outlet_data in discovered_outlets:
                     index = outlet_data.get('index')
                     # Utiliser l'alias découvert comme fallback si aucun alias perso n'est défini
                     discovered_alias = outlet_data.get('alias', f"Prise {index}")
                     outlet_alias = self.aliases.get('outlets', {}).get(str(ip), {}).get(str(index), discovered_alias)
                     outlets.append((outlet_alias, index))

            self.available_outlets[ip] = outlets

        # --- Mettre à jour l'UI ---
        self.repopulate_all_rule_dropdowns()
        self.update_status_display() # Créer ou mettre à jour les labels de statut

        logging.info("Listes de périphériques UI mises à jour.")


    # --- Affichage du Statut ---
    def update_status_display(self):
        """Crée ou met à jour les labels dans la section statut."""
        # Vider l'ancien contenu (plus simple que de chercher/mettre à jour)
        for widget in self.scrollable_status_frame.winfo_children():
            widget.destroy()
        self.status_labels = {}

        row_num = 0

        # --- Affichage Capteurs ---
        ttk.Label(self.scrollable_status_frame, text="Capteurs:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(5, 2))
        row_num += 1

        # Température
        temp_readings = self.temp_manager.read_all_temperatures()
        for sensor_id, temp in temp_readings.items():
            alias = self.get_alias('sensor', sensor_id)
            frame = ttk.Frame(self.scrollable_status_frame)
            frame.grid(row=row_num, column=0, columnspan=4, sticky='w')
            name_label = ttk.Label(frame, text=f"{alias}:")
            name_label.pack(side=tk.LEFT, padx=5)
            value_label = ttk.Label(frame, text=f"{temp}°C" if temp is not None else "Erreur/Non prêt", width=15)
            value_label.pack(side=tk.LEFT, padx=5)
            edit_button = ttk.Button(frame, text="✎", width=2, command=lambda s_id=sensor_id, s_name=alias: self.edit_alias_dialog('sensor', s_id, s_name))
            edit_button.pack(side=tk.LEFT, padx=2)
            self.status_labels[sensor_id] = {'type': 'sensor', 'label_name': name_label, 'label_value': value_label, 'button_edit': edit_button}
            row_num += 1

        # Lumière
        light_readings = self.light_manager.read_all_sensors() # Retourne {hex_addr: lux}
        for addr_hex, lux in light_readings.items():
            alias = self.get_alias('sensor', addr_hex)
            frame = ttk.Frame(self.scrollable_status_frame)
            frame.grid(row=row_num, column=0, columnspan=4, sticky='w')
            name_label = ttk.Label(frame, text=f"{alias}:")
            name_label.pack(side=tk.LEFT, padx=5)
            value_label = ttk.Label(frame, text=f"{lux:.1f} Lux" if lux is not None else "Erreur/Non prêt", width=15)
            value_label.pack(side=tk.LEFT, padx=5)
            edit_button = ttk.Button(frame, text="✎", width=2, command=lambda s_id=addr_hex, s_name=alias: self.edit_alias_dialog('sensor', s_id, s_name))
            edit_button.pack(side=tk.LEFT, padx=2)
            self.status_labels[addr_hex] = {'type': 'sensor', 'label_name': name_label, 'label_value': value_label, 'button_edit': edit_button}
            row_num += 1

        # --- Affichage États Kasa ---
        ttk.Label(self.scrollable_status_frame, text="Prises Kasa:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(10, 2))
        row_num += 1

        for ip, data in self.kasa_devices.items():
            device_alias = self.get_alias('device', ip)
            device_info = data['info']

            # Afficher le nom de la barre elle-même
            frame_dev = ttk.Frame(self.scrollable_status_frame)
            frame_dev.grid(row=row_num, column=0, columnspan=4, sticky='w')
            dev_name_label = ttk.Label(frame_dev, text=f"{device_alias} ({ip}):")
            dev_name_label.pack(side=tk.LEFT, padx=5)
            dev_edit_button = ttk.Button(frame_dev, text="✎", width=2, command=lambda d_ip=ip, d_name=device_alias: self.edit_alias_dialog('device', d_ip, d_name))
            dev_edit_button.pack(side=tk.LEFT, padx=2)
            self.status_labels[ip] = {'type': 'device', 'label_name': dev_name_label, 'button_edit': dev_edit_button}
            row_num += 1

            # Afficher les prises de cette barre
            if ip in self.available_outlets:
                for outlet_alias, index in self.available_outlets[ip]:
                     # Trouver l'état actuel (depuis la découverte ou une mise à jour ultérieure)
                     current_state = "Inconnu"
                     if 'outlets' in device_info:
                         outlet_info = next((o for o in device_info['outlets'] if o.get('index') == index), None)
                         if outlet_info:
                             current_state = "ON" if outlet_info.get('is_on') else "OFF"

                     frame_outlet = ttk.Frame(self.scrollable_status_frame)
                     frame_outlet.grid(row=row_num, column=1, columnspan=3, sticky='w', padx=(20,0)) # Indenter les prises
                     outlet_name_label = ttk.Label(frame_outlet, text=f"└─ {outlet_alias}:")
                     outlet_name_label.pack(side=tk.LEFT, padx=5)
                     outlet_value_label = ttk.Label(frame_outlet, text=current_state, width=10)
                     outlet_value_label.pack(side=tk.LEFT, padx=5)
                     outlet_edit_button = ttk.Button(frame_outlet, text="✎", width=2, command=lambda d_ip=ip, o_idx=index, o_name=outlet_alias: self.edit_alias_dialog('outlet', d_ip, o_name, sub_id=o_idx))
                     outlet_edit_button.pack(side=tk.LEFT, padx=2)

                     # Utiliser une clé composite pour les prises dans status_labels
                     outlet_key = f"{ip}_{index}"
                     self.status_labels[outlet_key] = {'type': 'outlet', 'ip': ip, 'index': index, 'label_name': outlet_name_label, 'label_value': outlet_value_label, 'button_edit': outlet_edit_button}
                     row_num += 1

        # Ajuster la scrollregion du canvas status
        self.scrollable_status_frame.update_idletasks()
        status_canvas = self.scrollable_status_frame.master
        status_canvas.configure(scrollregion=status_canvas.bbox("all"))

    # --- Mise à Jour Périodique (Statut & Logs) ---
    def schedule_periodic_updates(self):
        """Planifie la mise à jour périodique de l'UI pendant le monitoring."""
        self.update_live_status()
        # Planifier la prochaine mise à jour (ex: toutes les 5 secondes)
        self.ui_update_job = self.root.after(5000, self.schedule_periodic_updates)

    def cancel_periodic_updates(self):
        """Annule la mise à jour périodique de l'UI."""
        if self.ui_update_job:
            self.root.after_cancel(self.ui_update_job)
            self.ui_update_job = None

    def update_live_status(self):
        """Met à jour UNIQUEMENT LES VALEURS affichées dans la section Statut."""
        if not self.monitoring_active: return # Ne pas mettre à jour si arrêté

        logging.debug("Mise à jour des valeurs de statut en direct.")

        # --- Mettre à jour les VALEURS des capteurs ---
        temp_readings = self.temp_manager.read_all_temperatures()
        light_readings = self.light_manager.read_all_sensors()

        for sensor_id, data in self.status_labels.items():
            if data['type'] == 'sensor':
                # On met à jour SEULEMENT label_value
                value = None
                unit = ""
                is_temp = sensor_id in temp_readings
                is_light = sensor_id in light_readings

                if is_temp:
                    value = temp_readings.get(sensor_id)
                    unit = "°C"
                elif is_light:
                    value = light_readings.get(sensor_id)
                    unit = " Lux"

                if value is not None:
                    display_text = f"{value:.1f}{unit}" if isinstance(value, float) else f"{value}{unit}"
                    # Vérifier si le label existe toujours (par précaution)
                    if data['label_value'].winfo_exists():
                         data['label_value'].config(text=display_text)
                else:
                    if data['label_value'].winfo_exists():
                         data['label_value'].config(text="Erreur/N/A")

                # # LIGNE SUPPRIMÉE/COMMENTÉE: Ne pas mettre à jour le nom ici
                # # data['label_name'].config(text=f"{self.get_alias('sensor', sensor_id)}:")

        # --- Mettre à jour les VALEURS des états Kasa ---
        # (Utilise l'état partagé mis à jour par la boucle asyncio)
        for key, data in self.status_labels.items():
            if data['type'] == 'outlet':
                ip = data['ip']
                index = data['index']
                current_state = self._get_shared_kasa_state(ip, index) # Fonction placeholder
                # Vérifier si le label existe toujours
                if data['label_value'].winfo_exists():
                     data['label_value'].config(text=current_state)

                # # LIGNE SUPPRIMÉE/COMMENTÉE: Ne pas mettre à jour le nom ici
                # # device_alias = self.get_alias('device', ip) # Pas nécessaire ici
                # # outlet_alias = self.get_alias('outlet', ip, sub_id=index)
                # # data['label_name'].config(text=f"└─ {outlet_alias}:")

            # elif data['type'] == 'device':
            #     # LIGNE SUPPRIMÉE/COMMENTÉE: Ne pas mettre à jour le nom de l'appareil ici
            #     # ip = key
            #     # device_alias = self.get_alias('device', ip)
            #     # if data['label_name'].winfo_exists():
            #     #     data['label_name'].config(text=f"{device_alias} ({ip}):")
            #     pass # Rien à mettre à jour périodiquement pour le nom de l'appareil

        # # Pas besoin d'ajuster la scrollregion ici car on ne change que le texte
        # # self.scrollable_status_frame.update_idletasks()
        # # status_canvas = self.scrollable_status_frame.master
        # # status_canvas.configure(scrollregion=status_canvas.bbox("all"))
    def _get_shared_kasa_state(self, ip, index):
        """Récupère l'état Kasa depuis une structure partagée (à remplir par asyncio)."""
        # Ceci est un placeholder. La vraie donnée viendra de la boucle asyncio.
        # Supposons une structure self.live_kasa_states = { ip: { index: bool } }
        try:
             is_on = self.live_kasa_states[ip][index]
             return "ON" if is_on else "OFF"
        except (AttributeError, KeyError):
             # Si la structure n'existe pas ou la clé manque
             return "Inconnu"


    def update_log_display(self):
        """Vérifie la queue de logs et met à jour le widget Text."""
        while True:
            try:
                record = self.log_queue.get_nowait()
            except queue.Empty:
                break
            else:
                self.log_display.config(state=tk.NORMAL)
                self.log_display.insert(tk.END, record + '\n')
                self.log_display.config(state=tk.DISABLED)
                self.log_display.see(tk.END) # Scroll vers la fin
        # Planifier la prochaine vérification
        self.root.after(100, self.update_log_display) # Vérifier toutes les 100ms


    # --- Démarrage / Arrêt du Monitoring ---
    def start_monitoring(self):
        """Démarre la boucle de surveillance des règles."""
        if self.monitoring_active:
            logging.warning("Le monitoring est déjà actif.")
            return

        logging.info("Démarrage du monitoring des règles...")
        self.monitoring_active = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        # Désactiver l'édition des règles pendant le monitoring
        self._set_rules_ui_state(tk.DISABLED)

        # Préparer l'état partagé pour les états Kasa
        self.live_kasa_states = {} # Sera rempli par la boucle asyncio

        # Démarrer la boucle asyncio dans un thread séparé
        self.monitoring_thread = threading.Thread(target=self._run_monitoring_loop, daemon=True)
        self.monitoring_thread.start()

        # Démarrer les mises à jour périodiques de l'UI
        self.schedule_periodic_updates()


    def stop_monitoring(self):
        """Arrête la boucle de surveillance."""
        if not self.monitoring_active:
            logging.warning("Le monitoring n'est pas actif.")
            return

        logging.info("Arrêt du monitoring des règles...")
        self.monitoring_active = False # Signal pour arrêter la boucle asyncio

        # Attendre (optionnel) que le thread se termine proprement
        # if self.monitoring_thread and self.monitoring_thread.is_alive():
        #     self.monitoring_thread.join(timeout=5) # Attendre max 5 secs

        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self._set_rules_ui_state(tk.NORMAL) # Réactiver l'édition

        # Arrêter les mises à jour de l'UI
        self.cancel_periodic_updates()

        # Optionnel mais recommandé: Éteindre toutes les prises par sécurité
        logging.info("Tentative d'extinction de toutes les prises Kasa par sécurité...")
        threading.Thread(target=self._turn_off_all_kasa_safely, daemon=True).start()

        logging.info("Monitoring arrêté.")

    def _set_rules_ui_state(self, state):
        """Active ou désactive les widgets d'édition dans les règles."""
        # Activer/Désactiver le bouton "Ajouter Règle" (trouver le bouton)
        try:
            # Chercher le bouton "Ajouter Règle" de manière plus robuste
            main_frame = self.root.winfo_children()[0] # Suppose que main_frame est le premier enfant
            add_button = next(w for w in main_frame.winfo_children() if isinstance(w, ttk.Button) and "Ajouter" in w.cget("text"))
            if add_button:
                add_button.config(state=state)
        except (IndexError, StopIteration, tk.TclError) as e:
             logging.warning(f"Impossible de trouver ou configurer le bouton 'Ajouter Règle': {e}")


        # Parcourir les widgets de chaque règle
        for rule_id, data in self.rule_widgets.items():
            widgets_dict = data['widgets']
            rule_frame = data['frame'] # Le frame contenant cette règle

            # Trouver le bouton Supprimer associé à cette règle
            try:
                delete_button = next(w for w in rule_frame.winfo_children() if isinstance(w, ttk.Button) and "🗑️" in w.cget("text"))
                if delete_button:
                     delete_button.config(state=state)
            except (StopIteration, tk.TclError) as e:
                 logging.warning(f"Impossible de trouver ou configurer le bouton 'Supprimer' pour la règle {rule_id}: {e}")


            # Parcourir les widgets principaux DANS le dictionnaire de la règle
            for widget_name, widget in widgets_dict.items():
                # Appliquer l'état SEULEMENT aux types de widgets appropriés
                if isinstance(widget, (ttk.Combobox, ttk.Entry)):
                    try:
                        widget.config(state=state if state == tk.DISABLED else 'readonly' if isinstance(widget, ttk.Combobox) else tk.NORMAL)
                        # Note: On remet 'readonly' aux Combobox si on active, sinon NORMAL pour Entry
                        if state == tk.NORMAL and isinstance(widget, ttk.Combobox):
                             widget.config(state='readonly') # Les combobox restent readonly
                        elif state == tk.NORMAL and isinstance(widget, ttk.Entry):
                             widget.config(state=tk.NORMAL)
                        elif state == tk.DISABLED:
                              widget.config(state=tk.DISABLED)

                    except tk.TclError as e:
                        logging.warning(f"Erreur Tcl en configurant l'état pour {widget_name} (règle {rule_id}): {e}")
                elif isinstance(widget, tk.Frame):
                    # Pour les Frames (comme until_timer_frame, until_sensor_frame),
                    # configurer les widgets *à l'intérieur* du frame.
                    for child_widget in widget.winfo_children():
                        if isinstance(child_widget, (ttk.Combobox, ttk.Entry)):
                             try:
                                 # Appliquer la même logique que ci-dessus pour les enfants
                                 if state == tk.NORMAL and isinstance(child_widget, ttk.Combobox):
                                     child_widget.config(state='readonly')
                                 elif state == tk.NORMAL and isinstance(child_widget, ttk.Entry):
                                     child_widget.config(state=tk.NORMAL)
                                 elif state == tk.DISABLED:
                                     child_widget.config(state=tk.DISABLED)
                             except tk.TclError as e:
                                 logging.warning(f"Erreur Tcl en configurant l'état pour un enfant de {widget_name} (règle {rule_id}): {e}")
                        # Ne pas toucher aux Labels dans les frames 'until'

                # Ignorer les autres types comme StringVar, etc.


    def _run_monitoring_loop(self):
        """Fonction exécutée dans le thread de monitoring, gère la boucle asyncio."""
        try:
            self.asyncio_loop = asyncio.get_event_loop()
        except RuntimeError:
            self.asyncio_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.asyncio_loop)

        try:
            self.asyncio_loop.run_until_complete(self._async_monitoring_task())
        except Exception as e:
            logging.critical(f"Erreur majeure dans la boucle de monitoring asyncio: {e}", exc_info=True)
        finally:
            # Nettoyage éventuel si nécessaire
            # self.asyncio_loop.close() # Attention si d'autres tâches l'utilisent
            logging.info("Boucle de monitoring asyncio terminée.")
            # S'assurer que l'UI reflète l'arrêt si ce n'est pas déjà fait
            if self.monitoring_active: # Si l'arrêt vient d'une erreur interne
                 self.root.after(0, self.stop_monitoring)


    async def _async_monitoring_task(self):
        """La tâche principale de monitoring exécutée par asyncio."""
        # Dictionnaire pour suivre l'état des règles "UNTIL" actives
        # { rule_id: {'end_time': datetime | None, 'revert_action': 'ON'|'OFF'} }
        active_until_rules = {}
        last_kasa_update_time = datetime.now()
        kasa_update_interval = timedelta(seconds=10) # Màj état Kasa toutes les 10s

        while self.monitoring_active:
            current_time = datetime.now()
            logging.debug("Cycle de monitoring...")

            # --- 1. Lire les capteurs ---
            # Note: Ces lectures sont synchrones dans les wrappers actuels.
            # Pour une vraie async, il faudrait les adapter ou utiliser run_in_executor.
            try:
                temp_values = self.temp_manager.read_all_temperatures()
                light_values = self.light_manager.read_all_sensors()
                sensor_values = {**temp_values, **light_values} # Fusionner les dictionnaires
                # Filtrer les valeurs None (erreurs de lecture)
                valid_sensor_values = {k: v for k, v in sensor_values.items() if v is not None}
                logging.debug(f"Valeurs capteurs valides: {valid_sensor_values}")
            except Exception as e:
                logging.error(f"Erreur lors de la lecture des capteurs dans la boucle: {e}")
                valid_sensor_values = {} # Pas de données valides si erreur globale

            # --- 2. Lire l'état actuel des Kasa (moins souvent) ---
            # Mettre à jour l'état partagé self.live_kasa_states
            if current_time - last_kasa_update_time >= kasa_update_interval:
                 logging.debug("Mise à jour des états Kasa en direct...")
                 new_live_states = {}
                 for ip, data in self.kasa_devices.items():
                     controller = data['controller']
                     try:
                          # Tenter de connecter/rafraîchir si nécessaire
                         await controller._connect() # Utilise la logique interne de connexion/refresh
                         if controller._device: # Si la connexion/refresh a réussi
                              device_states = await controller.get_outlet_state() # Récupère l'état frais
                              if device_states is not None:
                                   new_live_states[ip] = {outlet['index']: outlet['is_on'] for outlet in device_states}
                              else:
                                   logging.warning(f"Impossible d'obtenir l'état des prises pour {ip} (get_outlet_state a retourné None)")
                                   # Garder l'ancien état? Ou marquer comme inconnu? Pour l'instant, on saute.
                         else:
                              logging.warning(f"Impossible de connecter/rafraîchir {ip} pour lire l'état.")
                     except Exception as e:
                          logging.error(f"Erreur lors de la lecture de l'état de {ip}: {e}")
                 self.live_kasa_states = new_live_states # Remplacer l'état précédent
                 last_kasa_update_time = current_time
                 logging.debug(f"États Kasa mis à jour: {self.live_kasa_states}")


            # --- 3. Évaluer les règles ---
            tasks_to_run = [] # Collecter les tâches Kasa à exécuter

            # Copier self.rules pour éviter les problèmes si l'UI modifie la liste en cours d'itération
            rules_to_evaluate = list(self.rules)

            # Dictionnaire pour suivre l'action souhaitée pour chaque prise { (ip, index): 'ON' | 'OFF' | None }
            desired_outlet_states = {}

            for rule in rules_to_evaluate:
                rule_id = rule.get('id')
                if not all([rule.get('sensor_id'), rule.get('operator'), rule.get('threshold') is not None,
                            rule.get('target_device_ip'), rule.get('target_outlet_index') is not None, rule.get('action')]):
                    logging.debug(f"Règle {rule_id or 'Inconnue'} incomplète, ignorée.")
                    continue

                sensor_id = rule.get('sensor_id')
                operator = rule.get('operator')
                threshold = float(rule.get('threshold'))
                target_ip = rule.get('target_device_ip')
                target_index = int(rule.get('target_outlet_index'))
                primary_action = rule.get('action') # 'ON' ou 'OFF'
                outlet_key = (target_ip, target_index)

                # Vérifier si une condition "UNTIL" est active pour cette règle
                is_until_active = rule_id in active_until_rules

                # --- 3a. Évaluer la condition "UNTIL" si active ---
                revert_action_needed = False
                if is_until_active:
                    until_info = active_until_rules[rule_id]
                    until_end_time = until_info.get('end_time')
                    until_condition = rule.get('until_condition')

                    if until_end_time and current_time >= until_end_time:
                        # Timer expiré
                        revert_action_needed = True
                        logging.info(f"Règle {rule_id}: Condition 'UNTIL Timer' terminée.")
                    elif until_condition and until_condition.get('type') == 'Capteur':
                        # Évaluer la condition capteur de 'UNTIL'
                        until_sensor_id = until_condition.get('sensor_id')
                        until_operator = until_condition.get('operator')
                        until_threshold = float(until_condition.get('threshold'))

                        if until_sensor_id in valid_sensor_values:
                            current_until_value = valid_sensor_values[until_sensor_id]
                            if self._compare(current_until_value, until_operator, until_threshold):
                                revert_action_needed = True
                                logging.info(f"Règle {rule_id}: Condition 'UNTIL {until_sensor_id} {until_operator} {until_threshold}' ({current_until_value}) remplie.")
                        else:
                             logging.warning(f"Règle {rule_id}: Capteur 'UNTIL' {until_sensor_id} non disponible pour évaluation.")

                    if revert_action_needed:
                        desired_outlet_states[outlet_key] = until_info['revert_action']
                        del active_until_rules[rule_id] # Désactiver le 'UNTIL'
                        # Ne pas évaluer la condition principale SI pour ce cycle si UNTIL vient de se terminer
                        continue # Passer à la règle suivante

                # --- 3b. Évaluer la condition principale "SI" (si UNTIL n'est pas/plus actif ou n'a pas déclenché de revert) ---
                if sensor_id in valid_sensor_values:
                    current_value = valid_sensor_values[sensor_id]
                    condition_met = self._compare(current_value, operator, threshold)
                    logging.debug(f"Règle {rule_id}: Évalutation {sensor_id}({current_value}) {operator} {threshold} -> {condition_met}")

                    if condition_met:
                        # La condition principale est remplie
                        # Vérifier si une action est déjà en cours pour cette prise
                        if outlet_key not in desired_outlet_states: # Si aucune autre règle n'a déjà décidé
                             desired_outlet_states[outlet_key] = primary_action

                             # Vérifier si on doit activer une condition "UNTIL"
                             until_condition = rule.get('until_condition')
                             if until_condition and rule_id not in active_until_rules:
                                 until_type = until_condition.get('type')
                                 revert_action = 'OFF' if primary_action == 'ON' else 'ON'
                                 end_time = None
                                 if until_type == 'Timer (secondes)':
                                     duration = until_condition.get('duration')
                                     if duration is not None:
                                         end_time = current_time + timedelta(seconds=duration)
                                         logging.info(f"Règle {rule_id}: Activation 'UNTIL Timer' de {duration}s. Fin: {end_time.strftime('%H:%M:%S')}")
                                 elif until_type == 'Capteur':
                                     # Juste marquer comme actif, pas de timer
                                      logging.info(f"Règle {rule_id}: Activation 'UNTIL Capteur' ({until_condition.get('sensor_id')} {until_condition.get('operator')} {until_condition.get('threshold')}).")
                                      end_time = None # Marqueur pour condition capteur active

                                 active_until_rules[rule_id] = {
                                     'revert_action': revert_action,
                                     'end_time': end_time # Peut être None pour type Capteur
                                 }

                    # Si la condition principale n'est PAS remplie, on ne définit PAS d'état désiré ici.
                    # S'il n'y a pas d'autre règle qui active cette prise, elle devrait s'éteindre
                    # (ou rester éteinte) naturellement lors de la comparaison finale.

                else: # Capteur principal non disponible
                    logging.warning(f"Règle {rule_id}: Capteur principal {sensor_id} non disponible pour évaluation.")


            # --- 4. Appliquer les changements Kasa ---
            logging.debug(f"États Kasa désirés: {desired_outlet_states}")
            logging.debug(f"États Kasa actuels (live): {self.live_kasa_states}")

            for outlet_key, desired_state in desired_outlet_states.items():
                 target_ip, target_index = outlet_key
                 # Obtenir l'état actuel connu (depuis la dernière lecture)
                 current_state_known = self.live_kasa_states.get(target_ip, {}).get(target_index)
                 current_state_bool = current_state_known if current_state_known is not None else None # True, False ou None

                 action_needed = False
                 if desired_state == 'ON' and current_state_bool is not True: # Si désiré ON et pas déjà ON (ou inconnu)
                     action_needed = True
                     action_func = 'turn_outlet_on'
                 elif desired_state == 'OFF' and current_state_bool is not False: # Si désiré OFF et pas déjà OFF (ou inconnu)
                     action_needed = True
                     action_func = 'turn_outlet_off'

                 if action_needed:
                     if target_ip in self.kasa_devices:
                         controller = self.kasa_devices[target_ip]['controller']
                         logging.info(f"Action requise pour {target_ip} Prise {target_index}: {action_func}")
                         # Ajouter l'appel asynchrone à la liste des tâches
                         tasks_to_run.append(getattr(controller, action_func)(target_index))
                         # Mettre à jour immédiatement l'état 'live' supposé pour la prochaine itération rapide
                         # (sera corrigé par la lecture périodique si l'action échoue)
                         if target_ip not in self.live_kasa_states: self.live_kasa_states[target_ip] = {}
                         self.live_kasa_states[target_ip][target_index] = (desired_state == 'ON')

                     else:
                         logging.error(f"Impossible d'exécuter l'action pour {target_ip}: appareil non trouvé dans les contrôleurs.")

            # --- Gérer les prises qui n'ont PAS d'état désiré défini ---
            # Elles devraient être éteintes, sauf si elles sont déjà éteintes.
            all_managed_outlets = set(k for rule in rules_to_evaluate for k in [(rule.get('target_device_ip'), rule.get('target_outlet_index'))] if rule.get('target_device_ip') and rule.get('target_outlet_index') is not None)

            for ip, outlets in self.live_kasa_states.items():
                 for index, is_on in outlets.items():
                     outlet_key = (ip, index)
                     # Si cette prise est gérée par au moins une règle ET qu'aucune règle ne veut l'allumer ET qu'elle est actuellement ON
                     if outlet_key in all_managed_outlets and outlet_key not in desired_outlet_states and is_on:
                         logging.info(f"Aucune règle n'active {ip} Prise {index}, mais elle est ON. Action requise: turn_outlet_off")
                         if ip in self.kasa_devices:
                             controller = self.kasa_devices[ip]['controller']
                             tasks_to_run.append(controller.turn_outlet_off(index))
                             # Mettre à jour l'état live supposé
                             self.live_kasa_states[ip][index] = False
                         else:
                            logging.error(f"Impossible d'éteindre {ip} Prise {index}: appareil non trouvé.")


            # --- 5. Exécuter les tâches Kasa collectées ---
            if tasks_to_run:
                logging.debug(f"Exécution de {len(tasks_to_run)} tâche(s) Kasa...")
                results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        # Essayer de déterminer quelle tâche a échoué (besoin de plus d'infos)
                        logging.error(f"Erreur lors de l'exécution d'une tâche Kasa: {result}")
                logging.debug("Tâches Kasa terminées.")


            # --- 6. Attendre avant le prochain cycle ---
            await asyncio.sleep(2) # Intervalle de la boucle principale (ajuster si nécessaire)

    def _compare(self, value1, operator, value2):
        """Effectue une comparaison basée sur l'opérateur."""
        try:
            v1 = float(value1)
            v2 = float(value2)
            if operator == '<': return v1 < v2
            if operator == '>': return v1 > v2
            if operator == '=': return v1 == v2
            if operator == '!=': return v1 != v2
            if operator == '<=': return v1 <= v2
            if operator == '>=': return v1 >= v2
        except (ValueError, TypeError) as e:
             logging.error(f"Erreur de comparaison: {value1} {operator} {value2} - {e}")
             return False # Ne pas déclencher sur erreur
        return False

    def _turn_off_all_kasa_safely(self):
        """Tente d'éteindre toutes les prises Kasa connues (pour arrêt/fermeture)."""
         # Exécuter dans la boucle asyncio existante si possible, ou une nouvelle temporaire
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                 asyncio.run_coroutine_threadsafe(self._async_turn_off_all(), loop)
            else:
                 loop.run_until_complete(self._async_turn_off_all())
        except RuntimeError: # Pas de boucle définie pour ce thread
             loop = asyncio.new_event_loop()
             asyncio.set_event_loop(loop)
             loop.run_until_complete(self._async_turn_off_all())
             # loop.close() # Fermer la boucle temporaire

    async def _async_turn_off_all(self):
        """Tâche asynchrone pour éteindre toutes les prises."""
        tasks = []
        for ip, data in self.kasa_devices.items():
            controller = data['controller']
            # Vérifier si le contrôleur est pour une prise/barre
            if data['info'].get('is_strip') or data['info'].get('is_plug'):
                 logging.info(f"Extinction de sécurité: {ip}")
                 tasks.append(controller.turn_all_outlets_off()) # Utilise la méthode de votre contrôleur

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                 if isinstance(result, Exception):
                      logging.error(f"Erreur lors de l'extinction de sécurité d'un appareil: {result}")
        logging.info("Extinction de sécurité terminée.")


    def save_configuration(self):
        """Récupère les règles de l'UI et sauvegarde la configuration."""
        # S'assurer que les dernières modifications UI sont dans self.rules
        logging.info("Préparation de la sauvegarde : mise à jour des données des règles depuis l'UI...")
        for rule_id in self.rule_widgets.keys():
             try:
                 self.on_rule_change(rule_id) # Force la mise à jour des données internes depuis l'UI
             except Exception as e:
                 logging.error(f"Erreur pendant on_rule_change pour {rule_id} lors de la sauvegarde: {e}")
                 # On continue quand même pour essayer de sauvegarder le reste

        config_to_save = {
            "aliases": self.aliases, # Utiliser les alias potentiellement mis à jour
            "rules": self.rules
        }

        # --- AJOUT DE DEBUG ---
        logging.debug(f"Données prêtes pour la sauvegarde : {config_to_save}")
        # --- FIN DE L'AJOUT ---

        if save_config(config_to_save, DEFAULT_CONFIG_FILE):
            messagebox.showinfo("Sauvegarde", "Configuration sauvegardée avec succès.", parent=self.root)
        else:
            messagebox.showerror("Sauvegarde", "Erreur lors de la sauvegarde de la configuration.", parent=self.root)
    def on_closing(self):
        """Gère la fermeture de l'application."""
        if self.monitoring_active:
             if messagebox.askyesno("Quitter", "Le monitoring est actif. Voulez-vous l'arrêter et quitter ?", parent=self.root):
                 self.stop_monitoring()
                 # Attendre un peu que l'arrêt se fasse ?
                 self.root.after(500, self.root.destroy)
             else:
                 return # Ne pas quitter
        else:
            if messagebox.askyesno("Quitter", "Êtes-vous sûr de vouloir quitter ?", parent=self.root):
                 self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = GreenhouseApp(root)
    root.mainloop()