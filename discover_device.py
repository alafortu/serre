# discover_device.py
# -----------------------------------------------------------
# Découverte des appareils intelligents Kasa sur le réseau local.
# Ce module utilise la bibliothèque kasa pour détecter les appareils,
# récupérer leurs informations et préparer leur contrôle.
# -----------------------------------------------------------
import asyncio
# Note: Nous continuons d'utiliser SmartDevice pour la découverte,
# mais une refonte future pourrait impliquer les classes kasa.iot.
# A deeper refactor might involve kasa.iot classes later if needed.
from kasa import Discover, KasaException, SmartDevice

class DeviceDiscoverer:
    """
    Découvre les appareils intelligents Kasa présents sur le réseau local.
    """
    async def discover(self) -> list[dict]:
        """
        Analyse le réseau et retourne une liste d'informations sur les appareils Kasa détectés.

        Retourne:
            list[dict]: Une liste de dictionnaires, chacun représentant un appareil.
                        Exemple:
                        [
                            {
                                'ip': '192.168.0.98',
                                'alias': 'TP-LINK_Power Strip_54A7',
                                'model': 'KP303(US)',
                                'mac': 'B0:95:75:XX:XX:XX',
                                'rssi': -55,
                                'hw_ver': '1.0',
                                'sw_ver': '1.0.5...',
                                'has_emeter': False,
                                'is_strip': True,
                                'is_plug': False,  # Indique si c'est une prise simple
                                'outlets': [
                                    {'index': 0, 'alias': 'Fan', 'is_on': True},
                                    {'index': 1, 'alias': 'Heat', 'is_on': True},
                                    {'index': 2, 'alias': 'Pump', 'is_on': True}
                                ]
                            },
                            # ... autres appareils
                        ]
        """
        # Démarre la découverte des appareils Kasa
        print("Starting Kasa device discovery...")
        discovered_devices_info = []
        try:
            found_devices = await Discover.discover(timeout=7)
            if not found_devices:
                print("No Kasa devices found on the network.")
                return []

            print(f"Found {len(found_devices)} device(s). Fetching details...")

            # Parcourt chaque appareil trouvé pour récupérer ses détails
            for ip, device in found_devices.items():
                try:
                    await device.update()

                    # --- Use getattr for safe access to potentially missing attributes ---
                    mac_addr = getattr(device, 'mac', 'N/A')
                    rssi_val = getattr(device, 'rssi', None) # None if not available
                    hw_info_dict = getattr(device, 'hw_info', {}) # Default to empty dict
                    sw_info_dict = getattr(device, 'sw_info', {}) # Default to empty dict
                    has_emeter_flag = getattr(device, 'has_emeter', False)
                    is_strip_flag = getattr(device, 'is_strip', False)
                    is_plug_flag = getattr(device, 'is_plug', False)
                    alias_val = getattr(device, 'alias', f"Unknown Device @ {ip}") # Use IP if no alias
                    model_val = getattr(device, 'model', 'Unknown Model')
                    # ------------------------------------------------------------------

                    # Extract versions safely from the dicts retrieved via getattr
                    hw_ver = hw_info_dict.get('hw_ver', 'N/A')
                    sw_ver = sw_info_dict.get('sw_ver', 'N/A')

                    device_info = {
                        'ip': ip,
                        'alias': alias_val,
                        'model': model_val,
                        'mac': mac_addr,
                        'rssi': rssi_val,
                        'hw_ver': hw_ver,
                        'sw_ver': sw_ver,
                        'has_emeter': has_emeter_flag,
                        'is_strip': is_strip_flag,
                        'is_plug': is_plug_flag, # Pass plug flag for controller hint
                        'outlets': []
                    }

                    # --- Traite les prises multiples ou simples selon le type détecté ---
                    if device_info['is_strip'] and hasattr(device, 'children') and device.children:
                        # Check hasattr for children too, just in case
                        for i, plug in enumerate(device.children):
                            device_info['outlets'].append({
                                'index': i,
                                'alias': getattr(plug, 'alias', f'Outlet {i}'), # Safe access for child alias
                                'is_on': getattr(plug, 'is_on', False)        # Safe access for child state
                            })
                    elif device_info['is_plug']:
                         # Safe access for plug state
                         is_on_state = getattr(device, 'is_on', False)
                         device_info['outlets'].append({
                                'index': 0,
                                'alias': device_info['alias'], # Use main alias
                                'is_on': is_on_state
                            })
                    # Add handling for other device types (bulbs, etc.) if needed

                    discovered_devices_info.append(device_info)
                    print(f"  - Added: {device_info['alias']} ({ip}) - MAC: {device_info['mac']} RSSI: {device_info['rssi']}")

                except KasaException as e:
                    # Gère les erreurs spécifiques à Kasa lors de la mise à jour ou du traitement
                    print(f"  - Kasa error processing device {ip}: {e}. Skipping.")
                except Exception as e:
                    # Gère les autres erreurs inattendues lors du traitement
                    print(f"  - Unexpected error processing device {ip}: {e}. Skipping.")

        except KasaException as e:
            # Gère les erreurs spécifiques à Kasa lors de la phase de découverte principale
            print(f"Error during discovery phase: {e}")
        except Exception as e:
            # Gère les autres erreurs inattendues lors de la phase de découverte principale
            print(f"An unexpected error occurred during discovery phase: {e}")

        print("Discovery finished.")
        return discovered_devices_info

# Example usage (for testing this file directly)
# (No changes needed in the __main__ block below)
# -----------------------------------------------------------
# Bloc de test pour exécuter la découverte directement
# -----------------------------------------------------------
if __name__ == "__main__":
    async def test_discovery():
        discoverer = DeviceDiscoverer()
        devices = await discoverer.discover()
        print("\n--- Discovered Devices ---")
        if devices:
            import json
            # Print the updated structure
            print(json.dumps(devices, indent=2))
        else:
            print("No devices were found or processed.")

    asyncio.run(test_discovery())