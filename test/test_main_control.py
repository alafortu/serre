# main.py
import asyncio
import datetime
import sys
import time
from w1thermsensor import W1ThermSensor, SensorNotReadyError
from discover_device import DeviceDiscoverer
from device_control import DeviceController

TEMP_OFF_THRESHOLD = 27.0  # °C — Turn OFF if any sensor goes above this
TEMP_ON_THRESHOLD = 26.0   # °C — Turn ON only if all sensors are below this
TARGET_OUTLET_INDEX = 2    # Index of outlet to control

async def main():
    print("=== Temperature-Controlled Smart Plug with Hysteresis ===")
    print(f"Started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Get list of available temperature sensors
    try:
        sensors = W1ThermSensor.get_available_sensors()
        if not sensors:
            print("No temperature sensors found.")
            return
        print(f"Found {len(sensors)} temperature sensor(s).")
    except Exception as e:
        print(f"Failed to initialize temperature sensors: {e}")
        return

    # Discover Kasa devices
    discoverer = DeviceDiscoverer()
    print("Discovering Kasa devices...")
    devices = await discoverer.discover()

    if not devices:
        print("No smart devices found.")
        return

    # Pick a device with at least TARGET_OUTLET_INDEX
    target_device = next((d for d in devices if len(d.get("outlets", [])) > TARGET_OUTLET_INDEX), None)

    if not target_device:
        print(f"No device with at least outlet index {TARGET_OUTLET_INDEX} found.")
        return

    controller = DeviceController(
        target_device['ip'],
        is_strip=target_device.get('is_strip'),
        is_plug=target_device.get('is_plug')
    )

    print(f"Controlling device: {target_device['alias']} (IP: {target_device['ip']})")
    print(f"Monitoring ALL sensors. Outlet index {TARGET_OUTLET_INDEX} will be managed based on temperature.")

    is_outlet_on = True  # Assume it's on initially

    try:
        while True:
            try:
                temps = []
                for sensor in sensors:
                    try:
                        t = sensor.get_temperature()
                        temps.append(t)
                        print(f"Sensor {sensor.id}: {t:.2f}°C")
                    except SensorNotReadyError:
                        print(f"Sensor {sensor.id} not ready. Skipping.")
                
                if not temps:
                    print("No temperature readings available. Skipping this cycle.")
                    await asyncio.sleep(5)
                    continue

                max_temp = max(temps)
                print(f"Max temperature detected: {max_temp:.2f}°C")

                # Turn OFF if any temp > TEMP_OFF_THRESHOLD
                if max_temp > TEMP_OFF_THRESHOLD and is_outlet_on:
                    print(f"Threshold exceeded! Turning OFF outlet {TARGET_OUTLET_INDEX}.")
                    success = await controller.turn_outlet_off(TARGET_OUTLET_INDEX)
                    if success:
                        is_outlet_on = False

                # Turn ON only if all temps <= TEMP_ON_THRESHOLD
                elif max_temp <= TEMP_ON_THRESHOLD and not is_outlet_on:
                    print(f"Temperature is safe. Turning ON outlet {TARGET_OUTLET_INDEX}.")
                    success = await controller.turn_outlet_on(TARGET_OUTLET_INDEX)
                    if success:
                        is_outlet_on = True
                else:
                    print("No state change needed.")

            except Exception as e:
                print(f"Unexpected error in monitoring loop: {e}")

            await asyncio.sleep(5)  # Interval between checks

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred: {e}")
