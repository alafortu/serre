# main.py
import datetime
import asyncio
import sys
from discover_device import DeviceDiscoverer
from device_control import DeviceController


async def interactive_control(controller: DeviceController, device_info: dict):
    """Handles the interactive menu for controlling a selected device."""
    while True:
        print("\n--- Device Control Menu ---")
        print(f"Controlling: {device_info['alias']} ({device_info['ip']})")

        # Get current state to display
        current_outlets = await controller.get_outlet_state()
        if not current_outlets:
            print("Could not retrieve outlet state. Returning to device selection.")
            # Maybe the connection dropped, invalidate controller's device object
            controller._device = None
            return

        print("Current Outlet States:")
        for outlet in current_outlets:
            status = "ON" if outlet['is_on'] else "OFF"
            print(f"  {outlet['index']}: {outlet['alias']} - {status}")

        # Display options
        print("\nAvailable Actions:")
        print("  L: List outlets (refresh state)")
        if len(current_outlets) > 1: # Offer 'all' options only for strips/multi-outlet devices
            print("  AON: Turn ALL outlets ON")
            print("  AOFF: Turn ALL outlets OFF")
        for outlet in current_outlets:
             print(f"  {outlet['index']}ON: Turn outlet {outlet['index']} ON")
             print(f"  {outlet['index']}OFF: Turn outlet {outlet['index']} OFF")
        print("  B: Back to device selection")
        print("  Q: Quit")

        choice = input("Enter action: ").strip().upper()

        if choice == 'L':
            # State is already refreshed at the start of the loop
            print("Refreshing state...") # Give feedback
            continue
        elif choice == 'AON' and len(current_outlets) > 1:
            await controller.turn_all_outlets_on()
        elif choice == 'AOFF' and len(current_outlets) > 1:
            await controller.turn_all_outlets_off()
        elif choice == 'B':
            print("Returning to device selection...")
            return
        elif choice == 'Q':
            print("Exiting.")
            sys.exit(0)
        else:
            # Check for individual outlet control (e.g., "0ON", "1OFF")
            try:
                # Find the split between index number and ON/OFF
                split_index = -1
                for i, char in enumerate(choice):
                    if not char.isdigit():
                        split_index = i
                        break

                # Ensure the action part exists and is valid (ON/OFF)
                if split_index > 0 and choice[split_index:] in ('ON', 'OFF'):
                    outlet_index = int(choice[:split_index])
                    action = choice[split_index:]

                    # Check if the chosen index is valid for this device
                    valid_index = any(o['index'] == outlet_index for o in current_outlets)

                    if valid_index:
                        if action == 'ON':
                            await controller.turn_outlet_on(outlet_index)
                        elif action == 'OFF':
                            await controller.turn_outlet_off(outlet_index)
                    else:
                         print(f"Invalid outlet index: {outlet_index}")

                else:
                     # Handle cases like just "0" or "ON" or "0XYZ"
                     print(f"Invalid action format: '{choice}'. Use N_ON or N_OFF (e.g., 0ON, 1OFF).")

            except ValueError: # Handles if int() fails
                print(f"Invalid action format: {choice}")
            except Exception as e:
                 print(f"An error occurred processing action {choice}: {e}")

        # Pause slightly after an action to allow reading output/device reacting
        await asyncio.sleep(1)


async def main():
    """Main application flow."""
    discoverer = DeviceDiscoverer()

    while True:
        devices = await discoverer.discover()

        if not devices:
            print("\nNo Kasa devices found on the network.")
            print("Ensure devices are powered on and on the same network as this computer.")
            print("Retrying discovery in 10 seconds (Ctrl+C to stop)...")
            try:
                 await asyncio.sleep(10)
                 continue # Retry discovery
            except asyncio.CancelledError:
                 print("\nDiscovery cancelled. Exiting.")
                 break
            except KeyboardInterrupt: # Catch Ctrl+C during sleep
                 print("\nDiscovery cancelled. Exiting.")
                 break

        print("\n--- Discovered Devices ---")
        for i, device in enumerate(devices):
            outlet_count = len(device.get('outlets', []))
            print(f"  {i}: {device['alias']} ({device['model']}) - IP: {device['ip']} [{outlet_count} outlets]")

        print("\nSelect a device number to control, 'R' to rescan, or 'Q' to quit.")
        choice = input("Enter selection: ").strip().upper()

        if choice == 'Q':
            print("Exiting.")
            break
        elif choice == 'R':
            print("Rescanning...")
            continue # Loop back to discovery
        else:
            try:
                device_index = int(choice)
                if 0 <= device_index < len(devices):
                    selected_device = devices[device_index]
                    print(f"\nSelected: {selected_device['alias']}")

                    # ---> Pass the hints when creating the controller <---
                    controller = DeviceController(
                        selected_device['ip'],
                        is_strip=selected_device.get('is_strip'), # Use .get for safety
                        is_plug=selected_device.get('is_plug')   # Use .get for safety
                    )
                    # ----------------------------------------------------

                    # Enter the interactive control loop for this device
                    await interactive_control(controller, selected_device)
                else:
                    print("Invalid device number.")
            except ValueError:
                print("Invalid input. Please enter a number, 'R', or 'Q'.")
            except Exception as e:
                 print(f"An unexpected error occurred in the main loop: {e}")

        # Brief pause before looping back to discovery menu (if user chose 'B' or an error occurred)
        await asyncio.sleep(0.5)


if __name__ == "__main__":
    # Set UTF-8 encoding for Windows console if needed (might help with special chars in names)
    if sys.platform == "win32":
        try:
            # Note: This might not always work depending on terminal emulator
            sys.stdout.reconfigure(encoding='utf-8')
            sys.stderr.reconfigure(encoding='utf-8')
        except AttributeError:
             # sys.stdout might not have reconfigure (e.g., in some IDE consoles)
             pass

    print("Kasa Device Control Utility")
    print(f"Running at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} EDT") 
    print("-" * 30)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user. Exiting.")
    except Exception as e:
         print(f"\nA critical error occurred: {e}")
    finally:
         print("\nApplication finished.")