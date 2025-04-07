import asyncio
from kasa import Discover, SmartDeviceException

async def main():
    print("Discovering Kasa devices on the network...")
    try:
        # Discover all devices on the network
        found_devices = await Discover.discover()

        if not found_devices:
            print("No Kasa devices found on the network.")
            return

        print(f"Found {len(found_devices)} device(s):")
        for ip, dev in found_devices.items():
            print(f"  - IP: {ip}, Alias: {dev.alias}, Type: {type(dev)}")

        # Example: Control the first device found
        # You might want more sophisticated logic to select the correct device
        first_ip = list(found_devices.keys())[0]
        dev_to_control = found_devices[first_ip]
        print(f"\nControlling device: {dev_to_control.alias} at {first_ip}")

        print("Turning device on...")
        await dev_to_control.turn_on()
        print("Updating device state...")
        await dev_to_control.update()
        print(f"Device state updated. Is on: {dev_to_control.is_on}")

    except SmartDeviceException as e:
        print(f"Error during Kasa device communication: {e}")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    asyncio.run(main())