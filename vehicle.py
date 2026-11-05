"""
Unified Vehicle Client Interface
Supports 100% Free Methods:
1. Local Bluetooth Low Energy (BLE) via Tesla's official tesla-control (zero-cloud, offline, free).
2. Official Tesla Fleet API (Free tier with $16/mo developer credits and virtual key signing).
3. Home Assistant Local Bridge (controls vehicle onboard charger directly, no smart plug needed).
4. Local Simulator (instant demo & unit testing).
Strictly zero emojis.
"""

import logging
import os
import subprocess
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
# 1. LOCAL BLUETOOTH LOW ENERGY (BLE) CLIENT (Zero-Cloud, Offline, 100% Free)
# ==============================================================================
class TeslaControlBLEClient(BaseVehicleClient):
    """
    Direct vehicle control over Bluetooth Low Energy (BLE).
    Zero cloud accounts, zero domains, zero subscriptions, 100% offline.
    Uses Tesla's official open-source tool: tesla-control (teslamotors/vehicle-command).
    Requires a Bluetooth adapter within physical range (~30ft) of the vehicle.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.vin: str = config.get("vin", "").strip()
        self.key_file: str = config.get("key_file", "private-key.pem").strip()
        self.binary_path: str = config.get("binary_path", "tesla-control").strip()
        self.vehicle_name: str = "Tesla Model 3 (BLE)"
        self.charge_amps: int = 5
        self.is_charging: bool = False
        self.battery_level: Optional[int] = None
        self._last_state: Optional[Dict[str, Any]] = None

    def _run_cmd(self, args: List[str], timeout: int = 20) -> subprocess.CompletedProcess:
        cmd = [self.binary_path, "-vin", self.vin, "-ble", "-key-file", self.key_file] + args
        logger.debug("[BLE] Executing: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def initialize(self) -> bool:
        if not self.vin:
            logger.warning("[BLE] VIN is required for Bluetooth Low Energy control.")
            return False
        if not os.path.exists(self.key_file):
            logger.warning("[BLE] Key file '%s' not found.", self.key_file)
            return False
        return True

    def is_awake(self) -> bool:
        # Over BLE, the Bluetooth radio remains responsive when car is asleep
        return True

    def wake_up(self, timeout: int = 60) -> bool:
        try:
            res = self._run_cmd(["wake"], timeout=timeout)
            return res.returncode == 0
        except Exception as e:
            logger.warning("[BLE] Wake exception: %s", e)
            return False

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        # Over BLE, we can query vehicle body controller or return tracked state
        state = {
            "online": True,
            "charging_state": "Charging" if self.is_charging else "Stopped",
            "plugged_in": True,
            "battery_level": self.battery_level,
            "charge_limit_soc": 90,
            "charge_amps": self.charge_amps,
            "charge_current_request": self.charge_amps,
            "charger_actual_current": self.charge_amps if self.is_charging else 0,
            "charger_voltage": 120,
            "time_to_full_charge": 0.0,
            "timestamp": time.time()
        }
        self._last_state = state
        return state

    def set_charging_amps(self, amps: int) -> bool:
        logger.info("[BLE] Sending command: charging-set-amps %d to %s...", amps, self.vin)
        try:
            res = self._run_cmd(["charging-set-amps", str(amps)])
            if res.returncode == 0:
                logger.info("[BLE] Charging rate set to %d A successfully.", amps)
                self.charge_amps = amps
                return True
            logger.error("[BLE] Command charging-set-amps failed: %s", res.stderr.strip() or res.stdout.strip())
            return False
        except Exception as e:
            logger.error("[BLE] Exception running charging-set-amps: %s", e)
            return False

    def start_charging(self) -> bool:
        logger.info("[BLE] Sending command: charging-start to %s...", self.vin)
        try:
            res = self._run_cmd(["charging-start"])
            if res.returncode == 0:
                logger.info("[BLE] Charging started successfully.")
                self.is_charging = True
                return True
            logger.error("[BLE] Command charging-start failed: %s", res.stderr.strip() or res.stdout.strip())
            return False
        except Exception as e:
            logger.error("[BLE] Exception running charging-start: %s", e)
            return False

    def stop_charging(self) -> bool:
        logger.info("[BLE] Sending command: charging-stop to %s...", self.vin)
        try:
            res = self._run_cmd(["charging-stop"])
            if res.returncode == 0:
                logger.info("[BLE] Charging stopped successfully.")
                self.is_charging = False
                return True
            logger.error("[BLE] Command charging-stop failed: %s", res.stderr.strip() or res.stdout.strip())
            return False
        except Exception as e:
            logger.error("[BLE] Exception running charging-stop: %s", e)
            return False

    def get_info(self) -> Dict[str, Any]:
        return {
            "provider": "ble",
            "name": self.vehicle_name,
            "vin": self.vin or "Not Configured",
            "configured": bool(self.vin and os.path.exists(self.key_file)),
            "description": "Local Bluetooth Low Energy (Offline & Free)"
        }


# ==============================================================================
# 2. OFFICIAL TESLA FLEET API CLIENT (Cloud, 100% Free with $16/mo Credit)
# ==============================================================================
REGIONS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "ap": "https://fleet-api.prd.ap.vn.cloud.tesla.com"
}
AUTH_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"


class TeslaFleetDirectClient(BaseVehicleClient):
    """
    Direct Tesla Developer Fleet API Client.
    Completely free under Tesla's $16/month developer credit tier for personal use.
    Handles automatic OAuth token refreshing, REST telemetry polling,
    and command signing via tesla-control or virtual key proxy.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.client_id = config.get("client_id", "").strip()
        self.client_secret = config.get("client_secret", "").strip()
        self.access_token = config.get("access_token", "").strip()
        self.refresh_token = config.get("refresh_token", "").strip()
        self.region = config.get("region", "na").strip().lower()
        self.vin = config.get("vin", "").strip()
        self.key_file = config.get("key_file", "private-key.pem").strip()
        self.binary_path = config.get("binary_path", "tesla-control").strip()

        self.base_url = REGIONS.get(self.region, REGIONS["na"])
        self.vehicle_name = "Tesla Model 3"
        self._last_state: Optional[Dict[str, Any]] = None
        self._last_wake_time: float = 0.0

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "SolarEdgeTeslaOptimizer/2.0"
        }

    def refresh_access_token(self) -> bool:
        if not self.refresh_token:
            logger.warning("[Fleet] No refresh token available to renew access token.")
            return False

        logger.info("[Fleet] Refreshing Fleet API access token...")
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token
        }
        try:
            resp = requests.post(AUTH_URL, data=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("access_token", self.access_token)
                self.refresh_token = data.get("refresh_token", self.refresh_token)
                logger.info("[Fleet] Token refreshed successfully.")
                return True
            else:
                logger.error("[Fleet] Token refresh failed: HTTP %d - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("[Fleet] Token refresh exception: %s", e)
            return False

    def initialize(self) -> bool:
        if not self.access_token and self.refresh_token:
            self.refresh_access_token()

        if not self.access_token:
            logger.warning("[Fleet] No access token or refresh token configured.")
            return False

        try:
            resp = requests.get(f"{self.base_url}/api/1/vehicles", headers=self._headers(), timeout=12)
            if resp.status_code == 401:
                # Token expired, try refresh once
                if self.refresh_access_token():
                    resp = requests.get(f"{self.base_url}/api/1/vehicles", headers=self._headers(), timeout=12)

            if resp.status_code == 200:
                vehicles = resp.json().get("response", [])
                if not vehicles:
                    logger.warning("[Fleet] No vehicles returned on this account.")
                    return False

                target = None
                if self.vin:
                    target = next((v for v in vehicles if v.get("vin") == self.vin), None)
                if not target:
                    target = vehicles[0]

                self.vin = target.get("vin")
                self.vehicle_name = target.get("display_name") or target.get("vin", "Model 3")
                logger.info("[Fleet] Connected to %s (VIN: %s)", self.vehicle_name, self.vin)
                return True
            else:
                logger.warning("[Fleet] Failed to query vehicles: HTTP %d - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("[Fleet] Initialization error: %s", e)
            return False

    def is_awake(self) -> bool:
        if not self.vin:
            return False
        try:
            resp = requests.get(f"{self.base_url}/api/1/vehicles/{self.vin}", headers=self._headers(), timeout=8)
            if resp.status_code == 200:
                state = resp.json().get("response", {}).get("state", "").lower()
                return state == "online"
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
        logger.info("[Fleet] Sending wake command for %s...", self.vin)
        try:
            resp = requests.post(f"{self.base_url}/api/1/vehicles/{self.vin}/wake_up", headers=self._headers(), timeout=15)
            if resp.status_code == 200:
                state = resp.json().get("response", {}).get("state", "").lower()
                if state == "online":
                    return True

            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(3)
                if self.is_awake():
                    return True
            return False
        except Exception as e:
            logger.warning("[Fleet] Wake failed: %s", e)
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
            resp = requests.get(
                f"{self.base_url}/api/1/vehicles/{self.vin}/vehicle_data?endpoints=charge_state",
                headers=self._headers(),
                timeout=12
            )
            if resp.status_code == 401 and self.refresh_access_token():
                resp = requests.get(
                    f"{self.base_url}/api/1/vehicles/{self.vin}/vehicle_data?endpoints=charge_state",
                    headers=self._headers(),
                    timeout=12
                )

            if resp.status_code == 200:
                data = resp.json().get("response", {})
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
            logger.warning("[Fleet] Error fetching telemetry: %s", e)
            return self._last_state

    def _execute_command(self, action: str, params: Optional[dict] = None) -> bool:
        """
        Executes a vehicle command using tesla-control if key exists,
        or falls back to standard REST API endpoint.
        """
        if not self.vin:
            return False

        # Method A: Try tesla-control signed command if key_file exists
        if os.path.exists(self.key_file):
            try:
                cmd_map = {
                    "charge_start": ["charging-start"],
                    "charge_stop": ["charging-stop"],
                    "set_charging_amps": ["charging-set-amps", str(params.get("charging_amps", 5))]
                }
                if action in cmd_map:
                    cli_args = cmd_map[action]
                    env = os.environ.copy()
                    env["TESLA_AUTH_TOKEN"] = self.access_token
                    res = subprocess.run(
                        [self.binary_path, "-vin", self.vin, "-key-file", self.key_file] + cli_args,
                        capture_output=True, text=True, timeout=20, env=env
                    )
                    if res.returncode == 0:
                        logger.info("[Fleet] Signed command %s executed successfully via tesla-control.", action)
                        return True
                    else:
                        logger.warning("[Fleet] tesla-control signing error: %s. Falling back to REST...", res.stderr.strip())
            except Exception as e:
                logger.debug("[Fleet] tesla-control not available: %s. Falling back to REST...", e)

        # Method B: Standard REST endpoint
        try:
            url = f"{self.base_url}/api/1/vehicles/{self.vin}/command/{action}"
            resp = requests.post(url, headers=self._headers(), json=params or {}, timeout=15)
            if resp.status_code == 200 and resp.json().get("response", {}).get("result"):
                logger.info("[Fleet] REST command %s succeeded.", action)
                return True
            logger.warning("[Fleet] REST command %s returned: HTTP %d - %s", action, resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.error("[Fleet] Command %s exception: %s", action, e)
            return False

    def set_charging_amps(self, amps: int) -> bool:
        return self._execute_command("set_charging_amps", {"charging_amps": amps})

    def start_charging(self) -> bool:
        return self._execute_command("charge_start")

    def stop_charging(self) -> bool:
        return self._execute_command("charge_stop")

    def get_info(self) -> Dict[str, Any]:
        return {
            "provider": "fleet",
            "name": self.vehicle_name,
            "vin": self.vin or "Not Configured",
            "configured": bool(self.access_token or self.refresh_token),
            "description": "Tesla Developer Fleet API (Free Tier)"
        }


# ==============================================================================
# 3. HOME ASSISTANT BRIDGE (Local LAN, 100% Free, Direct Vehicle Charger Control)
# ==============================================================================
class HomeAssistantClient(BaseVehicleClient):
    """
    Connects to Home Assistant via local LAN REST API.
    Directly controls the Tesla Model 3's internal onboard charger.
    Does NOT require a smart plug or smart wall charger.
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.url = config.get("url", "http://homeassistant.local:8123").rstrip("/")
        self.token = config.get("token", "").strip()

        # HA Tesla Integration Entities
        self.soc_entity = config.get("soc_entity", "sensor.battery_sensor")
        self.amps_entity = config.get("amps_entity", "number.charging_amps")
        self.switch_entity = config.get("switch_entity", "switch.charger")

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
                logger.info("[HomeAssistant] Connected to %s", self.url)
                return True
            return False
        except Exception as e:
            logger.warning("[HomeAssistant] Could not reach %s: %s", self.url, e)
            return False

    def is_awake(self) -> bool:
        return True

    def wake_up(self, timeout: int = 60) -> bool:
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

            # Read current amps
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
# 4. SIMULATOR CLIENT (Demo & Testing Mode)
# ==============================================================================
class SimulatorVehicleClient(BaseVehicleClient):
    """Simulated Tesla Model 3 for instant testing."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.vehicle_name = "Model 3 (Simulated)"
        self.vin = "5YJ3E1EB8NF000001"
        self.battery_level = 68
        self.charge_amps = 5
        self.is_charging = False
        self.online = True

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
    provider = config.get("provider", "fleet").lower() if isinstance(config, dict) else "simulator"

    if provider == "ble":
        return TeslaControlBLEClient(config.get("ble", {}))
    elif provider == "fleet":
        return TeslaFleetDirectClient(config.get("fleet", {}))
    elif provider == "homeassistant":
        return HomeAssistantClient(config.get("homeassistant", {}))
    else:
        return SimulatorVehicleClient(config.get("simulator", {}))
