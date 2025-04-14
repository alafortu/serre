#!/usr/bin/env python3
"""
Module light_sensor.py

Ce module définit la classe BH1750Manager qui utilise la bibliothèque bh1750 pour gérer
deux capteurs BH1750 sur un Raspberry Pi. La classe scanne automatiquement le bus I²C
pour détecter les capteurs aux adresses spécifiées (par défaut 0x23 et 0x5C) et offre
des méthodes pour lire la luminosité (en lux) sur demande.

Installation requise:
    pip install bh1750

Exemple d'utilisation:
    from light_sensor import BH1750Manager
    manager = BH1750Manager()
    active_sensors = manager.get_active_sensors()
    print("Capteurs actifs :", active_sensors)
    # Pour lire tous les capteurs :
    readings = manager.read_all_sensors()
    print("Lectures :", readings)
"""

import time
import smbus
from bh1750 import BH1750

class BH1750Manager:
    def __init__(self, bus_number: int = 1, addresses: list = [0x23, 0x5C]):
        """
        Initialise le manager pour les capteurs BH1750.

        Args:
            bus_number (int): Numéro du bus I²C (par défaut 1 sur Raspberry Pi).
            addresses (list): Liste des adresses I²C à scanner pour détecter les capteurs.
                              Par défaut, [0x23, 0x5C].
        """
        self.bus = smbus.SMBus(bus_number)
        self.addresses = addresses
        self.sensors = {}
        self.scan_sensors()

    def scan_sensors(self):
        """
        Scanne le bus I²C pour les adresses spécifiées et initialise un objet BH1750
        pour chaque capteur détecté.
        """
        for addr in self.addresses:
            try:
                # Création d'une instance du capteur BH1750 pour l'adresse donnée.
                sensor = BH1750(self.bus, address=addr)
                # Essai de lecture pour s'assurer que le capteur répond.
                _ = sensor.lux  
                self.sensors[addr] = sensor
                print(f"Capteur BH1750 détecté à l'adresse {hex(addr)}")
            except Exception as e:
                print(f"Aucun capteur détecté à l'adresse {hex(addr)} : {e}")

    def get_active_sensors(self) -> list:
        """
        Retourne la liste des adresses (en entier) des capteurs détectés.
        """
        return list(self.sensors.keys())

    def read_sensor(self, address: int) -> float:
        """
        Lit la valeur en lux du capteur situé à l'adresse spécifiée.

        Args:
            address (int): Adresse I²C du capteur à lire.

        Returns:
            float: La luminosité en lux ou None en cas d'erreur ou si le capteur n'est pas actif.
        """
        if address in self.sensors:
            try:
                return self.sensors[address].lux
            except Exception as e:
                print(f"Erreur lors de la lecture du capteur {hex(address)}: {e}")
                return None
        else:
            print(f"Capteur non disponible à l'adresse {hex(address)}")
            return None

    def read_all_sensors(self) -> dict:
        """
        Lit la valeur de luminosité en lux pour tous les capteurs détectés.

        Returns:
            dict: Un dictionnaire dont les clés sont les adresses (format hexadécimal) et
                  les valeurs sont la lecture en lux.
        """
        readings = {}
        for addr, sensor in self.sensors.items():
            try:
                readings[hex(addr)] = sensor.lux
            except Exception as e:
                print(f"Erreur lors de la lecture du capteur {hex(addr)}: {e}")
                readings[hex(addr)] = None
        return readings


if __name__ == '__main__':
    print("Initialisation du BH1750Manager...")
    manager = BH1750Manager()
    active = manager.get_active_sensors()
    print("Capteurs actifs détectés:", [hex(addr) for addr in active])
    print("Début des lectures (Ctrl+C pour arrêter) :")
    try:
        while True:
            all_readings = manager.read_all_sensors()
            print("Lectures :", all_readings)
            time.sleep(1)
    except KeyboardInterrupt:
        print("Arrêt du programme.")

