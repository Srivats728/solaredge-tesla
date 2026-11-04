"""
Tesla Vehicle Client (TeslaPy wrapper)
Manages vehicle communication, smart wake-up, charge monitoring, and amp adjustments.
"""

import logging
import time
from typing import Optional, Dict, Any
import teslapy

logger = logging.getLogger(__name__)


class TeslaController:
    """Controls Tesla Model 3 charging using TeslaPy."""

    def __init__(self, email: str, vin: Optional[str] = None):
        self.email = email
        self.vin = vin
        self.tesla: Optional[teslapy.Tesla] = None
        self.vehicle: Optional[teslapy.Vehicle] = None
        self._last_known_state: Optional[Dict[str, Any]] = None
        self._last_wake_attempt: float = 0.0

    def initialize(self) -> bool:
        """Initialize Tesla client and find target vehicle."""
        try:
            logger.info("Initializing TeslaPy client for %s...", self.email)
            self.tesla = teslapy.Tesla(self.email)

            if not self.tesla.authorized:
                logger.error("Tesla client is not authorized! Please run authenticate_tesla.py first.")
                return False

            vehicles = self.tesla.vehicle_list()
            if not vehicles:
                logger.error("No vehicles found on Tesla account %s.", self.email)
                return False

            if self.vin:
                match = [v for v in vehicles if v.get('vin') == self.vin or v.get('display_name') == self.vin]
                if match:
                    self.vehicle = match[0]
                else:
                    logger.warning("VIN/Name '%s' not found. Defaulting to first vehicle: %s",
                                   self.vin, vehicles[0].get('display_name'))
                    self.vehicle = vehicles[0]
            else:
                self.vehicle = vehicles[0]

            logger.info("Target vehicle selected: %s (VIN: %s, State: %s)",
                        self.vehicle.get('display_name'),
                        self.vehicle.get('vin'),
                        self.vehicle.get('state'))
            return True

        except Exception as e:
            logger.error("Failed to initialize Tesla client: %s", e)
            return False

    def is_awake(self) -> bool:
        """Check if the vehicle is currently awake without waking it."""
        if not self.vehicle:
            return False
        state = self.vehicle.get('state', '').lower()
        return state == 'online'

    def wake_up(self, timeout: int = 60) -> bool:
        """
        Wake up the vehicle if it is asleep.
        Only call this when solar surplus is confirmed to prevent phantom drain.
        """
        if not self.vehicle:
            return False

        now = time.time()
        # Cooldown to prevent spamming wake commands if car fails to wake
        if now - self._last_wake_attempt < 30:
            logger.debug("Wake attempt throttled (within 30s cooldown).")
            return self.is_awake()

        self._last_wake_attempt = now
        try:
            logger.info("Waking up vehicle %s...", self.vehicle.get('display_name'))
            self.vehicle.sync_wake_up(timeout=timeout)
            logger.info("Vehicle is now online.")
            return True
        except Exception as e:
            logger.warning("Could not wake up vehicle: %s", e)
            return False

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        """
        Retrieve charging state from vehicle.
        If wake_if_needed is False and car is asleep, returns last known cached status or None.
        """
        if not self.vehicle:
            return None

        # Check vehicle online state
        try:
            # Refresh top-level vehicle state
            self.vehicle.get_vehicle_summary()
        except Exception as ex:
            logger.debug("Failed to refresh vehicle summary: %s", ex)

        if not self.is_awake():
            if wake_if_needed:
                if not self.wake_up():
                    return self._last_known_state
            else:
                # Return cached or asleep state without waking
                return {
                    "online": False,
                    "charging_state": "Asleep",
                    "plugged_in": None,
                    "battery_level": self._last_known_state.get("battery_level") if self._last_known_state else None,
                    "charge_amps": self._last_known_state.get("charge_amps") if self._last_known_state else None,
                    "charger_voltage": self._last_known_state.get("charger_voltage") if self._last_known_state else 120,
                    "charger_actual_current": 0
                }

        try:
            data = self.vehicle.get_vehicle_data()
            charge_state = data.get('charge_state', {})

            charging_state = charge_state.get('charging_state')  # 'Charging', 'Stopped', 'Complete', 'Disconnected'
            plugged_in = charging_state != 'Disconnected' and charge_state.get('charge_port_door_open', False)

            state_info = {
                "online": True,
                "charging_state": charging_state,
                "plugged_in": plugged_in,
                "battery_level": charge_state.get('battery_level'),
                "charge_limit_soc": charge_state.get('charge_limit_soc'),
                "charge_amps": charge_state.get('charge_amps'),
                "charge_current_request": charge_state.get('charge_current_request'),
                "charger_actual_current": charge_state.get('charger_actual_current', 0),
                "charger_voltage": charge_state.get('charger_voltage', 120),
                "time_to_full_charge": charge_state.get('time_to_full_charge', 0.0),
                "timestamp": time.time()
            }
            self._last_known_state = state_info
            return state_info

        except Exception as e:
            logger.warning("Error fetching vehicle charge data: %s", e)
            return self._last_known_state

    def set_charging_amps(self, amps: int) -> bool:
        """Set the charging current (Amps) on the vehicle."""
        if not self.vehicle:
            return False

        # Ensure vehicle is awake
        if not self.is_awake() and not self.wake_up():
            return False

        try:
            logger.info("Setting vehicle charging amps to %d A...", amps)
            res = self.vehicle.command('set_charging_amps', charging_amps=amps)
            logger.info("set_charging_amps response: %s", res)
            return bool(res.get('response', {}).get('result', False) or res.get('result', False))
        except Exception as e:
            logger.error("Failed to set charging amps to %d A: %s", amps, e)
            return False

    def start_charging(self) -> bool:
        """Start charging the vehicle."""
        if not self.vehicle:
            return False

        if not self.is_awake() and not self.wake_up():
            return False

        try:
            logger.info("Sending start charging command...")
            res = self.vehicle.command('charge_start')
            logger.info("charge_start response: %s", res)
            return bool(res.get('response', {}).get('result', False) or res.get('result', False))
        except Exception as e:
            logger.error("Failed to start charging: %s", e)
            return False

    def stop_charging(self) -> bool:
        """Stop charging the vehicle."""
        if not self.vehicle:
            return False

        if not self.is_awake():
            # If the car is asleep, it's not charging anyway
            return True

        try:
            logger.info("Sending stop charging command...")
            res = self.vehicle.command('charge_stop')
            logger.info("charge_stop response: %s", res)
            return bool(res.get('response', {}).get('result', False) or res.get('result', False))
        except Exception as e:
            logger.error("Failed to stop charging: %s", e)
            return False
