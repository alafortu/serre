import streamlit as st
import asyncio
import threading
import time
import json
import logging
import os
from datetime import datetime
import random
from w1thermsensor import W1ThermSensor, SensorNotReadyError

# --- Importez vos modules existants ---
import discover_device
import device_control

# --- Configuration du logging ---
logging.basicConfig(filename='serre.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')

# --- Fonctions de lecture des capteurs ---
def read_temperature_sensors() -> dict:
    """Retourne un dictionnaire {sensor_id: température} pour les capteurs DS18B20."""
    sensors = W1ThermSensor.get_available_sensors()
    data = {}
    for sensor in sensors:
        try:
            data[sensor.id] = sensor.get_temperature()  # Température en °C
        except SensorNotReadyError:
            data[sensor.id] = None
    return data

def read_light_sensor() -> float:
    """
    Retourne la valeur lue par le capteur de lumière.
    Remplacez ici la lecture réelle par celle de votre capteur BH170.
    Pour l'exemple, on simule une valeur aléatoire.
    """
    return random.uniform(0, 1000)

def get_all_sensor_readings(sensor_names: dict) -> dict:
    """
    Regroupe la lecture de tous les capteurs en utilisant un mapping des noms.
    La clé sera le nom convivial défini par l'utilisateur.
    """
    readings = {}
    temps = read_temperature_sensors()
    for sensor_id, temp in temps.items():
        name = sensor_names.get(sensor_id, sensor_id)
        readings[name] = temp
    # Capteur de lumière, identifié ici par la clé "light"
    light_name = sensor_names.get("light", "Light Sensor")
    readings[light_name] = read_light_sensor()
    return readings

# --- Découverte des appareils Kasa ---
async def discover_kasa_devices_async() -> list:
    dd = discover_device.DeviceDiscoverer()
    devices_info = await dd.discover()
    return devices_info

def discover_kasa_devices() -> list:
    return asyncio.run(discover_kasa_devices_async())

def turn_off_all_kasa_devices(devices: list):
    """
    Pour chaque appareil Kasa découvert, éteint toutes les prises.
    """
    for device in devices:
        ip = device["ip"]
        is_strip = device.get("is_strip", False)
        is_plug = device.get("is_plug", False)
        controller = device_control.DeviceController(ip, is_strip=is_strip, is_plug=is_plug)
        asyncio.run(controller.turn_all_outlets_off())
        logging.info(f"Éteint {device.get('alias', ip)} ({ip})")

# --- Évaluation d'une condition ---
def evaluate_condition(sensor_value, operator: str, threshold: float) -> bool:
    if sensor_value is None:
        return False
    if operator == "<":
        return sensor_value < threshold
    elif operator == ">":
        return sensor_value > threshold
    elif operator == "=":
        return sensor_value == threshold
    elif operator == "!=":
        return sensor_value != threshold
    elif operator == "<=":
        return sensor_value <= threshold
    elif operator == ">=":
        return sensor_value >= threshold
    else:
        return False

# --- Gestion asynchrone des règles ---
stop_event = threading.Event()
background_thread = None

def rules_runner(rules: list, sensors_mapping: dict, kasa_mapping: dict):
    """
    Boucle en arrière-plan qui parcourt les règles, lit les capteurs et commande les prises
    en fonction de la clause SI … ALORS, et gère l'option JUSQU'À.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    while not stop_event.is_set():
        current_readings = get_all_sensor_readings(sensors_mapping)
        logging.info(f"Lecture des capteurs : {current_readings}")
        for idx, rule in enumerate(rules):
            # Partie condition (SI …)
            cond = rule.get("if", {})
            sensor_name = cond.get("sensor")
            operator = cond.get("operator")
            value = cond.get("value")
            if sensor_name in current_readings and evaluate_condition(current_readings[sensor_name], operator, value):
                # La condition est satisfaite → exécute l'action ALORS …
                action_part = rule.get("then", {})
                device_key = action_part.get("device")
                outlet_index = action_part.get("outlet_index", 0)
                action = action_part.get("action")
                device_info = kasa_mapping.get(device_key)
                if device_info:
                    controller = device_control.DeviceController(
                        device_info["ip"],
                        is_strip=device_info.get("is_strip", False),
                        is_plug=device_info.get("is_plug", False)
                    )
                    if action == "on":
                        loop.run_until_complete(controller.turn_outlet_on(outlet_index))
                    else:
                        loop.run_until_complete(controller.turn_outlet_off(outlet_index))
                    logging.info(f"Règle {idx+1} déclenchée : {device_info.get('alias', device_key)}, prise {outlet_index} → {action}")
                    # Optionnel: gérer la clause "JUSQU'À"
                    until = rule.get("until")
                    if until:
                        if until.get("type") == "timer":
                            duration = until.get("duration", 60)
                            logging.info(f"Règle {idx+1} (JUSQU'À timer): attente de {duration} secondes.")
                            start_time = time.time()
                            while time.time() - start_time < duration and not stop_event.is_set():
                                time.sleep(1)
                            revert = "off" if action == "on" else "on"
                            if revert == "on":
                                loop.run_until_complete(controller.turn_outlet_on(outlet_index))
                            else:
                                loop.run_until_complete(controller.turn_outlet_off(outlet_index))
                            logging.info(f"Règle {idx+1} timer terminé, inversion vers {revert}.")
                        elif until.get("type") == "sensor":
                            u_cond = until.get("condition", {})
                            u_sensor = u_cond.get("sensor")
                            u_operator = u_cond.get("operator")
                            u_value = u_cond.get("value")
                            logging.info(f"Règle {idx+1} (JUSQU'À sensor): attente jusqu'à ce que {u_sensor} {u_operator} {u_value}.")
                            while not stop_event.is_set():
                                current_readings = get_all_sensor_readings(sensors_mapping)
                                if u_sensor in current_readings and evaluate_condition(current_readings[u_sensor], u_operator, u_value):
                                    break
                                time.sleep(1)
                            revert = "off" if action == "on" else "on"
                            if revert == "on":
                                loop.run_until_complete(controller.turn_outlet_on(outlet_index))
                            else:
                                loop.run_until_complete(controller.turn_outlet_off(outlet_index))
                            logging.info(f"Règle {idx+1} condition 'JUSQU’À' atteinte, inversion vers {revert}.")
        time.sleep(2)
    loop.close()

# --- Gestion de la configuration ---
def load_config():
    """Charge la configuration depuis 'serre_config.json' si elle existe."""
    if os.path.exists("serre_config.json"):
        with open("serre_config.json", "r") as f:
            config = json.load(f)
        st.session_state.rules = config.get("rules", st.session_state.rules)
        st.session_state.sensors_names = config.get("sensors_names", st.session_state.sensors_names)
        st.session_state.kasa_devices = config.get("kasa_devices", st.session_state.kasa_devices)
        st.sidebar.success("Configuration chargée depuis serre_config.json")
    else:
        st.sidebar.warning("Aucune configuration sauvegardée trouvée.")

# --- Interface Streamlit ---
st.title("Gestion de la serre intelligente")

# Initialisation de la configuration en session state
if "rules" not in st.session_state:
    st.session_state.rules = []  # Liste des règles

if "sensors_names" not in st.session_state:
    sensors_available = W1ThermSensor.get_available_sensors()
    st.session_state.sensors_names = {s.id: s.id for s in sensors_available}
    st.session_state.sensors_names["light"] = "Light Sensor"

if "kasa_devices" not in st.session_state:
    with st.spinner("Recherche des appareils Kasa…"):
        devices = discover_kasa_devices()
    st.session_state.kasa_devices = {device["alias"]: device for device in devices}
    turn_off_all_kasa_devices(devices)

# --- Barre latérale pour la configuration ---
st.sidebar.header("Configuration")
if st.sidebar.button("Charger configuration"):
    load_config()

st.sidebar.subheader("Capteurs")
for sensor_id, name in st.session_state.sensors_names.items():
    new_name = st.sidebar.text_input(f"Nom pour le capteur {sensor_id}", value=name, key=f"sensor_{sensor_id}")
    st.session_state.sensors_names[sensor_id] = new_name

st.sidebar.subheader("Appareils Kasa")
for device_alias, device in st.session_state.kasa_devices.items():
    new_name = st.sidebar.text_input(f"Nom pour {device_alias} ({device['ip']})", value=device_alias, key=f"kasa_{device['ip']}")
    # Vous pouvez mettre à jour la clé dans le mapping si nécessaire

# --- Interface principale de définition des règles ---
st.header("Configuration des règles")
st.write("Format d'une règle :")
st.write('**SI** [capteur] [opérateur (<,>,=,!=,<=,>=)] [valeur] **ALORS** [barre Kasa] [index de la prise] [action (on/off)]')
st.write("Optionnellement, ajouter **JUSQU’À** : soit un timer (durée en secondes) soit une condition sur un capteur.")

def rule_form(rule: dict, idx: int, sensors_options: list, kasa_options: list) -> dict:
    st.markdown(f"**Règle {idx+1}**")
    col1, col2, col3 = st.columns([3, 3, 3])
    with col1:
        st.write("SI")
        sensor_selected = st.selectbox("Capteur", options=sensors_options,
                                       index=sensors_options.index(rule.get("if", {}).get("sensor", sensors_options[0])),
                                       key=f"if_sensor_{idx}")
        oper = st.selectbox("Opérateur", options=["<", ">", "=", "!=", "<=", ">="],
                            index=["<", ">", "=", "!=", "<=", ">="].index(rule.get("if", {}).get("operator", ">=")),
                            key=f"if_op_{idx}")
        cond_value = st.number_input("Valeur", value=rule.get("if", {}).get("value", 0.0),
                                     key=f"if_val_{idx}")
    with col2:
        st.write("ALORS")
        device_selected = st.selectbox("Barre Kasa", options=kasa_options,
                                       index=kasa_options.index(rule.get("then", {}).get("device", kasa_options[0])),
                                       key=f"then_dev_{idx}")
        outlet = st.number_input("Index de la prise", min_value=0,
                                 value=rule.get("then", {}).get("outlet_index", 0),
                                 key=f"then_outlet_{idx}")
        act = st.selectbox("Action", options=["on", "off"],
                           index=["on", "off"].index(rule.get("then", {}).get("action", "on")),
                           key=f"then_act_{idx}")
    with col3:
        add_until = st.checkbox("Ajouter 'JUSQU’À'", value=("until" in rule and rule["until"] is not None),
                                key=f"until_check_{idx}")
        until_val = {}
        if add_until:
            until_type = st.selectbox("Type", options=["timer", "sensor"], index=0, key=f"until_type_{idx}")
            if until_type == "timer":
                duration = st.number_input("Durée (s)", min_value=1,
                                           value=rule.get("until", {}).get("duration", 60),
                                           key=f"until_dur_{idx}")
                until_val = {"type": "timer", "duration": duration}
            else:
                st.write("Condition JUSQU’À")
                u_sensor = st.selectbox("Capteur", options=sensors_options,
                                        index=sensors_options.index(rule.get("until", {}).get("condition", {}).get("sensor", sensors_options[0])),
                                        key=f"until_sensor_{idx}")
                u_oper = st.selectbox("Opérateur", options=["<", ">", "=", "!=", "<=", ">="],
                                      index=["<", ">", "=", "!=", "<=", ">="].index(rule.get("until", {}).get("condition", {}).get("operator", ">=")),
                                      key=f"until_op_{idx}")
                u_value = st.number_input("Valeur", value=rule.get("until", {}).get("condition", {}).get("value", 0.0),
                                          key=f"until_val_{idx}")
                u_action = st.selectbox("Action", options=["on", "off"],
                                        index=["on", "off"].index(rule.get("until", {}).get("action", "off")),
                                        key=f"until_act_{idx}")
                until_val = {"type": "sensor", 
                             "condition": {"sensor": u_sensor, "operator": u_oper, "value": u_value},
                             "action": u_action}
        rule["if"] = {"sensor": sensor_selected, "operator": oper, "value": cond_value}
        rule["then"] = {"device": device_selected, "outlet_index": int(outlet), "action": act}
        rule["until"] = until_val if add_until else None
    return rule

sensors_options = list(st.session_state.sensors_names.values())
kasa_options = list(st.session_state.kasa_devices.keys())

# Affichage et édition dynamique de la liste des règles
remove_rule_indices = []
for i, rule in enumerate(st.session_state.rules):
    col_rule, col_del = st.columns([9, 1])
    with col_rule:
        st.session_state.rules[i] = rule_form(rule, i, sensors_options, kasa_options)
    with col_del:
        if st.button("X", key=f"remove_{i}"):
            remove_rule_indices.append(i)
if remove_rule_indices:
    for i in sorted(remove_rule_indices, reverse=True):
        st.session_state.rules.pop(i)

if st.button("Ajouter une règle"):
    default_rule = {
        "if": {"sensor": sensors_options[0] if sensors_options else "", "operator": ">=", "value": 0.0},
        "then": {"device": kasa_options[0] if kasa_options else "", "outlet_index": 0, "action": "on"},
        "until": None
    }
    st.session_state.rules.append(default_rule)

st.write("Règles actuelles :", st.session_state.rules)

# --- Boutons de lancement / arrêt / sauvegarde ---
# --- Boutons de lancement / arrêt / sauvegarde ---
col_start, col_stop, col_save = st.columns(3)

if col_start.button("Gère ma serre"):
    if (background_thread is None) or (not background_thread.is_alive()):
        stop_event.clear()
        # Passe la configuration actuelle au thread (copie des règles, capteurs et mapping des appareils)
        rules_copy = st.session_state.rules.copy()
        sensors_mapping = st.session_state.sensors_names
        kasa_mapping = st.session_state.kasa_devices
        background_thread = threading.Thread(target=rules_runner,
                                             args=(rules_copy, sensors_mapping, kasa_mapping),
                                             daemon=True)
        background_thread.start()
        st.success("La gestion de la serre est lancée !")
    else:
        st.warning("La gestion de la serre est déjà en cours.")

if col_stop.button("Stop"):
    stop_event.set()
    st.success("La gestion de la serre est arrêtée.")

if col_save.button("Sauvegarder configuration"):
    config = {
        "rules": st.session_state.rules,
        "sensors_names": st.session_state.sensors_names,
        "kasa_devices": st.session_state.kasa_devices
    }
    with open("serre_config.json", "w") as f:
        json.dump(config, f, indent=2)
    st.success("Configuration sauvegardée dans serre_config.json")


# --- Affichage en temps réel des lectures ---
st.header("État en temps réel des capteurs")
sensor_placeholder = st.empty()
auto_refresh = st.checkbox("Actualisation en continu", value=True, key="auto_refresh")
if auto_refresh:
    sensor_data = get_all_sensor_readings(st.session_state.sensors_names)
    sensor_placeholder.table(sensor_data)
    time.sleep(2)
    st.experimental_rerun()
