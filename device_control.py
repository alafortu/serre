# device_control.py
import asyncio
# Make sure these specific types are imported
from kasa import SmartDevice, KasaException, SmartStrip, SmartPlug

class DeviceController:
    """
    Controls a specific Kasa smart device (Plug or Strip).
    """
    # MODIFIED __init__ to accept hints
    def __init__(self, ip_address: str, is_strip: bool | None = None, is_plug: bool | None = None):
        """
        Initializes the controller for a device at the given IP address.

        Args:
            ip_address (str): The IP address of the Kasa device.
            is_strip (bool | None): Hint from discovery if the device is a strip.
            is_plug (bool | None): Hint from discovery if the device is a plug.
        """
        if not ip_address:
            raise ValueError("IP address cannot be empty.")
        self.ip_address = ip_address
        self._device = None # Placeholder for the connected device object
        # Store hints
        self._hint_is_strip = is_strip
        self._hint_is_plug = is_plug

    # MODIFIED _connect to use hints
    async def _connect(self) -> bool:
        """
        Establishes connection and updates the device state using type hints if possible.
        Returns True on success, False on failure.
        """
        print(f"Attempting to connect to {self.ip_address}...")
        DeviceClass = None # Variable to hold the specific class (SmartStrip, SmartPlug)

        # --- Use hints first ---
        if self._hint_is_strip:
            print("Type hint suggests: Smart Strip")
            DeviceClass = SmartStrip
        elif self._hint_is_plug:
            print("Type hint suggests: Smart Plug")
            DeviceClass = SmartPlug
        # Add elif for SmartBulb hint etc. if needed

        # --- Fallback: If no hint, try generic detection (less reliable) ---
        if not DeviceClass:
            print("No type hint provided, attempting generic detection...")
            try:
                # This part might still fail for some devices, hence the hint is preferred
                generic_device = SmartDevice(self.ip_address)
                await generic_device.update()
                if generic_device.is_strip:
                     print("Generic detection: Smart Strip")
                     DeviceClass = SmartStrip
                elif generic_device.is_plug:
                     print("Generic detection: Smart Plug")
                     DeviceClass = SmartPlug
                # Add elif for bulb etc.
            except KasaException as e:
                print(f"Error during generic detection for {self.ip_address}: {e}")
                # Fall through, DeviceClass is still None

        # --- Final Check and Connection ---
        if not DeviceClass:
             # Combine generic detection failure and no-hint case
             print(f"Could not determine specific device type for {self.ip_address}.")
             self._device = None
             return False

        # Now connect using the determined specific class
        try:
            print(f"Connecting as {DeviceClass.__name__}...")
            self._device = DeviceClass(self.ip_address)
            await self._device.update()
            # Check if connection really worked (alias is a good indicator)
            if self._device.alias:
                 print(f"Connected successfully to {self._device.alias} ({self._device.model}).")
                 return True
            else:
                 # Sometimes update() might not raise error but fails silently
                 print(f"Connection attempt as {DeviceClass.__name__} failed (no device details).")
                 self._device = None
                 return False
        except KasaException as e:
            print(f"Error connecting as {DeviceClass.__name__} to {self.ip_address}: {e}")
            self._device = None
            return False
        except Exception as e: # Catch other unexpected errors
            print(f"Unexpected error connecting as {DeviceClass.__name__} to {self.ip_address}: {e}")
            self._device = None
            return False

    async def get_outlet_state(self) -> list[dict] | None:
        """
        Gets the state of all controllable outlets on the device.

        Returns:
            list[dict] | None: A list of outlet states or None if connection fails or no outlets.
                              Example: [{'index': 0, 'alias': 'Fan', 'is_on': False}, ...]
        """
        # Attempt connection if not already connected or if connection check fails
        # Note: self._device might exist but be stale, update() handles refresh
        if not self._device:
            if not await self._connect():
                 print("Connection failed in get_outlet_state.")
                 return None # Connection failed

        try:
            await self._device.update() # Ensure fresh state
            outlets = []
            if self._device.is_strip and self._device.children:
                for i, plug in enumerate(self._device.children):
                     outlets.append({
                         'index': i,
                         'alias': plug.alias,
                         'is_on': plug.is_on
                     })
            elif self._device.is_plug:
                 outlets.append({
                         'index': 0,
                         'alias': self._device.alias,
                         'is_on': self._device.is_on
                     })
            # Add handling for other device types if needed
            return outlets
        except KasaException as e:
            print(f"Error getting outlet state for {self.ip_address}: {e}")
            # Invalidate connection on error? Maybe.
            # self._device = None
            return None
        except Exception as e:
             print(f"Unexpected error getting outlet state for {self.ip_address}: {e}")
             return None


    async def turn_outlet_on(self, index: int) -> bool:
        """
        Turns a specific outlet ON.

        Args:
            index (int): The index of the outlet to turn on (0 for single plugs).

        Returns:
            bool: True if successful, False otherwise.
        """
        if not self._device:
             if not await self._connect():
                  return False # Connection failed

        try:
            target_plug = None
            if self._device.is_strip and self._device.children and 0 <= index < len(self._device.children):
                 target_plug = self._device.children[index]
            elif self._device.is_plug and index == 0:
                 target_plug = self._device # Control the plug itself

            if target_plug:
                print(f"Turning ON outlet {index} ('{target_plug.alias}')...")
                await target_plug.turn_on()
                await self._device.update() # Verify state change by updating parent
                # Re-access child state after update for verification
                if self._device.is_strip:
                    is_now_on = self._device.children[index].is_on
                else: # is_plug
                    is_now_on = self._device.is_on
                print(f"Outlet {index} is now On: {is_now_on}")
                return is_now_on # Return True if it's actually on
            else:
                 print(f"Error: Invalid outlet index {index} for device {self.ip_address}.")
                 return False
        except KasaException as e:
             print(f"Error turning ON outlet {index} for {self.ip_address}: {e}")
             return False
        except Exception as e:
             print(f"Unexpected error turning ON outlet {index} for {self.ip_address}: {e}")
             return False

    async def turn_outlet_off(self, index: int) -> bool:
        """
        Turns a specific outlet OFF.

        Args:
            index (int): The index of the outlet to turn off (0 for single plugs).

        Returns:
            bool: True if successful (outlet is off), False otherwise.
        """
        if not self._device:
            if not await self._connect():
                 return False # Connection failed

        try:
            target_plug = None
            if self._device.is_strip and self._device.children and 0 <= index < len(self._device.children):
                 target_plug = self._device.children[index]
            elif self._device.is_plug and index == 0:
                 target_plug = self._device # Control the plug itself

            if target_plug:
                print(f"Turning OFF outlet {index} ('{target_plug.alias}')...")
                await target_plug.turn_off()
                await self._device.update() # Verify state change by updating parent
                # Re-access child state after update for verification
                if self._device.is_strip:
                    is_now_on = self._device.children[index].is_on
                else: # is_plug
                    is_now_on = self._device.is_on
                print(f"Outlet {index} is now On: {is_now_on}")
                return not is_now_on # Return True if it's actually off
            else:
                 print(f"Error: Invalid outlet index {index} for device {self.ip_address}.")
                 return False
        except KasaException as e:
             print(f"Error turning OFF outlet {index} for {self.ip_address}: {e}")
             return False
        except Exception as e:
             print(f"Unexpected error turning OFF outlet {index} for {self.ip_address}: {e}")
             return False

    async def turn_all_outlets_on(self) -> bool:
        """Turns all controllable outlets ON. Returns True if all attempts were made."""
        if not self._device:
            if not await self._connect():
                 return False

        if not self._device.is_strip:
            print("Turning on single plug...")
            return await self.turn_outlet_on(0)

        if self._device.is_strip and self._device.children:
            print("Turning all outlets ON...")
            # Run tasks concurrently
            tasks = [plug.turn_on() for plug in self._device.children]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Check for errors
            success = True
            for i, result in enumerate(results):
                 if isinstance(result, Exception):
                     print(f" Error turning on outlet {i}: {result}")
                     success = False

            await self._device.update() # Update state after attempts
            if success:
                 print("Finished turning all outlets on (check states above for specifics).")
            else:
                 print("Finished turning all outlets on, but some errors occurred.")
            return success # Indicate if all commands were sent without immediate error
        return False # Should not happen if is_strip is True


    async def turn_all_outlets_off(self) -> bool:
        """Turns all controllable outlets OFF. Returns True if all attempts were made."""
        if not self._device:
            if not await self._connect():
                 return False

        if not self._device.is_strip:
             print("Turning off single plug...")
             return await self.turn_outlet_off(0)

        if self._device.is_strip and self._device.children:
            print("Turning all outlets OFF...")
            # Run tasks concurrently
            tasks = [plug.turn_off() for plug in self._device.children]
            results = await asyncio.gather(*tasks, return_exceptions=True)

             # Check for errors
            success = True
            for i, result in enumerate(results):
                 if isinstance(result, Exception):
                     print(f" Error turning off outlet {i}: {result}")
                     success = False

            await self._device.update() # Update state after attempts
            if success:
                print("Finished turning all outlets off (check states above for specifics).")
            else:
                print("Finished turning all outlets off, but some errors occurred.")
            return success # Indicate if all commands were sent without immediate error
        return False # Should not happen if is_strip is True


# Example usage (for testing this file directly)
if __name__ == "__main__":
    async def test_control():
        # --- IMPORTANT: Replace with a known device IP for testing ---
        TEST_IP = "192.168.0.98" # Or leave empty to skip test
        # -------------------------------------------------------------
        if not TEST_IP:
            print("Please set the TEST_IP variable in device_control.py for testing.")
            return

        # For testing, we might not know the type, so don't pass hints here
        # Rely on the fallback detection within _connect for this test scenario
        controller = DeviceController(TEST_IP)

        print("\n--- Getting Initial State ---")
        states = await controller.get_outlet_state()
        if states:
            print(f"Current outlet states: {states}")
        else:
             print("Could not get initial state. Aborting test.")
             return # Stop test if initial state fails

        # Test turning one outlet off (use index 0)
        print("\n--- Testing Turn OFF (Outlet 0) ---")
        await controller.turn_outlet_off(0)
        await asyncio.sleep(2)

        # Test turning one outlet on (use index 0)
        print("\n--- Testing Turn ON (Outlet 0) ---")
        await controller.turn_outlet_on(0)
        await asyncio.sleep(2)

        # Test turning all off (if it's a strip)
        if controller._device and controller._device.is_strip:
            print("\n--- Testing Turn ALL OFF ---")
            await controller.turn_all_outlets_off()
            await asyncio.sleep(2)

             # Test turning all on (if it's a strip)
            print("\n--- Testing Turn ALL ON ---")
            await controller.turn_all_outlets_on()


    print("Running device_control.py test...")
    asyncio.run(test_control())
    print("Test finished.")