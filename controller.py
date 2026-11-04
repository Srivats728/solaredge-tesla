"""
Charging Controller & Optimization Engine
Calculates dynamic charging rates, enforces hysteresis/debouncing,
and coordinates SolarEdge readings with Tesla controls.
"""

import logging
import math
import time
from typing import Dict, Any, Optional
from solaredge import SolarEdgeClient
from tesla_client import TeslaController
from notifier import WhatsAppNotifier

logger = logging.getLogger(__name__)


class SolarChargingController:
    """Manages the logic of matching Tesla charging rate with SolarEdge generation."""

    def __init__(
        self,
        solaredge: SolarEdgeClient,
        tesla: TeslaController,
        notifier: WhatsAppNotifier,
        config: Dict[str, Any]
    ):
        self.solaredge = solaredge
        self.tesla = tesla
        self.notifier = notifier
        self.cfg = config

        # Operating Mode: 'auto', 'force' (force 12A), 'off' (stop charge)
        self.mode = "auto"

        # Parameters
        self.voltage = self.cfg.get("voltage", 120)
        self.min_amps = max(5, self.cfg.get("min_amps", 5))  # Tesla firmware min is 5A
        self.max_amps = min(12, self.cfg.get("max_amps", 12))  # NEMA 5-15 continuous limit is 12A
        self.start_threshold_w = self.cfg.get("start_threshold_watts", 1700)
        self.baseline_house_w = self.cfg.get("baseline_house_watts", 600)
        self.stop_threshold_w = self.cfg.get("stop_threshold_watts", 1100)

        self.start_delay_sec = self.cfg.get("start_delay_seconds", 120)
        self.stop_delay_sec = self.cfg.get("stop_delay_seconds", 180)
        self.amp_cooldown_sec = self.cfg.get("amp_change_cooldown_seconds", 45)

        # Telemetry Cache for Web Dashboard
        self.last_solar_data: Dict[str, Any] = {}
        self.last_tesla_data: Dict[str, Any] = {}

        # Internal tracking state
        self.is_charging = False
        self.current_set_amps: Optional[int] = None
        self.target_amps: int = 5
        self.high_solar_since: Optional[float] = None
        self.low_solar_since: Optional[float] = None
        self.last_amp_change_time: float = 0.0
        self.last_unplugged_alert_time: float = 0.0

    def set_mode(self, new_mode: str):
        """Change operating mode ('auto', 'force', 'off')."""
        logger.info("Optimizer mode changed from %s to %s", self.mode, new_mode)
        self.mode = new_mode

        if self.mode == "off":
            if self.is_charging:
                logger.info("Mode is OFF: stopping charging...")
                self.tesla.stop_charging()
                self.is_charging = False
        elif self.mode == "force":
            logger.info("Mode is FORCE: setting max current %d A and starting charge...", self.max_amps)
            self.tesla.wake_up()
            self.tesla.set_charging_amps(self.max_amps)
            self.tesla.start_charging()
            self.is_charging = True
            self.current_set_amps = self.max_amps

    def reload_config(self, full_config: Dict[str, Any]):
        """Dynamically reload configuration parameters without restarting daemon."""
        logger.info("Reloading configuration parameters into controller...")
        chg = full_config.get("charging", {})
        self.voltage = chg.get("voltage", self.voltage)
        self.min_amps = max(5, chg.get("min_amps", self.min_amps))
        self.max_amps = min(12, chg.get("max_amps", self.max_amps))
        self.start_threshold_w = chg.get("start_threshold_watts", self.start_threshold_w)
        self.baseline_house_w = chg.get("baseline_house_watts", self.baseline_house_w)
        self.stop_threshold_w = chg.get("stop_threshold_watts", self.stop_threshold_w)
        self.start_delay_sec = chg.get("start_delay_seconds", self.start_delay_sec)
        self.stop_delay_sec = chg.get("stop_delay_seconds", self.stop_delay_sec)

        # Update SolarEdge connection params if changed
        se = full_config.get("solaredge", {})
        if se.get("host") and (se.get("host") != self.solaredge.host or se.get("port") != self.solaredge.port):
            self.solaredge.close()
            self.solaredge.host = se.get("host", self.solaredge.host)
            self.solaredge.port = se.get("port", self.solaredge.port)

        # Update Notifier
        self.notifier.__init__(full_config.get("whatsapp", {}))

    def calculate_target_amps(self, solar_power_w: float) -> int:
        """
        Calculate appropriate charging current based on solar surplus.
        Available for car = solar_power_w - baseline_house_w
        """
        available_power_w = max(0.0, solar_power_w - self.baseline_house_w)
        raw_amps = available_power_w / float(self.voltage)
        # Use floor to stay conservative and avoid drawing from the grid
        target = math.floor(raw_amps)
        # Clamp within allowable Level 1 boundaries
        target = max(self.min_amps, min(self.max_amps, target))
        return target

    def evaluate_step(self):
        """Execute a single control loop iteration."""
        # 1. Read SolarEdge Generation
        solar_data = self.solaredge.read_generation()
        if solar_data.get("connected", False):
            self.last_solar_data = solar_data
        else:
            logger.warning("SolarEdge inverter not responding. Using cached data if any.")

        power_w = solar_data.get("power_watts", 0.0)
        status_desc = solar_data.get("status_desc", "UNKNOWN")
        now = time.time()

        logger.info("Solar Generation: %.1f W (%.2f kW) | Inverter: %s",
                    power_w, power_w / 1000.0, status_desc)

        # 2. Get Vehicle Status
        # If not charging and solar is low, do NOT wake the car (saves vampire drain)
        wake_needed = self.is_charging or (power_w >= self.start_threshold_w and self.high_solar_since is not None) or self.mode == "force"
        tesla_data = self.tesla.get_charge_state(wake_if_needed=wake_needed)

        if tesla_data:
            self.last_tesla_data = tesla_data
        else:
            logger.warning("Unable to retrieve Tesla charge status.")
            return

        charging_state = tesla_data.get("charging_state")
        battery_level = tesla_data.get("battery_level")
        plugged_in = tesla_data.get("plugged_in")

        # Sync internal is_charging state with vehicle reported state if online
        if tesla_data.get("online"):
            self.is_charging = (charging_state == "Charging")
            current_vehicle_amps = tesla_data.get("charge_current_request") or tesla_data.get("charge_amps")
            if current_vehicle_amps:
                self.current_set_amps = current_vehicle_amps

        logger.info("Tesla Status: %s | Plugged In: %s | Battery: %s%% | Car Amps: %s A",
                    charging_state, plugged_in, battery_level, self.current_set_amps)

        # If car is full, no action needed
        if charging_state == "Complete":
            logger.info("Tesla battery is full / reached charge limit (%s%%). Pausing control.", battery_level)
            self.is_charging = False
            return

        # Check Manual / Off Modes
        if self.mode == "off":
            if self.is_charging:
                self.tesla.stop_charging()
                self.is_charging = False
            return
        elif self.mode == "force":
            if not self.is_charging and plugged_in is not False:
                self.tesla.set_charging_amps(self.max_amps)
                self.tesla.start_charging()
                self.is_charging = True
                self.current_set_amps = self.max_amps
            return

        # 3. Decision Logic: Auto Solar Matching
        self.target_amps = self.calculate_target_amps(power_w)

        if not self.is_charging:
            self._handle_not_charging(power_w, now, plugged_in, battery_level)
        else:
            self._handle_charging(power_w, now, plugged_in, battery_level)

    def _handle_not_charging(self, power_w: float, now: float, plugged_in: Optional[bool], battery_level: Optional[int]):
        """Logic when car is currently idle / not charging."""
        self.low_solar_since = None

        if power_w >= self.start_threshold_w:
            if self.high_solar_since is None:
                self.high_solar_since = now
                logger.info("Solar power (%.1f W) exceeds start threshold (%.1f W). Starting delay timer (%d s)...",
                            power_w, self.start_threshold_w, self.start_delay_sec)
                return

            elapsed = now - self.high_solar_since
            remaining = self.start_delay_sec - elapsed
            if remaining > 0:
                logger.info("Solar above threshold for %.0f s (needs %d s, %.0f s remaining)...",
                            elapsed, self.start_delay_sec, remaining)
                return

            # Timer satisfied: wake vehicle and start charging
            logger.info("Solar surplus sustained for %d s. Initiating Tesla charging...", self.start_delay_sec)

            if not self.tesla.is_awake():
                if not self.tesla.wake_up():
                    logger.warning("Could not wake up Tesla to start charge.")
                    return

            fresh_status = self.tesla.get_charge_state(wake_if_needed=True)
            if fresh_status and fresh_status.get("plugged_in") is False:
                logger.warning("Tesla is not plugged in! Cannot start solar charging.")
                if now - self.last_unplugged_alert_time > 3600:
                    self.notifier.send("ALERT: Solar surplus available, but Tesla is unplugged.")
                    self.last_unplugged_alert_time = now
                self.high_solar_since = None
                return

            target_amps = self.calculate_target_amps(power_w)
            logger.info("Setting initial charging rate to %d A...", target_amps)
            self.tesla.set_charging_amps(target_amps)
            time.sleep(1)

            if self.tesla.start_charging():
                self.is_charging = True
                self.current_set_amps = target_amps
                self.last_amp_change_time = now
                self.high_solar_since = None
                msg = (f"Solar charging STARTED\n"
                       f"Solar Gen: {power_w:.0f} W\n"
                       f"Rate: {target_amps} A @ {self.voltage}V (~{target_amps * self.voltage} W)\n"
                       f"Battery: {battery_level}%")
                self.notifier.send(msg)
            else:
                logger.warning("Tesla start_charging command did not succeed.")
        else:
            if self.high_solar_since is not None:
                logger.info("Solar power dropped below start threshold. Resetting start delay timer.")
                self.high_solar_since = None

    def _handle_charging(self, power_w: float, now: float, plugged_in: Optional[bool], battery_level: Optional[int]):
        """Logic when car is currently active / charging."""
        self.high_solar_since = None

        if plugged_in is False:
            logger.warning("Tesla disconnected while charging.")
            self.is_charging = False
            self.notifier.send("Tesla disconnected from charger.")
            return

        if power_w < self.stop_threshold_w:
            if self.low_solar_since is None:
                self.low_solar_since = now
                logger.info("Solar power (%.1f W) fell below stop threshold (%.1f W). Starting stop timer (%d s)...",
                            power_w, self.stop_threshold_w, self.stop_delay_sec)
                if self.current_set_amps != self.min_amps:
                    logger.info("Stepping down current to %d A while waiting for cloud/solar recovery...", self.min_amps)
                    self.tesla.set_charging_amps(self.min_amps)
                    self.current_set_amps = self.min_amps
                    self.last_amp_change_time = now
                return

            elapsed = now - self.low_solar_since
            remaining = self.stop_delay_sec - elapsed
            if remaining > 0:
                logger.info("Solar below stop threshold for %.0f s (needs %d s, %.0f s remaining)...",
                            elapsed, self.stop_delay_sec, remaining)
                return

            logger.info("Solar power below stop threshold for %d s. Stopping Tesla charge...", self.stop_delay_sec)
            if self.tesla.stop_charging():
                self.is_charging = False
                self.low_solar_since = None
                msg = (f"Solar charging STOPPED (low solar)\n"
                       f"Solar Gen: {power_w:.0f} W\n"
                       f"Battery: {battery_level}%")
                self.notifier.send(msg)
            else:
                logger.warning("Tesla stop_charging command failed.")
            return
        else:
            if self.low_solar_since is not None:
                logger.info("Solar power recovered (%.1f W). Resetting stop delay timer.", power_w)
                self.low_solar_since = None

        target_amps = self.calculate_target_amps(power_w)
        if target_amps != self.current_set_amps:
            time_since_last_change = now - self.last_amp_change_time
            if time_since_last_change >= self.amp_cooldown_sec:
                logger.info("Adjusting charging current: %s A -> %s A (Solar: %.1f W)...",
                            self.current_set_amps, target_amps, power_w)
                if self.tesla.set_charging_amps(target_amps):
                    old_amps = self.current_set_amps
                    self.current_set_amps = target_amps
                    self.last_amp_change_time = now
                    if old_amps is not None and abs(target_amps - old_amps) >= 2:
                        self.notifier.send(
                            f"Solar rate adjusted: {target_amps} A (~{target_amps * self.voltage} W)\n"
                            f"Solar Gen: {power_w:.0f} W"
                        )
            else:
                logger.debug("Amp change from %s to %s deferred (cooldown: %.0fs / %ds).",
                             self.current_set_amps, target_amps, time_since_last_change, self.amp_cooldown_sec)
