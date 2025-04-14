# config_manager.py
import yaml # Ou import json
import logging
import os

DEFAULT_CONFIG_FILE = 'config.yaml' # Ou 'config.json'

def load_config(filename=DEFAULT_CONFIG_FILE) -> dict:
    """Charge la configuration depuis un fichier YAML (ou JSON)."""
    if not os.path.exists(filename):
        logging.warning(f"Fichier de configuration '{filename}' non trouvé. Création d'une configuration par défaut.")
        # Structure par défaut si le fichier n'existe pas
        return {"aliases": {"sensors": {}, "devices": {}, "outlets": {}}, "rules": []}

    try:
        with open(filename, 'r', encoding='utf-8') as f:
            # Pour YAML:
            config = yaml.safe_load(f)
            # Pour JSON:
            # import json
            # config = json.load(f)

            # S'assurer que les clés principales existent
            if config is None: config = {} # Fichier vide
            if "aliases" not in config: config["aliases"] = {"sensors": {}, "devices": {}, "outlets": {}}
            if "sensors" not in config["aliases"]: config["aliases"]["sensors"] = {}
            if "devices" not in config["aliases"]: config["aliases"]["devices"] = {}
            if "outlets" not in config["aliases"]: config["aliases"]["outlets"] = {}
            if "rules" not in config: config["rules"] = []

            logging.info(f"Configuration chargée depuis '{filename}'.")
            return config
    except Exception as e:
        logging.error(f"Erreur lors du chargement de la configuration depuis '{filename}': {e}")
        # Retourner une config par défaut en cas d'erreur de lecture/parsing
        return {"aliases": {"sensors": {}, "devices": {}, "outlets": {}}, "rules": []}


def save_config(data: dict, filename=DEFAULT_CONFIG_FILE):
    """Sauvegarde la configuration dans un fichier YAML (ou JSON)."""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            # Pour YAML:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)
            # Pour JSON:
            # import json
            # json.dump(data, f, indent=2, ensure_ascii=False)
        logging.info(f"Configuration sauvegardée dans '{filename}'.")
        return True
    except Exception as e:
        logging.error(f"Erreur lors de la sauvegarde de la configuration dans '{filename}': {e}")
        return False

# Test simple
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    test_data = load_config('test_config.yaml')
    print("Loaded:", test_data)
    test_data['rules'].append({'id': 'rule1', 'sensor_id': 'test'})
    test_data['aliases']['sensors']['test'] = 'Mon Capteur Test'
    save_config(test_data, 'test_config.yaml')
    print("Saved.")
    # Cleanup
    # import os
    # os.remove('test_config.yaml')