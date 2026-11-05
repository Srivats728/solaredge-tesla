"""
Unified Vehicle Client Interface
Supports Tessie API, Home Assistant, Official Tesla Fleet API, and Local Simulator.
Designed for extreme reliability, zero unnecessary complexity, and zero emojis.
"""

import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
import requests

logger = logging.getLogger(__name__)


class BaseVehicleClient(ABC):
    """Abstract base class for all vehicle integrations."""

    @abstractmethod
    def initialize(self) -> bool:
        """Discover or configure vehicle."""
        pass

    @abstractmethod
    def is_awake(self) -> bool:
        """Check if vehicle is awake without waking it."""
        pass

    @abstractmethod
    def wake_up(self, timeout: int = 60) -> bool:
        """Wake up vehicle if asleep."""
        pass

    @abstractmethod
    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        """Return standardized charge telemetry dict."""
        pass

    @abstractmethod
    def set_charging_amps(self, amps: int) -> bool:
        """Set charge rate in Amps."""
        pass

    @abstractmethod
    def start_charging(self) -> bool:
        """Start or resume charging."""
        pass

    @abstractmethod
    def stop_charging(self) -> bool:
        """Stop charging."""
        pass

    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """Return provider status metadata."""
        pass


# ==============================================================================
# 1. TESSIE API CLIENT (The Simplest 1-Key Solution)
# ==============================================================================
class TessieClient(BaseVehicleClient):
    """
    Controls Tesla vehicles via Tessie's managed Fleet API proxy.
    Requires only a single API Key. Zero developer portal or domain configuration needed.
    """

    BASE_URL = "https://api.tessie.com"

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.api_key: str = config.get("api_key", "").strip()
        self.vin: Optional[str] = config.get("vin", "").strip() or None
        self.vehicle_name: str = "Tesla Model 3"
        self._last_state: Optional[Dict[str, Any]] = None
        self._last_wake_time: float = 0.0

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json"
        }

    def initialize(self) -> bool:
        if not self.api_key:
            logger.warning("[Tessie] No API key configured.")
            return False

        try:
            logger.info("[Tessie] Fetching vehicles from Tessie API...")
            resp = requests.get(f"{self.BASE_URL}/vehicles", headers=self._headers(), timeout=12)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                if not results:
                    logger.warning("[Tessie] No vehicles found on Tessie account.")
                    return False

                # Select vehicle
                target = None
                if self.vin:
                    target = next((v for v in results if v.get("vin") == self.vin), None)
                if not target:
                    target = results[0]

                self.vin = target.get("vin")
                self.vehicle_name = target.get("last_state", {}).get("display_name") or target.get("vin", "Model 3")
                logger.info("[Tessie] Connected to %s (VIN: %s)", self.vehicle_name, self.vin)
                return True
            else:
                logger.error("[Tessie] HTTP %d: %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("[Tessie] Initialization failed: %s", e)
            return False

    def is_awake(self) -> bool:
        if not self.vin:
            return False
        try:
            resp = requests.get(f"{self.BASE_URL}/{self.vin}/status", headers=self._headers(), timeout=8)
            if resp.status_code == 200:
                status = resp.json().get("status", "").lower()
                return status in ["awake", "online"]
        except Exception:
            pass
        return False

    def wake_up(self, timeout: int = 60) -> bool:
        if not self.vin:
            return False

        now = time.time()
        if now - self._last_wake_time < 30:
            return self.is_awake()

        self._last_wake_time = now
        logger.info("[Tessie] Sending wake command for %s...", self.vin)
        try:
            resp = requests.post(f"{self.BASE_URL}/{self.vin}/wake", headers=self._headers(), timeout=15)
            if resp.status_code == 200 and resp.json().get("result"):
                return True

            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(3)
                if self.is_awake():
                    return True
            return False
        except Exception as e:
            logger.warning("[Tessie] Wake failed: %s", e)
            return False

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        if not self.vin:
            return None

        online = self.is_awake()
        if not online:
            if wake_if_needed:
                if not self.wake_up():
                    return self._last_state
            else:
                return {
                    "online": False,
                    "charging_state": "Asleep",
                    "plugged_in": None,
                    "battery_level": self._last_state.get("battery_level") if self._last_state else None,
                    "charge_amps": self._last_state.get("charge_amps") if self._last_state else 5,
                    "charger_voltage": 120,
                    "charger_actual_current": 0
                }

        try:
            resp = requests.get(f"{self.BASE_URL}/{self.vin}/state", headers=self._headers(), timeout=12)
            if resp.status_code == 200:
                data = resp.json()
                cs = data.get("charge_state", {})
                chg_state = cs.get("charging_state")
                plugged = chg_state != "Disconnected" and cs.get("charge_port_door_open", False)

                info = {
                    "online": True,
                    "charging_state": chg_state,
                    "plugged_in": plugged,
                    "battery_level": cs.get("battery_level"),
                    "charge_limit_soc": cs.get("charge_limit_soc"),
                    "charge_amps": cs.get("charge_amps"),
                    "charge_current_request": cs.get("charge_current_request"),
                    "charger_actual_current": cs.get("charger_actual_current", 0),
                    "charger_voltage": cs.get("charger_voltage", 120),
                    "time_to_full_charge": cs.get("time_to_full_charge", 0.0),
                    "timestamp": time.time()
                }
                self._last_state = info
                return info
            return self._last_state
        except Exception as e:
            logger.warning("[Tessie] Error getting state: %s", e)
            return self._last_state

    def _command(self, endpoint: str, params: Optional[dict] = None) -> bool:
        if not self.vin:
            return False
        try:
            url = f"{self.BASE_URL}/{self.vin}/command/{endpoint}"
            resp = requests.post(url, headers=self._headers(), params=params or {}, timeout=15)
            if resp.status_code == 200:
                res = resp.json()
                success = bool(res.get("result", False))
                logger.info("[Tessie] Command %s result: %s", endpoint, success)
                return success
            logger.error("[Tessie] Command %s failed: HTTP %d - %s", endpoint, resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("[Tessie] Command %s exception: %s", endpoint, e)
            return False

    def set_charging_amps(self, amps: int) -> bool:
        return self._command("set_charging_amps", {"amps": amps})

    def start_charging(self) -> bool:
        return self._command("start_charging")

    def stop_charging(self) -> bool:
        return self._command("stop_charging")

    def get_info(self) -> Dict[str, Any]:
        return {
            "provider": "tessie",
            "name": self.vehicle_name,
            "vin": self.vin,
            "configured": bool(self.api_key),
            "description": "Tessie Cloud API"
        }


# ==============================================================================
# 2. HOME ASSISTANT CLIENT (Local Smart Home Bridge)
# ==============================================================================
class HomeAssistantClient(BaseVehicleClient):
    """
    Connects to an existing Home Assistant instance via LAN REST API.
    Zero external cloud configuration needed if Home Assistant is already running.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.url = config.get("url", "http://homeassistant.local:8123").rstrip("/")
        self.token = config.get("token", "").strip()

        # Entity IDs (defaults match HA Tesla integration)
        self.soc_entity = config.get("soc_entity", "sensor.battery_sensor")
        self.amps_entity = config.get("amps_entity", "number.charging_amps")
        self.switch_entity = config.get("switch_entity", "switch.charger")
        self.status_entity = config.get("status_entity", "binary_sensor.charging")

        self.vehicle_name = "Tesla Model 3 (Home Assistant)"
        self._last_state: Optional[Dict[str, Any]] = None

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def initialize(self) -> bool:
        if not self.token:
            return False
        try:
            resp = requests.get(f"{self.url}/api/", headers=self._headers(), timeout=8)
            if resp.status_code == 200:
                logger.info("[HomeAssistant] Connected successfully to %s", self.url)
                return True
            logger.warning("[HomeAssistant] HTTP %d connecting to %s", resp.status_code, self.url)
            return False
        except Exception as e:
            logger.warning("[HomeAssistant] Could not reach %s: %s", self.url, e)
            return False

    def is_awake(self) -> bool:
        return True  # HA handles wake states automatically

    def wake_up(self, timeout: int = 60) -> bool:
        # Trigger wake up button in HA if available
        try:
            requests.post(
                f"{self.url}/api/services/button/press",
                headers=self._headers(),
                json={"entity_id": "button.wake_up"},
                timeout=10
            )
        except Exception:
            pass
        return True

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        try:
            # Read battery level
            soc_resp = requests.get(f"{self.url}/api/states/{self.soc_entity}", headers=self._headers(), timeout=8)
            battery_level = None
            if soc_resp.status_code == 200:
                try:
                    battery_level = int(float(soc_resp.json().get("state", 0)))
                except Exception:
                    pass

            # Read amps
            amps_resp = requests.get(f"{self.url}/api/states/{self.amps_entity}", headers=self._headers(), timeout=8)
            amps = 5
            if amps_resp.status_code == 200:
                try:
                    amps = int(float(amps_resp.json().get("state", 5)))
                except Exception:
                    pass

            # Read switch state
            sw_resp = requests.get(f"{self.url}/api/states/{self.switch_entity}", headers=self._headers(), timeout=8)
            charging_state = "Stopped"
            if sw_resp.status_code == 200 and sw_resp.json().get("state") == "on":
                charging_state = "Charging"

            info = {
                "online": True,
                "charging_state": charging_state,
                "plugged_in": True,
                "battery_level": battery_level,
                "charge_amps": amps,
                "charge_current_request": amps,
                "charger_actual_current": amps if charging_state == "Charging" else 0,
                "charger_voltage": 120,
                "time_to_full_charge": 0.0,
                "timestamp": time.time()
            }
            self._last_state = info
            return info
        except Exception as e:
            logger.warning("[HomeAssistant] Error fetching states: %s", e)
            return self._last_state

    def set_charging_amps(self, amps: int) -> bool:
        try:
            resp = requests.post(
                f"{self.url}/api/services/number/set_value",
                headers=self._headers(),
                json={"entity_id": self.amps_entity, "value": amps},
                timeout=10
            )
            return resp.status_code in [200, 201]
        except Exception as e:
            logger.error("[HomeAssistant] Failed to set amps: %s", e)
            return False

    def start_charging(self) -> bool:
        try:
            resp = requests.post(
                f"{self.url}/api/services/switch/turn_on",
                headers=self._headers(),
                json={"entity_id": self.switch_entity},
                timeout=10
            )
            return resp.status_code in [200, 201]
        except Exception:
            return False

    def stop_charging(self) -> bool:
        try:
            resp = requests.post(
                f"{self.url}/api/services/switch/turn_off",
                headers=self._headers(),
                json={"entity_id": self.switch_entity},
                timeout=10
            )
            return resp.status_code in [200, 201]
        except Exception:
            return False

    def get_info(self) -> Dict[str, Any]:
        return {
            "provider": "homeassistant",
            "name": self.vehicle_name,
            "vin": "Home Assistant Bridge",
            "configured": bool(self.token),
            "description": f"Home Assistant ({self.url})"
        }


# ==============================================================================
# 3. TESLA FLEET API CLIENT (Official Direct Developer Gateway)
# ==============================================================================
class TeslaFleetClientWrapper(BaseVehicleClient):
    """Wraps existing TeslaFleetClient for unified interface."""

    def __init__(self, config: Dict[str, Any]):
        from tesla_fleet import TeslaFleetClient
        self.inner = TeslaFleetClient(config)

    def initialize(self) -> bool:
        return self.inner.initialize()

    def is_awake(self) -> bool:
        return self.inner.is_awake()

    def wake_up(self, timeout: int = 60) -> bool:
        return self.inner.wake_up(timeout)

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        return self.inner.get_charge_state(wake_if_needed)

    def set_charging_amps(self, amps: int) -> bool:
        return self.inner.set_charging_amps(amps)

    def start_charging(self) -> bool:
        return self.inner.start_charging()

    def stop_charging(self) -> bool:
        return self.inner.stop_charging()

    def get_info(self) -> Dict[str, Any]:
        v = self.inner.vehicle_info or {}
        return {
            "provider": "fleet",
            "name": v.get("display_name", "Tesla Model 3"),
            "vin": v.get("vin", "Registered Vehicle"),
            "configured": bool(self.inner.access_token or self.inner.refresh_token),
            "description": "Tesla Developer Fleet API"
        }


# ==============================================================================
# 4. SIMULATOR CLIENT (Built-in Demo & Testing Mode)
# ==============================================================================
class SimulatorVehicleClient(BaseVehicleClient):
    """
    Simulated Tesla Model 3.
    Allows instant testing of the entire solar optimization system, Modbus readings,
    and WhatsApp alerts without requiring any external vehicle credentials.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.vehicle_name = "Model 3 (Simulated)"
        self.vin = "5YJ3E1EB8NF000001"
        self.battery_level = 68
        self.charge_amps = 5
        self.is_charging = False
        self.online = True
        logger.info("[Simulator] Initialized mock vehicle client.")

    def initialize(self) -> bool:
        return True

    def is_awake(self) -> bool:
        return self.online

    def wake_up(self, timeout: int = 60) -> bool:
        self.online = True
        return True

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        return {
            "online": self.online,
            "charging_state": "Charging" if self.is_charging else "Stopped",
            "plugged_in": True,
            "battery_level": self.battery_level,
            "charge_limit_soc": 90,
            "charge_amps": self.charge_amps,
            "charge_current_request": self.charge_amps,
            "charger_actual_current": self.charge_amps if self.is_charging else 0,
            "charger_voltage": 120,
            "time_to_full_charge": 4.5,
            "timestamp": time.time()
        }

    def set_charging_amps(self, amps: int) -> bool:
        logger.info("[Simulator] Setting charging current to %d A", amps)
        self.charge_amps = amps
        return True

    def start_charging(self) -> bool:
        logger.info("[Simulator] Charging started at %d A", self.charge_amps)
        self.is_charging = True
        return True

    def stop_charging(self) -> bool:
        logger.info("[Simulator] Charging stopped")
        self.is_charging = False
        return True

    def get_info(self) -> Dict[str, Any]:
        return {
            "provider": "simulator",
            "name": self.vehicle_name,
            "vin": self.vin,
            "configured": True,
            "description": "Built-in Local Simulator"
        }


# ==============================================================================
# FACTORY DISPATCHER
# ==============================================================================
def create_vehicle_client(config: Dict[str, Any]) -> BaseVehicleClient:
    """Factory creating the appropriate vehicle client based on configuration."""
    provider = config.get("provider", "simulator").lower()

    if provider == "tessie":
        return TessieClient(config.get("tessie", {}))
    elif provider == "homeassistant":
        return HomeAssistantClient(config.get("homeassistant", {}))
    elif provider == "fleet":
        return TeslaFleetClientWrapper(config.get("fleet", {}))
    else:
        return SimulatorVehicleClient(config.get("simulator", {}))
