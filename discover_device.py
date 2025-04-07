# discover_device.py
import asyncio
# Note: We'll keep using SmartDevice for discovery for now, but acknowledge the warning.
# A deeper refactor might involve kasa.iot classes later if needed.
from kasa import Discover, KasaException, SmartDevice

class DeviceDiscoverer:
    """
    Discovers Kasa smart devices on the local network.
    """
    async def discover(self) -> list[dict]:
        """
        Scans the network and returns information about discovered Kasa devices.

        Returns:
            list[dict]: A list of dictionaries, each representing a device.
                       Example:
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
                               'is_plug': False, # Added for clarity
                               'outlets': [
                                   {'index': 0, 'alias': 'Fan', 'is_on': True},
                                   {'index': 1, 'alias': 'Heat', 'is_on': True},
                                   {'index': 2, 'alias': 'Pump', 'is_on': True}
                               ]
                           },
                           # ... more devices
                       ]
        """
        print("Starting Kasa device discovery...")
        discovered_devices_info = []
        try:
            found_devices = await Discover.discover(timeout=7)
            if not found_devices:
                print("No Kasa devices found on the network.")
                return []

            print(f"Found {len(found_devices)} device(s). Fetching details...")

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

                    # --- Process outlets based on safely accessed type flags ---
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
                    # Log Kasa specific errors during update/processing
                    print(f"  - Kasa error processing device {ip}: {e}. Skipping.")
                except Exception as e:
                    # Log other unexpected errors during processing
                    print(f"  - Unexpected error processing device {ip}: {e}. Skipping.")

        except KasaException as e:
            # Log Kasa specific errors during the main discovery call
            print(f"Error during discovery phase: {e}")
        except Exception as e:
            # Log other unexpected errors during the main discovery call
            print(f"An unexpected error occurred during discovery phase: {e}")

        print("Discovery finished.")
        return discovered_devices_info

# Example usage (for testing this file directly)
# (No changes needed in the __main__ block below)
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