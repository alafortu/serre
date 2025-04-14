# light_sensor.py (Version modifiée pour utiliser la bibliothèque Adafruit)
#!/usr/bin/env python3
"""
Module light_sensor.py (Version Adafruit)

Utilise la bibliothèque adafruit_circuitpython_bh1750 pour gérer
deux capteurs BH1750 sur un Raspberry Pi via la couche Blinka.
Détecte les capteurs aux adresses spécifiées (par défaut 0x23 et 0x5C).
"""

import time
import logging
try:
    import board # Fourni par adafruit-blinka
    import busio # Fourni par adafruit-blinka
    import adafruit_bh1750
    ADAFRUIT_LIBS_AVAILABLE = True
except ImportError:
    logging.error("Bibliothèques Adafruit (blinka, adafruit_bh1750) non trouvées. Veuillez les installer.")
    ADAFRUIT_LIBS_AVAILABLE = False
except RuntimeError as e:
    # Blinka peut lever une RuntimeError si les prérequis matériels/OS ne sont pas remplis
    logging.error(f"Erreur RuntimeError lors de l'importation des bibliothèques Adafruit: {e}")
    logging.error("Assurez-vous que I2C/SPI sont activés et que les permissions sont correctes.")
    ADAFRUIT_LIBS_AVAILABLE = False


class BH1750Manager:
    def __init__(self, bus_number: int = 1, addresses: list = [0x23, 0x5C]):
        """
        Initialise le manager pour les capteurs BH1750 via Adafruit Blinka.

        Args:
            bus_number (int): Ignoré (Blinka utilise board.SCL/SDA). Reste pour compatibilité.
            addresses (list): Liste des adresses I²C à scanner.
        """
        self.addresses = addresses
        self.sensors = {} # Dictionnaire pour stocker les instances de capteurs {addr_int: sensor_instance}
        self.i2c = None

        if not ADAFRUIT_LIBS_AVAILABLE:
            logging.error("Initialisation BH1750Manager échouée: Bibliothèques Adafruit manquantes.")
            return # Ne pas continuer si les libs ne sont pas là

        try:
            # Initialise le bus I2C via Blinka (utilise les pins par défaut du Pi)
            self.i2c = busio.I2C(board.SCL, board.SDA)
            logging.info("Bus I2C initialisé via Adafruit Blinka.")
            self.scan_sensors()
        except ValueError as e:
            # Souvent une erreur si SCL/SDA ne sont pas trouvés (I2C désactivé?)
             logging.error(f"Erreur d'initialisation I2C (ValueError): {e}. Vérifiez que I2C est activé.")
        except RuntimeError as e:
             logging.error(f"Erreur d'initialisation I2C (RuntimeError): {e}. Problème matériel ou de permission ?")
        except Exception as e:
             logging.error(f"Erreur inattendue lors de l'initialisation I2C: {e}")


    def scan_sensors(self):
        """
        Scanne le bus I²C pour les adresses spécifiées et initialise un objet
        adafruit_bh1750 pour chaque capteur détecté.
        """
        if not self.i2c:
             logging.warning("Scan annulé: Bus I2C non initialisé.")
             return

        self.sensors = {} # Réinitialiser en cas de re-scan
        logging.info(f"Scan des adresses BH1750: { [hex(a) for a in self.addresses] }")
        for addr in self.addresses:
            try:
                # Tente de créer une instance du capteur Adafruit BH1750
                sensor_instance = adafruit_bh1750.BH1750(self.i2c, address=addr)
                # Une lecture test n'est pas forcément nécessaire, l'init peut suffire
                # ou la première lecture échouera si problème. On peut ajouter si besoin:
                # _ = sensor_instance.lux

                self.sensors[addr] = sensor_instance # Clé = adresse en int
                logging.info(f"Capteur Adafruit BH1750 détecté et initialisé à l'adresse {hex(addr)}")
            except ValueError:
                # Le constructeur Adafruit lève ValueError si le device n'est pas trouvé
                logging.warning(f"Aucun capteur BH1750 détecté à l'adresse {hex(addr)} (ValueError).")
            except Exception as e:
                logging.error(f"Erreur lors de la tentative d'initialisation du capteur {hex(addr)}: {e}")

    def get_active_sensors(self) -> list:
        """
        Retourne la liste des adresses (en entier) des capteurs détectés.
        """
        return list(self.sensors.keys())

    def read_sensor(self, address: int) -> float | None:
        """
        Lit la valeur en lux du capteur situé à l'adresse spécifiée.

        Args:
            address (int): Adresse I²C (entier) du capteur à lire.

        Returns:
            float | None: La luminosité en lux ou None en cas d'erreur.
        """
        if address in self.sensors:
            try:
                lux_value = self.sensors[address].lux
                logging.debug(f"Lecture capteur {hex(address)}: {lux_value:.2f} Lux")
                return lux_value
            except Exception as e:
                # Peut être une OSError si le capteur se déconnecte
                logging.error(f"Erreur lors de la lecture du capteur {hex(address)}: {e}")
                # Optionnel: tenter de réinitialiser ou supprimer le capteur de la liste?
                # del self.sensors[address] # Ou marquer comme défaillant
                return None
        else:
            logging.warning(f"Tentative de lecture d'un capteur non disponible/détecté à l'adresse {hex(address)}")
            return None

    def read_all_sensors(self) -> dict:
        """
        Lit la valeur de luminosité en lux pour tous les capteurs détectés.

        Returns:
            dict: Un dictionnaire dont les clés sont les adresses (format hexadécimal string)
                  et les valeurs sont la lecture en lux (float) ou None si erreur.
                  (Clé hex pour compatibilité avec greenhouse_app.py)
        """
        readings = {}
        # Itérer sur une copie des clés au cas où une lecture échoue et modifie self.sensors
        for addr in list(self.sensors.keys()):
            lux_value = self.read_sensor(addr) # Utilise la méthode qui gère déjà les erreurs
            readings[hex(addr)] = lux_value # Clé hexadécimale
        return readings

# --- Bloc de test (optionnel, mis à jour pour Adafruit) ---
if __name__ == '__main__':
    # Configurer un logging basique pour le test direct
    log_format = '%(asctime)s - %(levelname)s - %(message)s'
    logging.basicConfig(level=logging.INFO, format=log_format)

    if ADAFRUIT_LIBS_AVAILABLE:
        print("Initialisation du BH1750Manager (Adafruit)...")
        manager = BH1750Manager() # Utilise les adresses par défaut [0x23, 0x5C]
        active = manager.get_active_sensors()
        print("Capteurs actifs détectés aux adresses (int):", active)
        print("Capteurs actifs détectés aux adresses (hex):", [hex(addr) for addr in active])

        if active:
            print("Début des lectures (Ctrl+C pour arrêter) :")
            try:
                while True:
                    all_readings = manager.read_all_sensors()
                    # Formatage pour affichage propre
                    reading_str = ", ".join([f"{addr_hex}: {lux:.2f} Lux" if lux is not None else f"{addr_hex}: Erreur"
                                             for addr_hex, lux in all_readings.items()])
                    print(f"Lectures : {reading_str}")
                    time.sleep(2)
            except KeyboardInterrupt:
                print("\nArrêt du programme de test.")
        else:
            print("Aucun capteur actif détecté, impossible de démarrer les lectures.")
    else:
        print("Impossible d'exécuter le test: Bibliothèques Adafruit non disponibles.")