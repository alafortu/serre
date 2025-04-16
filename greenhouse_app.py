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
        try:
            # Définir une taille initiale (Largeur x Hauteur)
            # Augmenter la largeur (par exemple à 1300) pour tout voir
            self.root.geometry("1300x800")
        except tk.TclError as e:
             # Peut échouer sur certains systèmes/configurations Tcl/Tk, logguer l'erreur
             logging.warning(f"Impossible de définir la géométrie initiale: {e}")

        # --- Initialisation Backend ---
        self.log_queue = queue.Queue()
        setup_logging(self.log_queue)

        self.config = load_config(DEFAULT_CONFIG_FILE)
        # Structure Alias: utilise MAC pour devices/outlets
        self.aliases = self.config.get('aliases', {
            "sensors": {},
            "devices": {}, # {mac: alias}
            "outlets": {}  # {mac: {index: alias}}
        })
        self.rules = self.config.get('rules', []) # Les règles contiendront 'target_device_mac'

        # Gestionnaires de Périphériques
        # Utilise MAC comme clé. Stocke l'IP pour la communication.
        self.kasa_devices = {} # dict: {mac: {'info': dict, 'controller': DeviceController, 'ip': str}}
        self.temp_manager = TempSensorManager()
        self.light_manager = BH1750Manager() # Utilise les adresses par défaut [0x23, 0x5C]

        # Listes pour les dropdowns (basées sur MAC)
        self.available_sensors = [] # Liste de tuples (display_name, internal_id)
        self.available_kasa_strips = [] # Liste de tuples (display_name, mac)
        self.available_outlets = {} # Dict: {mac: [(display_name, index), ...]}

        # État de l'application
        self.monitoring_active = False
        self.monitoring_thread = None
        self.asyncio_loop = None
        self.ui_update_job = None
        self.live_kasa_states = {} # Dict: {mac: {index: bool}} - État live des prises Kasa

        # --- Interface Utilisateur ---
        self.create_widgets()
        self.populate_initial_ui_data()
        self.update_log_display()

        # --- Démarrage Découverte ---
        self.discover_all_devices()

        # --- Gestion Fermeture ---
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def get_alias(self, item_type, item_id, sub_id=None):
        """Récupère un alias ou retourne l'ID/MAC si non trouvé."""
        # item_id est l'ID du capteur ou la MAC pour device/outlet
        try:
            if item_type == 'sensor':
                return self.aliases.get('sensors', {}).get(str(item_id), str(item_id))
            elif item_type == 'device':
                # item_id est la MAC
                return self.aliases.get('devices', {}).get(str(item_id), str(item_id))
            elif item_type == 'outlet':
                # item_id est la MAC, sub_id est l'index de la prise
                device_outlets = self.aliases.get('outlets', {}).get(str(item_id), {})
                # Essayer de récupérer l'alias de la prise découverte si pas d'alias perso
                fallback_name = f"Prise {sub_id}"
                if str(item_id) in self.kasa_devices:
                    outlet_info = next((o for o in self.kasa_devices[str(item_id)].get('info',{}).get('outlets',[]) if o.get('index') == sub_id), None)
                    if outlet_info:
                        fallback_name = outlet_info.get('alias', fallback_name)
                return device_outlets.get(str(sub_id), fallback_name)
        except KeyError:
             # Gérer le cas où la structure d'alias n'est pas complète
             logging.warning(f"Clé manquante dans get_alias pour {item_type} {item_id} {sub_id}")
             pass # Continue pour retourner le fallback

        # Fallback final
        if sub_id is not None:
             # Essayer de trouver un nom par défaut basé sur l'info découverte
             if item_type == 'outlet' and str(item_id) in self.kasa_devices:
                 outlet_info = next((o for o in self.kasa_devices[str(item_id)].get('info',{}).get('outlets',[]) if o.get('index') == sub_id), None)
                 if outlet_info:
                     return outlet_info.get('alias', f"Prise {sub_id}")
             return f"{item_id} - Prise {sub_id}" # Retourne MAC et Index si rien trouvé
        return str(item_id) # Retourne ID capteur ou MAC appareil


    def update_alias(self, item_type, item_id, new_alias, sub_id=None):
        """Met à jour un alias dans la structure et prépare la sauvegarde. Utilise MAC pour devices/outlets."""
        # item_id est l'ID du capteur ou la MAC pour device/outlet
        if 'aliases' not in self.config: self.config['aliases'] = {"sensors": {}, "devices": {}, "outlets": {}}
        if item_type not in self.config['aliases']: self.config['aliases'][item_type] = {}

        if item_type == 'outlet':
            # item_id est la MAC, sub_id est l'index
            if 'outlets' not in self.config['aliases']: self.config['aliases']['outlets'] = {}
            if str(item_id) not in self.config['aliases']['outlets']: self.config['aliases']['outlets'][str(item_id)] = {}
            self.config['aliases']['outlets'][str(item_id)][str(sub_id)] = new_alias
        elif item_type == 'device':
             # item_id est la MAC
             if 'devices' not in self.config['aliases']: self.config['aliases']['devices'] = {}
             self.config['aliases']['devices'][str(item_id)] = new_alias
        elif item_type == 'sensor':
            # item_id est l'ID du capteur
            if 'sensors' not in self.config['aliases']: self.config['aliases']['sensors'] = {}
            self.config['aliases']['sensors'][str(item_id)] = new_alias
        else:
            logging.error(f"Type d'item inconnu pour l'alias: {item_type}")
            return

        self.aliases = self.config['aliases'] # Mettre à jour la copie locale utilisée par get_alias
        logging.info(f"Alias mis à jour pour {item_type} {item_id}" + (f"[{sub_id}]" if sub_id is not None else "") + f": '{new_alias}'")
        # self.save_configuration() # Optionnel: sauvegarder immédiatement

    def edit_alias_dialog(self, item_type, item_id, current_name, sub_id=None):
        """Ouvre une popup pour éditer un alias. item_id est la MAC pour device/outlet."""
        prompt = f"Entrez un nouveau nom pour {item_type} '{current_name}'"
        if item_type == 'outlet':
            # item_id est la MAC de la barre
            device_name = self.get_alias('device', item_id)
            prompt = f"Entrez un nouveau nom pour la prise '{current_name}' (Barre: {device_name})"
        elif item_type == 'device':
             # item_id est la MAC de la barre
             prompt = f"Entrez un nouveau nom pour l'appareil '{current_name}' (MAC: {item_id})"

        new_name = simpledialog.askstring("Modifier Alias", prompt, initialvalue=current_name, parent=self.root)

        if new_name and new_name != current_name:
            self.update_alias(item_type, item_id, new_name, sub_id)
            # Rafraîchir l'UI où cet alias est utilisé
            self.refresh_device_lists() # Met à jour les listes internes (available_xxx)
            self.repopulate_all_rule_dropdowns() # Met à jour les dropdowns dans les règles
            self.update_status_display() # Met à jour les labels dans la section statut
            self.root.update_idletasks() # Forcer Tkinter à traiter les changements

    # --- Création Widgets (Pas de changement majeur ici, juste l'affichage) ---
    def create_widgets(self):
        # ... (le reste de la création des widgets reste similaire) ...
        # Les changements sont dans la *population* et la *gestion* des données
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
        # Les clés seront les ID de capteur ou les MAC pour appareils/prises(composites)
        self.status_labels = {} # {id: {'label_name': tk.Label, 'label_value': tk.Label, 'button_edit': tk.Button}}

        # --- Section Logs ---
        log_frame = ttk.LabelFrame(status_log_frame, text="Journal d'Événements", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True, side=tk.RIGHT, padx=5)

        self.log_display = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, state=tk.DISABLED, height=15)
        self.log_display.pack(fill=tk.BOTH, expand=True)


    # --- Peuplement Initial ---
    def populate_initial_ui_data(self):
        """Remplit l'UI avec les règles chargées depuis la config."""
        # Les règles sont chargées dans self.rules à l'init
        # La découverte doit se faire AVANT de pouvoir peupler correctement
        # les dropdowns des règles existantes.
        # Cette fonction ajoutera les frames, mais le peuplement
        # fin se fera après la découverte via refresh_device_lists -> repopulate_all_rule_dropdowns
        for rule_data in self.rules:
             self.add_rule_ui(rule_data=rule_data)
        # Le rafraîchissement (refresh_device_lists) appelé après la découverte
        # s'occupera de peupler les dropdowns correctement.


    # --- Gestion Règles UI ---
    # --- Gestion Règles UI ---
    def add_rule_ui(self, rule_data=None):
        """Ajoute une ligne de règle à l'interface utilisateur."""
        rule_id = rule_data.get('id', str(uuid.uuid4())) if rule_data else str(uuid.uuid4())
        if not rule_data: # Nouvelle règle
            rule_data = {'id': rule_id}
            self.rules.append(rule_data)
        elif not any(r.get('id') == rule_id for r in self.rules):
             # Règle chargée, s'assurer qu'elle est dans la liste
            self.rules.append(rule_data) # Normalement déjà fait au load_config

        rule_frame = ttk.Frame(self.scrollable_rules_frame, padding="5", borderwidth=1, relief="groove")
        rule_frame.pack(fill=tk.X, pady=2, padx=2)

        widgets = {}

        # --- Condition "SI" ---
        ttk.Label(rule_frame, text="SI").pack(side=tk.LEFT, padx=2)
        widgets['sensor_var'] = tk.StringVar()
        widgets['sensor_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['sensor_var'], width=20, state="readonly")
        # +++ Peupler les valeurs initiales pour les nouvelles règles +++
        widgets['sensor_combo']['values'] = [name for name, _id in self.available_sensors]
        # +++ Fin Peuplement +++
        widgets['sensor_combo'].pack(side=tk.LEFT, padx=2)
        widgets['sensor_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['operator_var'] = tk.StringVar()
        widgets['operator_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['operator_var'], values=OPERATORS, width=4, state="readonly")
        widgets['operator_combo'].pack(side=tk.LEFT, padx=2)
        widgets['operator_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['value_var'] = tk.StringVar()
        widgets['value_entry'] = ttk.Entry(rule_frame, textvariable=widgets['value_var'], width=8) # Largeur augmentée pour décimales
        widgets['value_entry'].pack(side=tk.LEFT, padx=2)
        widgets['value_entry'].bind('<KeyRelease>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # --- Action "ALORS" ---
        ttk.Label(rule_frame, text="ALORS").pack(side=tk.LEFT, padx=(10, 2))
        widgets['kasa_var'] = tk.StringVar()
        widgets['kasa_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['kasa_var'], width=25, state="readonly") # Largeur ajustée
        # +++ Peupler les valeurs initiales pour les nouvelles règles +++
        widgets['kasa_combo']['values'] = [name for name, _mac in self.available_kasa_strips]
         # +++ Fin Peuplement +++
        widgets['kasa_combo'].pack(side=tk.LEFT, padx=2)
        widgets['kasa_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.update_outlet_options(rid))

        widgets['outlet_var'] = tk.StringVar()
        widgets['outlet_combo'] = ttk.Combobox(rule_frame, textvariable=widgets['outlet_var'], width=20, state="readonly") # Largeur ajustée
        # Les valeurs des prises sont définies par update_outlet_options APRES sélection Kasa
        widgets['outlet_combo']['values'] = [] # Initialement vide pour une nouvelle règle
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

        # Champs pour 'Timer'
        widgets['until_timer_frame'] = ttk.Frame(rule_frame)
        widgets['until_timer_value_var'] = tk.StringVar()
        widgets['until_timer_value_entry'] = ttk.Entry(widgets['until_timer_frame'], textvariable=widgets['until_timer_value_var'], width=6)
        widgets['until_timer_value_entry'].pack(side=tk.LEFT)
        widgets['until_timer_value_entry'].bind('<KeyRelease>', lambda e, rid=rule_id: self.on_rule_change(rid))
        ttk.Label(widgets['until_timer_frame'], text="secs").pack(side=tk.LEFT, padx=1)

        # Champs pour 'Capteur'
        widgets['until_sensor_frame'] = ttk.Frame(rule_frame)
        widgets['until_sensor_var'] = tk.StringVar()
        widgets['until_sensor_combo'] = ttk.Combobox(widgets['until_sensor_frame'], textvariable=widgets['until_sensor_var'], width=20, state="readonly")
        # +++ Peupler les valeurs initiales pour les nouvelles règles +++
        widgets['until_sensor_combo']['values'] = [name for name, _id in self.available_sensors]
        # +++ Fin Peuplement +++
        widgets['until_sensor_combo'].pack(side=tk.LEFT, padx=2)
        widgets['until_sensor_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['until_operator_var'] = tk.StringVar()
        widgets['until_operator_combo'] = ttk.Combobox(widgets['until_sensor_frame'], textvariable=widgets['until_operator_var'], values=OPERATORS, width=4, state="readonly")
        widgets['until_operator_combo'].pack(side=tk.LEFT, padx=2)
        widgets['until_operator_combo'].bind('<<ComboboxSelected>>', lambda e, rid=rule_id: self.on_rule_change(rid))

        widgets['until_sensor_value_var'] = tk.StringVar()
        widgets['until_sensor_value_entry'] = ttk.Entry(widgets['until_sensor_frame'], textvariable=widgets['until_sensor_value_var'], width=8) # Largeur augmentée
        widgets['until_sensor_value_entry'].pack(side=tk.LEFT, padx=2)
        widgets['until_sensor_value_entry'].bind('<KeyRelease>', lambda e, rid=rule_id: self.on_rule_change(rid))

        # Empaqueter les frames 'until' (mais ne pas les afficher encore)
        widgets['until_timer_frame'].pack(side=tk.LEFT, padx=2)
        widgets['until_sensor_frame'].pack(side=tk.LEFT, padx=2)
        widgets['until_timer_frame'].pack_forget()
        widgets['until_sensor_frame'].pack_forget()

        # Bouton Supprimer
        delete_button = ttk.Button(rule_frame, text="🗑️", width=3, command=lambda rid=rule_id: self.delete_rule(rid))
        delete_button.pack(side=tk.RIGHT, padx=5)

        # Stocker les widgets
        self.rule_widgets[rule_id] = {'frame': rule_frame, 'widgets': widgets}

        # Peupler les widgets si des données existent (chargement)
        if rule_data and rule_id in self.rule_widgets:
            self._populate_rule_ui_from_data(rule_id, rule_data)
            # Le peuplement fin des dropdowns se fera via repopulate_all_rule_dropdowns après découverte

        # Mettre à jour la barre de défilement
        # Peut être nécessaire de faire un update_idletasks avant pour que bbox soit correct
        self.scrollable_rules_frame.update_idletasks()
        self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))

        # Si c'est une nouvelle règle, s'assurer que les champs 'until' sont bien cachés par défaut
        if not rule_data:
             widgets['until_type_var'].set('Aucun') # Mettre la valeur par défaut
             self.toggle_until_fields(rule_id) # Appeler pour cacher les champs

    def _populate_rule_ui_from_data(self, rule_id, rule_data):
        """Remplit les widgets d'une règle avec les données chargées (pré-découverte)."""
        if rule_id not in self.rule_widgets: return
        widgets = self.rule_widgets[rule_id]['widgets']

        # SI
        sensor_id = rule_data.get('sensor_id')
        if sensor_id:
            # Afficher l'alias s'il existe, sinon l'ID
            widgets['sensor_var'].set(self.get_alias('sensor', sensor_id))
        widgets['operator_var'].set(rule_data.get('operator', ''))
        widgets['value_var'].set(str(rule_data.get('threshold', ''))) # Convertir en str pour l'Entry

        # ALORS
        kasa_mac = rule_data.get('target_device_mac')
        outlet_index = rule_data.get('target_outlet_index') # Peut être None ou int
        if kasa_mac:
            # Afficher l'alias si possible, sinon la MAC. Sera corrigé par repopulate.
             widgets['kasa_var'].set(self.get_alias('device', kasa_mac))
             # On ne peut pas encore définir les options de prise ni la sélection finale
             # car self.available_outlets n'est pas peuplé.
             # On stocke temporairement l'index désiré pour repopulate
             self.rule_widgets[rule_id]['desired_outlet_index'] = outlet_index
             # Mettre une valeur temporaire ou vide pour la prise
             widgets['outlet_var'].set(f"Prise {outlet_index}" if outlet_index is not None else "")


        widgets['action_var'].set(rule_data.get('action', ''))

        # JUSQU'À
        until_data = rule_data.get('until_condition', {})
        until_type = until_data.get('type', 'Aucun')
        widgets['until_type_var'].set(until_type)

        if until_type == 'Timer (secondes)':
            widgets['until_timer_value_var'].set(str(until_data.get('duration', '')))
        elif until_type == 'Capteur':
            until_sensor_id = until_data.get('sensor_id')
            if until_sensor_id:
                widgets['until_sensor_var'].set(self.get_alias('sensor', until_sensor_id))
            widgets['until_operator_var'].set(until_data.get('operator', ''))
            widgets['until_sensor_value_var'].set(str(until_data.get('threshold', '')))

        # Afficher/Cacher les champs JUSQU'À (toggle_until_fields sera appelé par repopulate)
        # self.toggle_until_fields(rule_id) # Pas maintenant

    def delete_rule(self, rule_id):
        """Supprime une règle de l'UI et de la liste interne."""
        if rule_id in self.rule_widgets:
            self.rule_widgets[rule_id]['frame'].destroy()
            del self.rule_widgets[rule_id]
            # Supprimer de self.rules par ID
            initial_len = len(self.rules)
            self.rules = [rule for rule in self.rules if rule.get('id') != rule_id]
            if len(self.rules) < initial_len:
                 logging.info(f"Règle {rule_id} supprimée de la liste interne.")
            else:
                 logging.warning(f"Règle {rule_id} non trouvée dans self.rules lors de la suppression.")

            # Mettre à jour la barre de défilement
            self.rules_canvas.update_idletasks()
            self.rules_canvas.configure(scrollregion=self.rules_canvas.bbox("all"))


    def update_outlet_options(self, rule_id, preselect_outlet_index=None):
        """Met à jour les options de prise basées sur la barre Kasa (MAC) sélectionnée."""
        if rule_id not in self.rule_widgets: return

        widgets = self.rule_widgets[rule_id]['widgets']
        selected_kasa_name = widgets['kasa_var'].get() # C'est l'alias affiché

        # Trouver la MAC correspondant à l'alias sélectionné
        selected_mac = None
        for name, mac in self.available_kasa_strips:
            if name == selected_kasa_name:
                selected_mac = mac
                break

        outlet_options = []
        current_outlet_alias = "" # Pour la présélection

        if selected_mac and selected_mac in self.available_outlets:
             # Les options sont les alias des prises
            outlet_options = [name for name, _index in self.available_outlets[selected_mac]]
            if preselect_outlet_index is not None:
                # Trouver l'alias correspondant à l'index de présélection
                for name, index in self.available_outlets[selected_mac]:
                    if index == preselect_outlet_index:
                        current_outlet_alias = name
                        break
                # Si on n'a pas trouvé l'alias mais l'index est valide, on prend le premier nom trouvé pour cet index
                if not current_outlet_alias:
                     matching_outlets = [name for name, index in self.available_outlets[selected_mac] if index == preselect_outlet_index]
                     if matching_outlets:
                         current_outlet_alias = matching_outlets[0]


        widgets['outlet_combo']['values'] = outlet_options
        if current_outlet_alias:
            widgets['outlet_var'].set(current_outlet_alias)
        elif outlet_options: # S'il y a des options mais pas de présélection, choisir la première
            widgets['outlet_var'].set(outlet_options[0])
        else:
            widgets['outlet_var'].set('') # Vider si aucune option

        # Très important: mettre à jour la règle interne APRÈS avoir changé la barre Kasa
        # Car cela affecte l'outlet sélectionnable et donc potentiellement l'index stocké.
        self.on_rule_change(rule_id)


    def toggle_until_fields(self, rule_id):
        """Affiche ou cache les champs 'Jusqu'à'."""
        if rule_id not in self.rule_widgets: return

        widgets = self.rule_widgets[rule_id]['widgets']
        until_type = widgets['until_type_var'].get()

        widgets['until_timer_frame'].pack_forget()
        widgets['until_sensor_frame'].pack_forget()

        if until_type == 'Timer (secondes)':
            widgets['until_timer_frame'].pack(side=tk.LEFT, padx=2)
        elif until_type == 'Capteur':
            widgets['until_sensor_frame'].pack(side=tk.LEFT, padx=2)

        # Mise à jour de la règle interne après changement
        self.on_rule_change(rule_id)


    def on_rule_change(self, rule_id):
        """Met à jour la structure de données de la règle (self.rules) lorsque l'UI change."""
        if rule_id not in self.rule_widgets:
            logging.warning(f"Tentative de mise à jour de la règle {rule_id} non trouvée dans rule_widgets.")
            return
        rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)
        if not rule_data:
            logging.error(f"Règle {rule_id} trouvée dans rule_widgets mais pas dans self.rules!")
            # Peut-être créer une nouvelle entrée dans self.rules ? Pour l'instant, on log et on sort.
            # rule_data = {'id': rule_id}
            # self.rules.append(rule_data)
            return

        widgets = self.rule_widgets[rule_id]['widgets']

        # Trouver les IDs/MACs internes à partir des noms affichés (alias)
        sensor_name = widgets['sensor_var'].get()
        kasa_name = widgets['kasa_var'].get()
        outlet_name = widgets['outlet_var'].get()
        until_sensor_name = widgets['until_sensor_var'].get()

        sensor_id = next((sid for name, sid in self.available_sensors if name == sensor_name), None)
        kasa_mac = next((kmac for name, kmac in self.available_kasa_strips if name == kasa_name), None)

        outlet_index = None
        if kasa_mac and kasa_mac in self.available_outlets:
             # Trouver l'index basé sur l'alias de prise sélectionné pour cette MAC
            outlet_index = next((idx for name, idx in self.available_outlets[kasa_mac] if name == outlet_name), None)

        until_sensor_id = next((sid for name, sid in self.available_sensors if name == until_sensor_name), None)

        # --- Mise à jour de rule_data ---
        rule_data['sensor_id'] = sensor_id
        rule_data['operator'] = widgets['operator_var'].get()
        try:
            # **MODIFICATION POUR FLOAT**
            value_str = widgets['value_var'].get().replace(',', '.') # Accepter virgule ou point
            rule_data['threshold'] = float(value_str) if value_str else None
        except ValueError:
            rule_data['threshold'] = None
            logging.warning(f"Règle {rule_id}: Valeur de seuil invalide '{widgets['value_var'].get()}'. Mise à None.")

        rule_data['target_device_mac'] = kasa_mac # Utiliser MAC
        rule_data['target_outlet_index'] = outlet_index
        rule_data['action'] = widgets['action_var'].get()
        # Supprimer l'ancienne clé IP si elle existe
        rule_data.pop('target_device_ip', None)

        # --- Mise à jour de 'until_condition' ---
        until_type = widgets['until_type_var'].get()
        if until_type == 'Aucun':
            rule_data.pop('until_condition', None) # Supprimer la condition si elle existe
        else:
            if 'until_condition' not in rule_data:
                rule_data['until_condition'] = {}

            rule_data['until_condition']['type'] = until_type

            if until_type == 'Timer (secondes)':
                try:
                    duration_str = widgets['until_timer_value_var'].get()
                    rule_data['until_condition']['duration'] = int(duration_str) if duration_str else None
                except ValueError:
                    rule_data['until_condition']['duration'] = None
                # Nettoyer les clés non pertinentes
                rule_data['until_condition'].pop('sensor_id', None)
                rule_data['until_condition'].pop('operator', None)
                rule_data['until_condition'].pop('threshold', None)

            elif until_type == 'Capteur':
                rule_data['until_condition']['sensor_id'] = until_sensor_id
                rule_data['until_condition']['operator'] = widgets['until_operator_var'].get()
                try:
                     # **MODIFICATION POUR FLOAT**
                    until_value_str = widgets['until_sensor_value_var'].get().replace(',', '.')
                    rule_data['until_condition']['threshold'] = float(until_value_str) if until_value_str else None
                except ValueError:
                     rule_data['until_condition']['threshold'] = None
                     logging.warning(f"Règle {rule_id}: Valeur de seuil 'until' invalide '{widgets['until_sensor_value_var'].get()}'. Mise à None.")
                 # Nettoyer les clés non pertinentes
                rule_data['until_condition'].pop('duration', None)

        # logging.debug(f"Données de la règle {rule_id} mises à jour: {rule_data}") # Décommenter pour debug


    def repopulate_all_rule_dropdowns(self):
        """Met à jour toutes les listes déroulantes dans toutes les règles (après découverte/refresh)."""
        logging.debug("Repopulation de toutes les listes déroulantes des règles.")
        sensor_names = [name for name, _id in self.available_sensors]
        # Utilise la liste basée sur MAC
        kasa_names = [name for name, _mac in self.available_kasa_strips]

        for rule_id, data in self.rule_widgets.items():
            widgets = data['widgets']
            rule_data = next((rule for rule in self.rules if rule.get('id') == rule_id), None)

            # --- Capteur Principal ---
            current_sensor_id = rule_data.get('sensor_id') if rule_data else None
            current_sensor_name = self.get_alias('sensor', current_sensor_id) if current_sensor_id else ""
            widgets['sensor_combo']['values'] = sensor_names
            if current_sensor_name in sensor_names:
                widgets['sensor_var'].set(current_sensor_name)
            else:
                widgets['sensor_var'].set('')

            # --- Barre Kasa ---
            current_kasa_mac = rule_data.get('target_device_mac') if rule_data else None
            current_kasa_name = self.get_alias('device', current_kasa_mac) if current_kasa_mac else ""
            widgets['kasa_combo']['values'] = kasa_names
            desired_outlet_index = data.get('desired_outlet_index') # Récupérer l'index sauvegardé pendant _populate

            if current_kasa_name in kasa_names:
                 widgets['kasa_var'].set(current_kasa_name)
                 # Important: Rafraîchir les options de prises pour cette barre Kasa et préselectionner
                 self.update_outlet_options(rule_id, preselect_outlet_index=desired_outlet_index)
            else:
                 widgets['kasa_var'].set('')
                 widgets['outlet_combo']['values'] = [] # Vider les options de prise
                 widgets['outlet_var'].set('')

            # --- Capteur 'Until' ---
            until_sensor_id = rule_data.get('until_condition', {}).get('sensor_id') if rule_data else None
            current_until_sensor_name = self.get_alias('sensor', until_sensor_id) if until_sensor_id else ""
            widgets['until_sensor_combo']['values'] = sensor_names
            if current_until_sensor_name in sensor_names:
                widgets['until_sensor_var'].set(current_until_sensor_name)
            else:
                 widgets['until_sensor_var'].set('')

            # S'assurer que les champs 'until' sont correctement affichés/cachés
            self.toggle_until_fields(rule_id) # Appeler ici après avoir défini les valeurs


    # --- Découverte et Rafraîchissement ---
    def discover_all_devices(self):
        """Lance la découverte des capteurs et appareils Kasa."""
        logging.info("Démarrage de la découverte des périphériques...")
        # --- Découverte Capteurs ---
        try:
            self.temp_manager.discover_sensors()
        except Exception as e:
            logging.error(f"Erreur pendant la découverte des capteurs de température: {e}")
        try:
            self.light_manager.scan_sensors()
        except Exception as e:
            logging.error(f"Erreur pendant la découverte des capteurs de lumière: {e}")

        # --- Découverte Kasa (Asynchrone) ---
        threading.Thread(target=self._run_kasa_discovery_async, daemon=True).start()

    def _run_kasa_discovery_async(self):
        """Exécute la découverte Kasa dans la boucle asyncio."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        loop.run_until_complete(self._async_discover_kasa())

    async def _async_discover_kasa(self):
        """Tâche asynchrone pour découvrir les appareils Kasa et les stocker par MAC."""
        discoverer = DeviceDiscoverer()
        discovered_kasa = await discoverer.discover() # Retourne liste de dicts avec 'ip', 'mac', 'alias', etc.

        new_kasa_devices = {} # Sera {mac: {'info':..., 'controller':..., 'ip':...}}
        tasks_turn_off = [] # Collecter les tâches d'extinction

        for device_info in discovered_kasa:
             ip = device_info.get('ip')
             mac = device_info.get('mac') # Clé primaire maintenant

             if not ip or not mac:
                 logging.warning(f"Appareil Kasa découvert sans IP ou MAC: {device_info.get('alias', 'N/A')}. Ignoré.")
                 continue

             # Créer le contrôleur avec l'IP
             controller = DeviceController(
                 ip, # Le contrôleur a besoin de l'IP pour communiquer
                 is_strip=device_info.get('is_strip'),
                 is_plug=device_info.get('is_plug')
             )

             new_kasa_devices[mac] = {
                 'info': device_info,      # Garder toutes les infos découvertes
                 'controller': controller,
                 'ip': ip                  # Stocker l'IP ici pour la retrouver via la MAC
             }

             # Planifier l'extinction si le monitoring n'est pas actif
             if not self.monitoring_active:
                 if device_info.get('is_strip') or device_info.get('is_plug'):
                     logging.info(f"Découverte: Planification de l'extinction des prises de {device_info.get('alias')} ({mac}) @ {ip}")
                     # Ajouter la coroutine à exécuter plus tard avec gather
                     tasks_turn_off.append(controller.turn_all_outlets_off())


        # Exécuter toutes les extinctions en parallèle si nécessaire
        if tasks_turn_off:
             logging.info(f"Exécution de {len(tasks_turn_off)} tâches d'extinction initiale...")
             results = await asyncio.gather(*tasks_turn_off, return_exceptions=True)
             for i, result in enumerate(results):
                 if isinstance(result, Exception):
                     # Essayer de retrouver l'appareil correspondant (plus complexe)
                     logging.error(f"Erreur lors de l'extinction initiale d'un appareil Kasa: {result}")
             logging.info("Tâches d'extinction initiale terminées.")


        self.kasa_devices = new_kasa_devices # Remplacer l'ancien dict
        logging.info(f"Découverte Kasa terminée. {len(self.kasa_devices)} appareil(s) trouvé(s) et stocké(s) par MAC.")

        # Planifier la mise à jour de l'UI dans le thread principal Tkinter
        self.root.after(100, self.refresh_device_lists)


    def refresh_device_lists(self):
        """Met à jour les listes internes (available_*) et l'UI après découverte."""
        logging.info("Rafraîchissement des listes de périphériques (basé sur MAC).")
        # --- Capteurs ---
        temp_ids = self.temp_manager.get_sensor_ids()
        light_ids_int = self.light_manager.get_active_sensors()
        light_ids_hex = [hex(addr) for addr in light_ids_int]

        self.available_sensors = []
        for tid in temp_ids:
             # Utilise l'alias de config ou l'ID
            self.available_sensors.append((self.get_alias('sensor', tid), tid))
        for addr_hex in light_ids_hex:
             # Utilise l'alias de config ou l'ID hex
            self.available_sensors.append((self.get_alias('sensor', addr_hex), addr_hex))

        # --- Appareils Kasa et Prises (basé sur MAC) ---
        self.available_kasa_strips = [] # (display_name, mac)
        self.available_outlets = {}     # {mac: [(display_name, index), ...]}

        # Trier les appareils par alias pour un affichage cohérent
        sorted_macs = sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m))

        #for mac, data in self.kasa_devices.items():
        for mac in sorted_macs:
            data = self.kasa_devices[mac]
            device_info = data['info']
            device_alias = self.get_alias('device', mac) # Récupère l'alias (perso ou découverte) ou la MAC

            # Filtrer pour n'ajouter que les prises/barres aux listes de règles ? Ou tout ?
            # Pour l'instant, on ajoute tout ce qui a un contrôleur, on verra si besoin de filtrer.
            self.available_kasa_strips.append((device_alias, mac))

            outlets = []
            if device_info.get('is_strip') or device_info.get('is_plug'):
                 # Utiliser les infos de prise de la découverte initiale
                 discovered_outlets = device_info.get('outlets', [])
                 for outlet_data in discovered_outlets:
                     index = outlet_data.get('index')
                     if index is not None:
                          # get_alias gère maintenant la recherche d'alias perso puis fallback sur alias découverte
                         outlet_alias = self.get_alias('outlet', mac, sub_id=index)
                         outlets.append((outlet_alias, index))

            self.available_outlets[mac] = sorted(outlets, key=lambda x: x[1]) # Trier par index

        # --- Mettre à jour l'UI ---
        self.repopulate_all_rule_dropdowns() # Met à jour TOUTES les listes dans les règles
        self.update_status_display() # Crée/met à jour les labels de statut (maintenant basé sur MAC)

        logging.info("Listes de périphériques UI (basées sur MAC) mises à jour.")


    # --- Affichage Statut ---
    # --- Affichage Statut ---
    def update_status_display(self):
        """Crée ou met à jour les labels dans la section statut (basé sur MAC)."""
        logging.debug("Mise à jour de l'affichage du statut.")
        # Vider l'ancien contenu
        for widget in self.scrollable_status_frame.winfo_children():
            widget.destroy()
        self.status_labels = {} # Réinitialiser

        row_num = 0

        # --- Lire toutes les températures et lumières une fois AVANT la boucle ---
        try:
            # Récupère un dict {sensor_id: temp | None}
            all_temp_readings = self.temp_manager.read_all_temperatures()
        except Exception as e:
            logging.error(f"Erreur lors de la lecture globale des températures : {e}")
            all_temp_readings = {} # Retourne un dict vide en cas d'erreur
        try:
            # Récupère un dict {hex_addr: lux | None}
            all_light_readings = self.light_manager.read_all_sensors()
        except Exception as e:
            logging.error(f"Erreur lors de la lecture globale des lumières : {e}")
            all_light_readings = {} # Retourne un dict vide en cas d'erreur


        # --- Affichage Capteurs ---
        ttk.Label(self.scrollable_status_frame, text="Capteurs:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(5, 2))
        row_num += 1

        # Tri des capteurs par alias pour un affichage cohérent
        sorted_sensors = sorted(self.available_sensors, key=lambda x: x[0])

        # Boucle sur les capteurs connus par l'application
        for alias, sensor_id in sorted_sensors:
            value_text = "N/A"
            unit = ""
            # Vérifier si l'ID du capteur est présent dans les lectures récupérées
            is_temp = sensor_id in all_temp_readings
            is_light = sensor_id in all_light_readings

            if is_temp:
                # >>> CORRECTION ICI <<<
                # Chercher la valeur dans le dictionnaire déjà lu
                temp = all_temp_readings.get(sensor_id) # Utiliser .get() est plus sûr
                # >>> FIN CORRECTION <<<
                value_text = f"{temp:.1f}" if temp is not None else "Erreur/Non prêt"
                unit = "°C"
            elif is_light:
                # >>> CORRECTION ICI <<<
                # Chercher la valeur dans le dictionnaire déjà lu
                lux = all_light_readings.get(sensor_id) # Utiliser .get() est plus sûr
                # >>> FIN CORRECTION <<<
                value_text = f"{lux:.1f}" if lux is not None else "Erreur/Non prêt"
                unit = " Lux"
            # Optionnel: Gérer le cas où un sensor_id de available_sensors n'est dans aucune lecture
            # else:
            #    logging.warning(f"Capteur {alias} ({sensor_id}) présent dans available_sensors mais pas dans les lectures récentes.")
            #    value_text = "Lecture Manquante"

            # Création des widgets (identique à avant)
            frame = ttk.Frame(self.scrollable_status_frame)
            frame.grid(row=row_num, column=0, columnspan=4, sticky='w')
            name_label = ttk.Label(frame, text=f"{alias}:", width=25)
            name_label.pack(side=tk.LEFT, padx=5)
            value_label = ttk.Label(frame, text=f"{value_text}{unit}", width=15)
            value_label.pack(side=tk.LEFT, padx=5)
            edit_button = ttk.Button(frame, text="✎", width=2, command=lambda s_id=sensor_id, s_name=alias: self.edit_alias_dialog('sensor', s_id, s_name))
            edit_button.pack(side=tk.LEFT, padx=2)

            self.status_labels[sensor_id] = {'type': 'sensor', 'label_name': name_label, 'label_value': value_label, 'button_edit': edit_button}
            row_num += 1

        # --- Affichage États Kasa (Reste inchangé) ---
        ttk.Label(self.scrollable_status_frame, text="Prises Kasa:", font=('Helvetica', 10, 'bold')).grid(row=row_num, column=0, columnspan=4, sticky='w', pady=(10, 2))
        row_num += 1

        sorted_macs = sorted(self.kasa_devices.keys(), key=lambda m: self.get_alias('device', m))

        for mac in sorted_macs:
            data = self.kasa_devices[mac]
            device_alias = self.get_alias('device', mac)
            device_info = data['info']
            ip_addr = data.get('ip', '?.?.?.?')

            frame_dev = ttk.Frame(self.scrollable_status_frame)
            frame_dev.grid(row=row_num, column=0, columnspan=4, sticky='w')
            dev_display_text = f"{device_alias} ({ip_addr}) [{mac}]"
            dev_name_label = ttk.Label(frame_dev, text=dev_display_text)
            dev_name_label.pack(side=tk.LEFT, padx=5)
            dev_edit_button = ttk.Button(frame_dev, text="✎", width=2, command=lambda d_mac=mac, d_name=device_alias: self.edit_alias_dialog('device', d_mac, d_name))
            dev_edit_button.pack(side=tk.LEFT, padx=2)
            self.status_labels[mac] = {'type': 'device', 'label_name': dev_name_label, 'button_edit': dev_edit_button}
            row_num += 1

            if mac in self.available_outlets:
                for outlet_alias, index in self.available_outlets[mac]:
                    current_state = "Inconnu"
                    if mac in self.live_kasa_states and index in self.live_kasa_states[mac]:
                         current_state = "ON" if self.live_kasa_states[mac][index] else "OFF"
                    elif 'outlets' in device_info:
                        outlet_info = next((o for o in device_info['outlets'] if o.get('index') == index), None)
                        if outlet_info:
                             current_state = "ON" if outlet_info.get('is_on') else "OFF"

                    frame_outlet = ttk.Frame(self.scrollable_status_frame)
                    frame_outlet.grid(row=row_num, column=1, columnspan=3, sticky='w', padx=(20,0))
                    outlet_name_label = ttk.Label(frame_outlet, text=f"└─ {outlet_alias}:", width=23)
                    outlet_name_label.pack(side=tk.LEFT, padx=5)
                    outlet_value_label = ttk.Label(frame_outlet, text=current_state, width=10)
                    outlet_value_label.pack(side=tk.LEFT, padx=5)
                    outlet_edit_button = ttk.Button(frame_outlet, text="✎", width=2, command=lambda d_mac=mac, o_idx=index, o_name=outlet_alias: self.edit_alias_dialog('outlet', d_mac, o_name, sub_id=o_idx))
                    outlet_edit_button.pack(side=tk.LEFT, padx=2)

                    outlet_key = f"{mac}_{index}"
                    self.status_labels[outlet_key] = {'type': 'outlet', 'mac': mac, 'index': index, 'label_name': outlet_name_label, 'label_value': outlet_value_label, 'button_edit': outlet_edit_button}
                    row_num += 1

        # Ajuster la scrollregion (inchangé)
        self.scrollable_status_frame.update_idletasks()
        status_canvas = self.scrollable_status_frame.master
        status_canvas.configure(scrollregion=status_canvas.bbox("all"))


    # --- Mise à Jour Périodique ---
    def schedule_periodic_updates(self):
        """Planifie la mise à jour périodique de l'UI pendant le monitoring."""
        # Update live status lit self.live_kasa_states qui est mis à jour par asyncio
        self.update_live_status()
        self.ui_update_job = self.root.after(5000, self.schedule_periodic_updates) # Toutes les 5s

    def cancel_periodic_updates(self):
        """Annule la mise à jour périodique de l'UI."""
        if self.ui_update_job:
            self.root.after_cancel(self.ui_update_job)
            self.ui_update_job = None

    def update_live_status(self):
        """Met à jour UNIQUEMENT LES VALEURS affichées dans la section Statut."""
        if not self.monitoring_active: return
        logging.debug("Mise à jour des valeurs de statut en direct.")

        # --- Mettre à jour les VALEURS des capteurs ---
        # Lire les valeurs fraîches (peut être optimisé pour ne lire que si nécessaire)
        temp_readings = self.temp_manager.read_all_temperatures()
        light_readings = self.light_manager.read_all_sensors() # {hex_addr: lux}

        for sensor_id, data in self.status_labels.items():
            if data['type'] == 'sensor':
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

                if data['label_value'].winfo_exists(): # Vérifier existence
                    if value is not None:
                        # Formatage cohérent avec update_status_display
                        display_text = f"{value:.1f}{unit}"
                        data['label_value'].config(text=display_text)
                    else:
                        data['label_value'].config(text="Erreur/N/A")
                # Ne pas mettre à jour le nom ici (label_name) car géré par update_status_display

        # --- Mettre à jour les VALEURS des états Kasa (depuis self.live_kasa_states) ---
        for key, data in self.status_labels.items():
             if data['type'] == 'outlet':
                 mac = data['mac']
                 index = data['index']
                 # Utilise _get_shared_kasa_state qui lit self.live_kasa_states (basé sur MAC)
                 current_state = self._get_shared_kasa_state(mac, index)
                 if data['label_value'].winfo_exists():
                     data['label_value'].config(text=current_state)
                 # Ne pas mettre à jour le nom ici (label_name)

             # Pas besoin de màj pour le 'device' lui-même ici (pas d'état à changer)

    def _get_shared_kasa_state(self, mac, index):
        """Récupère l'état Kasa depuis la structure partagée self.live_kasa_states (basée sur MAC)."""
        try:
            # Accède à l'état stocké par la boucle asyncio
            is_on = self.live_kasa_states[mac][index]
            return "ON" if is_on else "OFF"
        except (AttributeError, KeyError):
             # Si la structure n'existe pas ou la clé manque
            # logging.debug(f"État Kasa non trouvé dans live_kasa_states pour MAC {mac}, Index {index}")
            return "Inconnu"

    # --- Logs ---
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
                self.log_display.see(tk.END)
        self.root.after(100, self.update_log_display)

    # --- Démarrage / Arrêt Monitoring ---
    def start_monitoring(self):
        """Démarre la boucle de surveillance des règles."""
        if self.monitoring_active:
            logging.warning("Le monitoring est déjà actif.")
            return

        # Valider les règles avant de démarrer? Optionnel.

        logging.info("Démarrage du monitoring des règles...")
        self.monitoring_active = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self._set_rules_ui_state(tk.DISABLED) # Désactiver édition

        # Préparer l'état partagé (basé sur MAC)
        self.live_kasa_states = {} # Sera rempli par la boucle asyncio

        # Démarrer la boucle asyncio
        self.monitoring_thread = threading.Thread(target=self._run_monitoring_loop, daemon=True)
        self.monitoring_thread.start()

        # Démarrer les mises à jour UI
        self.schedule_periodic_updates()

    def stop_monitoring(self):
        """Arrête la boucle de surveillance."""
        if not self.monitoring_active:
            logging.warning("Le monitoring n'est pas actif.")
            return

        logging.info("Arrêt du monitoring des règles...")
        self.monitoring_active = False # Signal pour arrêter la boucle asyncio

        # Attendre un peu que le thread se termine (optionnel)
        # if self.monitoring_thread and self.monitoring_thread.is_alive():
        #    self.monitoring_thread.join(timeout=2)

        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self._set_rules_ui_state(tk.NORMAL) # Réactiver édition

        # Arrêter les mises à jour UI
        self.cancel_periodic_updates()

        # Éteindre les prises par sécurité
        logging.info("Tentative d'extinction de toutes les prises Kasa par sécurité...")
        threading.Thread(target=self._turn_off_all_kasa_safely, daemon=True).start()

        logging.info("Monitoring arrêté.")


    def _set_rules_ui_state(self, state):
        """Active ou désactive les widgets d'édition dans les règles."""
        # Bouton "Ajouter Règle"
        try:
            main_frame = self.root.winfo_children()[0]
            add_button = next(w for w in main_frame.winfo_children() if isinstance(w, ttk.Button) and "Ajouter" in w.cget("text"))
            add_button.config(state=state)
        except (IndexError, StopIteration, tk.TclError) as e:
            logging.warning(f"Impossible de trouver/configurer le bouton 'Ajouter Règle': {e}")

        # Widgets dans chaque règle
        for rule_id, data in self.rule_widgets.items():
            widgets_dict = data['widgets']
            rule_frame = data['frame']

            # Bouton Supprimer
            try:
                delete_button = next(w for w in rule_frame.winfo_children() if isinstance(w, ttk.Button) and "🗑️" in w.cget("text"))
                delete_button.config(state=state)
            except (StopIteration, tk.TclError) as e:
                logging.warning(f"Impossible de trouver/configurer le bouton 'Supprimer' pour {rule_id}: {e}")

            # Autres widgets (Combos, Entries)
            for widget_name, widget in widgets_dict.items():
                widget_state = state # Par défaut DISABLED
                if state == tk.NORMAL:
                    if isinstance(widget, ttk.Combobox):
                        widget_state = 'readonly'
                    elif isinstance(widget, ttk.Entry):
                         widget_state = tk.NORMAL

                if isinstance(widget, (ttk.Combobox, ttk.Entry)):
                    try:
                        widget.config(state=widget_state)
                    except tk.TclError as e:
                        logging.warning(f"Erreur Tcl config state {widget_name} ({rule_id}): {e}")
                elif isinstance(widget, tk.Frame): # Configurer les enfants des frames 'until'
                    for child_widget in widget.winfo_children():
                        child_state = state
                        if state == tk.NORMAL:
                             if isinstance(child_widget, ttk.Combobox): child_state = 'readonly'
                             elif isinstance(child_widget, ttk.Entry): child_state = tk.NORMAL

                        if isinstance(child_widget, (ttk.Combobox, ttk.Entry)):
                             try:
                                 child_widget.config(state=child_state)
                             except tk.TclError as e:
                                 logging.warning(f"Erreur Tcl config state enfant de {widget_name} ({rule_id}): {e}")


    def _run_monitoring_loop(self):
        """Gère la boucle asyncio dans le thread de monitoring."""
        try:
            self.asyncio_loop = asyncio.get_event_loop()
        except RuntimeError:
            self.asyncio_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.asyncio_loop)

        try:
            # Initialiser l'état Kasa une première fois avant de boucler ?
            # self.asyncio_loop.run_until_complete(self._update_live_kasa_states_task()) # Optionnel
            self.asyncio_loop.run_until_complete(self._async_monitoring_task())
        except Exception as e:
            logging.critical(f"Erreur majeure dans la boucle de monitoring asyncio: {e}", exc_info=True)
        finally:
            logging.info("Boucle de monitoring asyncio terminée.")
            if self.monitoring_active: # Si arrêt anormal
                self.root.after(0, self.stop_monitoring) # Demander arrêt propre à Tkinter


    async def _update_live_kasa_states_task(self):
         """Tâche séparée pour mettre à jour self.live_kasa_states."""
         logging.debug("Mise à jour initiale/périodique des états Kasa en direct...")
         new_live_states = {} # Sera {mac: {index: bool}}
         tasks = []

         # Crée une tâche de rafraîchissement pour chaque appareil
         for mac, data in self.kasa_devices.items():
             controller = data['controller']
             tasks.append(self._fetch_one_kasa_state(mac, controller))

         # Exécute toutes les lectures en parallèle
         results = await asyncio.gather(*tasks, return_exceptions=True)

         # Traite les résultats
         for result in results:
             if isinstance(result, Exception):
                 logging.error(f"Erreur lors de la lecture d'état Kasa: {result}")
             elif isinstance(result, dict) and result: # Si on a un dict non vide {mac: {index: state}}
                 # Fusionne le résultat dans new_live_states
                 new_live_states.update(result)

         self.live_kasa_states = new_live_states # Remplacer l'état précédent
         logging.debug(f"États Kasa 'live' mis à jour: {self.live_kasa_states}")

    async def _fetch_one_kasa_state(self, mac, controller):
         """Coroutine pour lire l'état d'un appareil Kasa."""
         try:
             await controller._connect() # Assure connexion/refresh
             if controller._device:
                 device_states_list = await controller.get_outlet_state() # Liste de dicts [{'index': i, 'is_on': b, ...}]
                 if device_states_list is not None:
                      # Crée le dict {index: state} pour cette MAC
                     outlet_states = {outlet['index']: outlet['is_on'] for outlet in device_states_list if 'index' in outlet and 'is_on' in outlet}
                     return {mac: outlet_states} # Retourne le résultat pour cette MAC
                 else:
                     logging.warning(f"Impossible d'obtenir l'état des prises pour {mac} (get_outlet_state a retourné None)")
             else:
                 logging.warning(f"Impossible de connecter/rafraîchir {mac} pour lire l'état.")
         except Exception as e:
             logging.error(f"Erreur lors de la lecture de l'état de {mac}: {e}")
             # Lève l'exception pour que gather la capture
             raise e # Ou retourner {} ? Pour l'instant on lève.
         return {} # Retourner dict vide en cas d'échec non exceptionnel


    async def _async_monitoring_task(self):
        """Tâche principale de monitoring (utilise MAC)."""
        active_until_rules = {} # { rule_id: {'end_time': datetime | None, 'revert_action': 'ON'|'OFF'} }
        last_kasa_update_time = datetime.min # Forcer la mise à jour au début
        kasa_update_interval = timedelta(seconds=10) # Màj état Kasa toutes les 10s

        while self.monitoring_active:
            current_time = datetime.now()
            logging.debug(f"--- Cycle Monitoring {current_time.strftime('%H:%M:%S')} ---")

            # --- 1. Lire les capteurs ---
            try:
                # Utiliser run_in_executor si les lectures sont bloquantes longtemps
                temp_values = await self.asyncio_loop.run_in_executor(None, self.temp_manager.read_all_temperatures)
                light_values = await self.asyncio_loop.run_in_executor(None, self.light_manager.read_all_sensors)
                sensor_values = {**temp_values, **light_values}
                valid_sensor_values = {k: v for k, v in sensor_values.items() if v is not None}
                logging.debug(f"Valeurs capteurs: {valid_sensor_values}")
            except Exception as e:
                logging.error(f"Erreur lecture capteurs dans boucle: {e}")
                valid_sensor_values = {}

            # --- 2. Lire l'état Kasa (périodiquement) ---
            if current_time - last_kasa_update_time >= kasa_update_interval:
                 try:
                    await self._update_live_kasa_states_task() # Met à jour self.live_kasa_states
                    last_kasa_update_time = current_time
                 except Exception as e:
                     logging.error(f"Échec de la mise à jour périodique des états Kasa: {e}")
                     # Continuer avec les anciens états si disponibles?

            # --- 3. Évaluer les règles (basé sur MAC) ---
            tasks_to_run = [] # Collecter les coroutines Kasa à exécuter
            rules_to_evaluate = list(self.rules) # Copie pour itération sûre
            # { (mac, index): 'ON' | 'OFF' } - Action prioritaire pour chaque prise
            desired_outlet_states = {}

            # --- 3a. Évaluer les conditions "UNTIL" actives ---
            active_until_rules_copy = dict(active_until_rules) # Copie pour itération
            for rule_id, until_info in active_until_rules_copy.items():
                 rule = next((r for r in rules_to_evaluate if r.get('id') == rule_id), None)
                 if not rule: # La règle a peut-être été supprimée
                     logging.warning(f"Condition 'UNTIL' active pour règle {rule_id} supprimée. Annulation.")
                     del active_until_rules[rule_id]
                     continue

                 target_mac = rule.get('target_device_mac')
                 target_index = rule.get('target_outlet_index')
                 if target_mac is None or target_index is None: continue # Règle invalide
                 outlet_key = (target_mac, target_index)

                 revert_action_needed = False
                 until_end_time = until_info.get('end_time')
                 until_condition = rule.get('until_condition')

                 if until_end_time and current_time >= until_end_time: # Timer
                     revert_action_needed = True
                     logging.info(f"Règle {rule_id}: Fin 'UNTIL Timer'. Action retour: {until_info['revert_action']}")
                 elif until_condition and until_condition.get('type') == 'Capteur': # Capteur
                     until_sensor_id = until_condition.get('sensor_id')
                     until_operator = until_condition.get('operator')
                     until_threshold = until_condition.get('threshold') # Déjà float

                     if until_sensor_id and until_operator and until_threshold is not None:
                          if until_sensor_id in valid_sensor_values:
                              current_until_value = valid_sensor_values[until_sensor_id]
                              # Utiliser _compare (qui gère float)
                              if self._compare(current_until_value, until_operator, until_threshold):
                                   revert_action_needed = True
                                   logging.info(f"Règle {rule_id}: Condition 'UNTIL {until_sensor_id} {until_operator} {until_threshold}' ({current_until_value}) remplie. Action retour: {until_info['revert_action']}")
                          else:
                              logging.warning(f"Règle {rule_id}: Capteur 'UNTIL' {until_sensor_id} indisponible.")
                     else:
                          logging.warning(f"Règle {rule_id}: Condition 'UNTIL Capteur' incomplète.")


                 if revert_action_needed:
                      # Priorité haute pour l'action de retour
                     desired_outlet_states[outlet_key] = until_info['revert_action']
                     del active_until_rules[rule_id] # Désactiver UNTIL

            # --- 3b. Évaluer les conditions principales "SI" ---
            for rule in rules_to_evaluate:
                rule_id = rule.get('id')
                if not rule_id: continue # Règle sans ID? Ignorer.

                # Vérifier si UNTIL est toujours actif pour cette règle
                is_until_active = rule_id in active_until_rules

                # Si un revert a été décidé pour cette prise, ne pas évaluer la condition SI principale
                target_mac = rule.get('target_device_mac')
                target_index = rule.get('target_outlet_index')
                if target_mac is None or target_index is None:
                    #logging.debug(f"Règle {rule_id or 'Inconnue'} incomplète (MAC/Index cible), ignorée.")
                    continue
                outlet_key = (target_mac, target_index)
                if outlet_key in desired_outlet_states and desired_outlet_states[outlet_key] == active_until_rules_copy.get(rule_id,{}).get('revert_action'):
                     # L'action de revert a priorité, on a déjà traité.
                     continue

                # Vérifier les éléments essentiels de la condition SI
                sensor_id = rule.get('sensor_id')
                operator = rule.get('operator')
                threshold = rule.get('threshold') # Déjà float ou None
                primary_action = rule.get('action') # 'ON' ou 'OFF'

                if not all([sensor_id, operator, threshold is not None, primary_action]):
                    #logging.debug(f"Règle {rule_id or 'Inconnue'} incomplète (SI), ignorée.")
                    continue

                # Évaluer SI
                if sensor_id in valid_sensor_values:
                    current_value = valid_sensor_values[sensor_id]
                    # Utiliser _compare (gère float)
                    condition_met = self._compare(current_value, operator, threshold)
                    logging.debug(f"Règle {rule_id}: Éval SI {sensor_id}({current_value}) {operator} {threshold} -> {condition_met}")

                    if condition_met:
                         # La condition SI est remplie. Définir l'état désiré SI aucune action
                         # prioritaire (revert UNTIL) n'a été définie.
                         if outlet_key not in desired_outlet_states:
                              desired_outlet_states[outlet_key] = primary_action

                              # Vérifier si on doit activer un "UNTIL" maintenant
                              until_condition = rule.get('until_condition')
                              if until_condition and not is_until_active: # Ne pas réactiver si déjà actif
                                   until_type = until_condition.get('type')
                                   revert_action = 'OFF' if primary_action == 'ON' else 'ON'
                                   end_time = None # Pour type Capteur
                                   log_msg = f"Règle {rule_id}: Activation 'UNTIL "

                                   if until_type == 'Timer (secondes)':
                                        duration = until_condition.get('duration') # Déjà int
                                        if duration is not None:
                                             end_time = current_time + timedelta(seconds=duration)
                                             log_msg += f"Timer' de {duration}s. Fin: {end_time.strftime('%H:%M:%S')}."
                                        else: log_msg = "" # Ne pas activer si durée invalide
                                   elif until_type == 'Capteur':
                                        u_sid = until_condition.get('sensor_id')
                                        u_op = until_condition.get('operator')
                                        u_th = until_condition.get('threshold')
                                        if all([u_sid, u_op, u_th is not None]):
                                             log_msg += f"Capteur' ({u_sid} {u_op} {u_th})."
                                        else: log_msg = "" # Ne pas activer si condition invalide
                                   else:
                                        log_msg = "" # Type UNTIL inconnu

                                   if log_msg: # Si l'activation est valide
                                        logging.info(log_msg)
                                        active_until_rules[rule_id] = {
                                             'revert_action': revert_action,
                                             'end_time': end_time
                                        }
                    # Si la condition SI n'est PAS remplie, on ne fait rien ici.
                    # L'étape suivante gérera l'extinction si nécessaire.

                else: # Capteur principal non disponible
                     logging.warning(f"Règle {rule_id}: Capteur SI {sensor_id} indisponible.")


            # --- 4. Appliquer les changements Kasa ---
            logging.debug(f"États Kasa désirés (après règles): {desired_outlet_states}")
            logging.debug(f"États Kasa actuels (live): {self.live_kasa_states}")

            # --- 4a. Actions explicites (ON/OFF basé sur desired_outlet_states) ---
            for outlet_key, desired_state in desired_outlet_states.items():
                 target_mac, target_index = outlet_key
                 # Lire l'état actuel connu depuis self.live_kasa_states
                 current_state_bool = self.live_kasa_states.get(target_mac, {}).get(target_index) # True, False ou None

                 action_needed = False
                 action_func_name = None
                 if desired_state == 'ON' and current_state_bool is not True:
                     action_needed = True
                     action_func_name = 'turn_outlet_on'
                 elif desired_state == 'OFF' and current_state_bool is not False:
                     action_needed = True
                     action_func_name = 'turn_outlet_off'

                 if action_needed:
                      if target_mac in self.kasa_devices:
                          controller = self.kasa_devices[target_mac]['controller']
                          logging.info(f"Action requise pour {self.get_alias('device', target_mac)} Prise {self.get_alias('outlet', target_mac, target_index)} ({target_mac}/{target_index}): {action_func_name}")
                          # Ajouter la coroutine à exécuter
                          tasks_to_run.append(getattr(controller, action_func_name)(target_index))
                          # Mettre à jour l'état supposé dans live_kasa_states immédiatement
                          if target_mac not in self.live_kasa_states: self.live_kasa_states[target_mac] = {}
                          self.live_kasa_states[target_mac][target_index] = (desired_state == 'ON')
                      else:
                          logging.error(f"Impossible d'exécuter {action_func_name} pour {target_mac}: appareil inconnu.")


            # --- 4b. Gérer les prises qui n'ont PAS d'état désiré explicite (doivent être OFF) ---
            # Identifier toutes les prises gérées par au moins une règle valide
            all_managed_outlets = set()
            for rule in rules_to_evaluate:
                mac = rule.get('target_device_mac')
                idx = rule.get('target_outlet_index')
                if mac and idx is not None:
                     all_managed_outlets.add((mac, idx))

            # Parcourir l'état live connu
            for mac, outlets in self.live_kasa_states.items():
                 for index, is_on in outlets.items():
                      outlet_key = (mac, index)
                      # Si la prise est gérée, qu'aucune règle ne la veut ON, et qu'elle est ON
                      if outlet_key in all_managed_outlets and \
                         outlet_key not in desired_outlet_states and \
                         is_on is True:

                           if mac in self.kasa_devices:
                               controller = self.kasa_devices[mac]['controller']
                               logging.info(f"Action requise (implicite): Éteindre {self.get_alias('device', mac)} Prise {self.get_alias('outlet', mac, index)} ({mac}/{index}) car non activée par règles.")
                               tasks_to_run.append(controller.turn_outlet_off(index))
                               # Mettre à jour l'état supposé
                               self.live_kasa_states[mac][index] = False
                           else:
                                logging.error(f"Impossible d'éteindre implicitement {mac}/{index}: appareil inconnu.")


            # --- 5. Exécuter les tâches Kasa collectées ---
            if tasks_to_run:
                logging.debug(f"Exécution de {len(tasks_to_run)} tâche(s) Kasa...")
                results = await asyncio.gather(*tasks_to_run, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                         # Difficile de savoir quelle tâche a échoué sans plus d'info
                        logging.error(f"Erreur lors de l'exécution d'une tâche Kasa: {result}")
                logging.debug("Tâches Kasa du cycle terminées.")

            # --- 6. Attendre ---
            await asyncio.sleep(2) # Intervalle boucle principale


    def _compare(self, value1, operator, value2):
        """Effectue une comparaison (gère float)."""
        # Ajout de logs pour déboguer le problème des décimales
        logging.debug(f"Comparaison: value1={value1}({type(value1)}), operator='{operator}', value2={value2}({type(value2)})")
        try:
            # Assurer que les deux sont des floats pour la comparaison
            v1 = float(value1)
            v2 = float(value2)
            logging.debug(f"Comparaison (après float): v1={v1}, operator='{operator}', v2={v2}")

            if operator == '<': return v1 < v2
            if operator == '>': return v1 > v2
            # Attention à la comparaison d'égalité avec les floats
            if operator == '=': return abs(v1 - v2) < 1e-9 # Comparaison avec tolérance
            if operator == '!=': return abs(v1 - v2) >= 1e-9
            if operator == '<=': return v1 <= v2
            if operator == '>=': return v1 >= v2

        except (ValueError, TypeError) as e:
            logging.error(f"Erreur de conversion float ou comparaison: {value1} ('{type(value1)}') {operator} {value2} ('{type(value2)}') - {e}")
            return False
        return False # Opérateur inconnu

    def _turn_off_all_kasa_safely(self):
        """Tente d'éteindre toutes les prises Kasa connues (utilise MAC)."""
        # Exécuter dans une boucle asyncio temporaire si nécessaire
        try:
             loop = asyncio.get_event_loop()
             if loop.is_running():
                 # Si la boucle de monitoring tourne encore (peu probable ici), utiliser run_coroutine_threadsafe
                 # Mais normalement stop_monitoring arrête la boucle avant d'appeler ceci.
                 # On lance donc dans une nouvelle exécution si besoin.
                 logging.info("Utilisation de la boucle existante (si non fermée) pour l'extinction.")
                 loop.run_until_complete(self._async_turn_off_all())
             else:
                 logging.info("Lancement d'une exécution asyncio pour l'extinction.")
                 asyncio.run(self._async_turn_off_all()) # Utilise asyncio.run pour gérer la boucle
        except RuntimeError as e: # Pas de boucle définie pour ce thread ou boucle fermée
            logging.info(f"RuntimeError lors de l'extinction ({e}), utilisation de asyncio.run.")
            try:
                asyncio.run(self._async_turn_off_all())
            except Exception as final_e:
                 logging.error(f"Erreur finale lors de l'exécution de _async_turn_off_all avec asyncio.run: {final_e}")


    async def _async_turn_off_all(self):
        """Tâche asynchrone pour éteindre toutes les prises (utilise MAC)."""
        tasks = []
        logging.info(f"Préparation de l'extinction pour {len(self.kasa_devices)} appareils Kasa connus...")
        for mac, data in self.kasa_devices.items():
            controller = data['controller']
            device_info = data['info']
            if device_info.get('is_strip') or device_info.get('is_plug'):
                logging.info(f"Extinction de sécurité planifiée pour: {self.get_alias('device', mac)} ({mac})")
                tasks.append(controller.turn_all_outlets_off()) # Méthode du contrôleur

        if tasks:
            logging.info(f"Exécution de {len(tasks)} tâches d'extinction de sécurité...")
            results = await asyncio.gather(*tasks, return_exceptions=True)
            success_count = 0
            fail_count = 0
            for result in results:
                if isinstance(result, Exception):
                    logging.error(f"Erreur lors de l'extinction de sécurité d'un appareil: {result}")
                    fail_count += 1
                else:
                    success_count +=1
            logging.info(f"Extinction de sécurité terminée. Succès: {success_count}, Échecs: {fail_count}.")
        else:
             logging.info("Aucune prise/barre Kasa à éteindre lors de l'arrêt.")


    def save_configuration(self):
        """Sauvegarde la configuration (règles avec MAC, alias basés sur MAC)."""
        logging.info("Préparation de la sauvegarde : mise à jour des données des règles depuis l'UI...")
        # Assurer que toutes les modifications UI sont dans self.rules
        for rule_id in list(self.rule_widgets.keys()): # Copie pour éviter erreur si règle supprimée pendant itération
             if rule_id in self.rule_widgets: # Vérifier si la règle existe toujours
                  try:
                      self.on_rule_change(rule_id) # Met à jour self.rules[rule_index]
                  except Exception as e:
                      logging.error(f"Erreur pendant on_rule_change pour {rule_id} avant sauvegarde: {e}")

        # Nettoyer les règles invalides (sans MAC cible par exemple) avant sauvegarde? Optionnel.
        valid_rules = []
        for rule in self.rules:
            is_valid = True
            # Ajouter ici des vérifications si nécessaire (ex: mac non None)
            # if not rule.get('target_device_mac'): is_valid = False
            if is_valid:
                valid_rules.append(rule)
            else:
                logging.warning(f"Exclusion de la règle invalide ID {rule.get('id','???')} de la sauvegarde.")

        config_to_save = {
            "aliases": self.aliases, # Structure alias basée sur MAC
            "rules": valid_rules      # Règles avec target_device_mac
        }

        logging.debug(f"Données prêtes pour la sauvegarde : {config_to_save}")

        if save_config(config_to_save, DEFAULT_CONFIG_FILE):
            messagebox.showinfo("Sauvegarde", "Configuration sauvegardée avec succès.", parent=self.root)
        else:
            messagebox.showerror("Sauvegarde", "Erreur lors de la sauvegarde de la configuration.", parent=self.root)


    def on_closing(self):
        """Gère la fermeture de l'application."""
        if self.monitoring_active:
            if messagebox.askyesno("Quitter", "Le monitoring est actif. Voulez-vous l'arrêter et quitter ?", parent=self.root):
                self.stop_monitoring()
                # Donner un peu de temps pour l'extinction avant de détruire
                self.root.after(1000, self.root.destroy) # Attendre 1 sec
            else:
                return # Ne pas quitter
        else:
            if messagebox.askyesno("Quitter", "Êtes-vous sûr de vouloir quitter ?", parent=self.root):
                self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = GreenhouseApp(root)
    root.mainloop()