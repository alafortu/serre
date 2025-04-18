# logger_setup.py (Version Corrigée)
import logging
import queue
from logging.handlers import RotatingFileHandler
import tkinter as tk
import sys # Ajouté pour le console handler

# Classe pour rediriger les logs vers un widget Text de Tkinter via une queue
class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    # Utiliser emit est standard, mais pré-formater ici est ok si
    # update_log_display attend une chaîne.
    def emit(self, record):
        try:
            # Formate le message avant de le mettre dans la queue
            msg = self.format(record)
            self.log_queue.put(msg)
        except Exception:
            self.handleError(record) # Gérer les erreurs potentielles ici

def setup_logging(log_queue):
    """Configure le logging vers un fichier, la console et la queue pour l'UI."""
    log_format = '%(asctime)s - %(levelname)s - [%(threadName)s] - %(filename)s:%(lineno)d - %(message)s' # Utiliser un format plus détaillé
    date_format = '%Y-%m-%d %H:%M:%S'
    log_formatter = logging.Formatter(log_format, datefmt=date_format)

    log_file = 'greenhouse.log'

    # Configuration du logger racine
    root_logger = logging.getLogger()
    # Définir le niveau de log souhaité (ex: DEBUG pour tout voir pendant le dev)
    root_logger.setLevel(logging.DEBUG)

    # --- Vider les handlers existants (optionnel mais plus propre) ---
    # Si vous voulez être sûr qu'AUCUN autre handler (ex: d'une bibliothèque)
    # ne perturbe, vous pouvez vider les handlers avant d'ajouter les vôtres.
    # Attention : cela supprime TOUS les handlers préexistants.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    # ------------------------------------------------------------------

    # Handler pour écrire dans un fichier rotatif
    file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    file_handler.setLevel(logging.INFO) # Logguer INFO et plus sévère dans le fichier

    # Handler pour envoyer les logs à la queue de l'UI
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(log_formatter)
    queue_handler.setLevel(logging.DEBUG) # Envoyer DEBUG et plus sévère à l'UI

    # Handler pour afficher aussi dans la console (décommenté)
    console_handler = logging.StreamHandler(sys.stderr) # Ou sys.stdout
    console_handler.setFormatter(log_formatter)
    console_handler.setLevel(logging.DEBUG) # Afficher DEBUG et plus sévère dans la console

    # --- Ajout des handlers au root logger ---
    # Supprimer la condition "if not root_logger.hasHandlers():"
    root_logger.addHandler(file_handler)
    root_logger.addHandler(queue_handler)
    root_logger.addHandler(console_handler) # Ajouter le handler console ici

    logging.info("Logging initialisé par logger_setup (Fichier+UI+Console).")