# temp_sensor_wrapper.py
import logging
from w1thermsensor import W1ThermSensor, SensorNotReadyError, NoSensorFoundError

class TempSensorManager:
    def __init__(self):
        self.sensors = []
        self.discover_sensors()

    def discover_sensors(self):
        """Découvre les capteurs DS18B20 connectés."""
        try:
            self.sensors = W1ThermSensor.get_available_sensors()
            if self.sensors:
                logging.info(f"Capteurs de température 1-Wire trouvés : {[s.id for s in self.sensors]}")
            else:
                logging.warning("Aucun capteur de température 1-Wire DS18B20 trouvé.")
        except NoSensorFoundError:
             logging.warning("Aucun capteur de température 1-Wire DS18B20 trouvé (NoSensorFoundError). Vérifiez les connexions et l'activation 1-Wire.")
        except Exception as e:
            logging.error(f"Erreur lors de la découverte des capteurs 1-Wire: {e}")
            self.sensors = [] # Assurer que la liste est vide en cas d'erreur majeure

    def get_sensor_ids(self) -> list[str]:
        """Retourne les IDs des capteurs découverts."""
        return [sensor.id for sensor in self.sensors]

    def read_all_temperatures(self) -> dict[str, float | None]:
        """Lit la température de tous les capteurs découverts."""
        readings = {}
        if not self.sensors:
             # Tenter une nouvelle découverte si aucun capteur n'était connu
             logging.debug("Tentative de redécouverte des capteurs de température.")
             self.discover_sensors()
             if not self.sensors:
                 logging.warning("Impossible de lire les températures, aucun capteur trouvé.")
                 return {} # Retourner un dict vide si toujours aucun capteur

        for sensor in self.sensors:
            try:
                temperature = sensor.get_temperature() # Défaut Celsius
                readings[sensor.id] = round(temperature, 2)
                logging.debug(f"Lecture capteur {sensor.id}: {temperature:.2f}°C")
            except SensorNotReadyError:
                logging.warning(f"Capteur de température {sensor.id} non prêt.")
                readings[sensor.id] = None
            except Exception as e:
                logging.error(f"Erreur de lecture du capteur de température {sensor.id}: {e}")
                readings[sensor.id] = None
                # Si une erreur survient, on pourrait essayer de redécouvrir au prochain cycle
                # ou marquer le capteur comme problématique. Pour l'instant, on retourne None.
        return readings

# Test simple
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    manager = TempSensorManager()
    print("Capteurs trouvés:", manager.get_sensor_ids())
    if manager.sensors:
      print("Lectures:", manager.read_all_temperatures())