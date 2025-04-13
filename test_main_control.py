# main.py
import asyncio
import datetime
import sys
import time
from w1thermsensor import W1ThermSensor, SensorNotReadyError
from discover_device import DeviceDiscoverer
from device_control import DeviceController

TEMPERATURE_THRESHOLD = 30.0  # °C
TARGET_OUTLET_INDEX = 2       # Plug index to control
TEMP_SENSOR_INDEX = 1         # Index of sensor to monitor

async def main():
    print("=== Temperature-Controlled Smart Plug ===")
    print(f"Starting at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Monitoring sensor #{TEMP_SENSOR_INDEX}...")

    # Get list of available temperature sensors
    try:
        sensors = W1ThermSensor.get_available_sensors()
        if len(sensors) <= TEMP_SENSOR_INDEX:
            print(f"Not enough sensors found. Expected index {TEMP_SENSOR_INDEX} to exist.")
            return
        sensor = sensors[TEMP_SENSOR_INDEX]
    except Exception as e:
        print(f"Failed to initialize temperature sensor: {e}")
        return

    # Discover smart plugs
    discoverer = DeviceDiscoverer()
    print("Discovering Kasa devices...")
    devices = await discoverer.discover()

    if not devices:
        print("No smart devices found.")
        return

    # Pick first plug/strip with at least 3 outlets
    target_device = None
    for d in devices:
        if len(d.get("outlets", [])) > TARGET_OUTLET_INDEX:
            target_device = d
            break

    if not target_device:
        print(f"No device with at least {TARGET_OUTLET_INDEX + 1} outlets found.")
        return

    controller = DeviceController(
        target_device['ip'],
        is_strip=target_device.get('is_strip'),
        is_plug=target_device.get('is_plug')
    )

    print(f"Monitoring temp -> controlling: {target_device['alias']} @ outlet {TARGET_OUTLET_INDEX}")

    try:
        while True:
            try:
                temp = sensor.get_temperature()
                print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Sensor {sensor.id} Temp: {temp:.2f}°C")

                if temp > TEMPERATURE_THRESHOLD:
                    print(f"Temperature exceeds {TEMPERATURE_THRESHOLD}°C! Turning OFF outlet {TARGET_OUTLET_INDEX}...")
                    await controller.turn_outlet_off(TARGET_OUTLET_INDEX)
                else:
                    print("Temperature is safe. No action needed.")

            except SensorNotReadyError:
                print("Sensor not ready. Skipping this cycle.")

            await asyncio.sleep(5)  # Check every 5 seconds

    except KeyboardInterrupt:
        print("\nMonitoring stopped by user.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        print(f"An error occurred: {e}")
