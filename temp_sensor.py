#temp_sensor.py

#!/usr/bin/env python3
from w1thermsensor import W1ThermSensor, SensorNotReadyError
import time

def main():
    try:
        sensors = W1ThermSensor.get_available_sensors()
        if not sensors:
            print("No DS18B20 sensors found. Please check your connections.")
            return
        
        while True:
            for sensor in sensors:
                try:
                    temperature = sensor.get_temperature()  # Default is Celsius
                    print(f"Sensor {sensor.id} Temperature: {temperature:.2f} °C")
                except SensorNotReadyError:
                    print(f"Sensor {sensor.id} not ready yet. Try again shortly.")
            time.sleep(2)  # Delay between readings

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == '__main__':
    main()
