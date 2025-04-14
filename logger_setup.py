# logger_setup.py
import logging
import queue
from logging.handlers import RotatingFileHandler
import tkinter as tk

# Classe pour rediriger les logs vers un widget Text de Tkinter via une queue
class QueueHandler(logging.Handler):
    def __init__(self, log_queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        self.log_queue.put(self.format(record))

def setup_logging(log_queue):
    """Configure le logging vers un fichier et la queue pour l'UI."""
    log_formatter = logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    log_file = 'greenhouse.log'

    # Handler pour écrire dans un fichier rotatif (max 1MB, 3 backups)
    file_handler = RotatingFileHandler(log_file, maxBytes=1024*1024, backupCount=3, encoding='utf-8')
    file_handler.setFormatter(log_formatter)

    # Handler pour envoyer les logs à la queue de l'UI
    queue_handler = QueueHandler(log_queue)
    queue_handler.setFormatter(log_formatter)

    # Configuration du logger racine
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO) # Niveau de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)

    # Éviter d'ajouter les handlers plusieurs fois si la fonction est appelée à nouveau
    if not root_logger.hasHandlers():
        root_logger.addHandler(file_handler)
        root_logger.addHandler(queue_handler)
        # Optionnel: Handler pour afficher aussi dans la console
        # console_handler = logging.StreamHandler()
        # console_handler.setFormatter(log_formatter)
        # root_logger.addHandler(console_handler)

    logging.info("Logging initialisé.")