import asyncio
import sys
# Corrected Imports:
from kasa import KasaException
from kasa.iot.iotstrip import IotStrip

# --- Configuration ---
# Use the IP address you discovered for your power strip
DEVICE_IP = "192.168.0.98"
# -------------------

async def main():
    # Create an IotStrip object using the specific IP address
    strip = IotStrip(DEVICE_IP)

    try:
        print(f"Connecting to Kasa Power Strip at {DEVICE_IP}...")
        # Fetch the initial state from the device
        await strip.update()
        print(f"Successfully connected to: {strip.alias} (Model: {strip.model})")

        # Check if the strip has controllable children (outlets)
        if not strip.children:
            print("Error: This device does not report controllable child plugs.")
            return

        print(f"\nFound {len(strip.children)} outlets on the strip:")
        for i, plug in enumerate(strip.children):
            # Make sure child state is also updated (usually covered by parent update, but good practice)
            # await plug.update() # Often not needed if parent update() was called
            print(f"  - Outlet {i}: Alias='{plug.alias}', Currently On={plug.is_on}")

        # --- Control Examples ---

        # Example 1: Turn Outlet 0 OFF
        target_outlet_index = 0
        if target_outlet_index < len(strip.children):
            print(f"\nAttempting to turn Outlet {target_outlet_index} ('{strip.children[target_outlet_index].alias}') OFF...")
            try:
                await strip.children[target_outlet_index].turn_off()
                await strip.update() # Update state after command
                print(f"Outlet {target_outlet_index} is now On: {strip.children[target_outlet_index].is_on}")
            except KasaException as e:
                print(f"  Failed to turn off Outlet {target_outlet_index}: {e}")
        else:
            print(f"Error: Outlet index {target_outlet_index} is invalid.")

        # Pause briefly to observe the change if watching the strip
        await asyncio.sleep(3)

        # Example 2: Turn Outlet 0 ON
        if target_outlet_index < len(strip.children):
            print(f"\nAttempting to turn Outlet {target_outlet_index} ('{strip.children[target_outlet_index].alias}') ON...")
            try:
                await strip.children[target_outlet_index].turn_on()
                await strip.update() # Update state after command
                print(f"Outlet {target_outlet_index} is now On: {strip.children[target_outlet_index].is_on}")
            except KasaException as e:
                print(f"  Failed to turn on Outlet {target_outlet_index}: {e}")

        # Example 3: Turn ALL outlets ON
        print("\nAttempting to turn ALL outlets ON...")
        all_on_success = True
        for i, plug in enumerate(strip.children):
            try:
                if not plug.is_on: # Only turn on if it's currently off
                    print(f"  Turning on Outlet {i} ('{plug.alias}')...")
                    await plug.turn_on()
            except KasaException as e:
                print(f"  Failed to turn on Outlet {i}: {e}")
                all_on_success = False

        # Update state *after* trying to turn all on
        await strip.update()
        if all_on_success:
             print("Attempted to turn all outlets ON (or they were already on).")
        else:
             print("Attempted to turn all outlets ON, but some failed.")

        print("Current states:")
        for i, plug in enumerate(strip.children):
             print(f"  - Outlet {i}: Is On={plug.is_on}")


    except KasaException as e:
        print(f"\nError communicating with the Kasa strip at {DEVICE_IP}: {e}")
        print("Please ensure the device is powered on, connected to the network,")
        print("and that the IP address is correct.")
    except Exception as e:
        print(f"\nAn unexpected error occurred: {e}")

if __name__ == "__main__":
    # Basic check for Python 3.7+ for asyncio.run
    if sys.version_info < (3, 7):
         print("This script requires Python 3.7 or newer.")
    else:
        asyncio.run(main())