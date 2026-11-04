"""
Tesla Fleet API Client
Official Tesla Fleet REST API implementation with diagnostic telemetry,
automatic fallback routing, and transparent error reporting.
"""

import logging
import time
from typing import Optional, Dict, Any, List
import requests

logger = logging.getLogger(__name__)

REGIONS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
}
OWNER_API_BASE = "https://owner-api.teslamotors.com"
AUTH_URL = "https://auth.tesla.com/oauth2/v3/token"


class TeslaFleetClient:
    """Controls Tesla vehicles via the official Tesla Fleet API with owner-api fallback."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.access_token: Optional[str] = config.get("access_token")
        self.refresh_token: Optional[str] = config.get("refresh_token")
        self.client_id: Optional[str] = config.get("client_id")
        self.client_secret: Optional[str] = config.get("client_secret")
        self.region: str = config.get("region", "na").lower()
        self.base_url: str = REGIONS.get(self.region, REGIONS["na"])
        self.vin: Optional[str] = config.get("vin")

        self.vehicle_id: Optional[str] = None
        self.vehicle_info: Optional[Dict[str, Any]] = None
        self.last_error: Optional[str] = None
        self.last_status_code: Optional[int] = None
        self.active_base_url: str = self.base_url
        self._token_expires_at: float = 0.0
        self._last_known_state: Optional[Dict[str, Any]] = None
        self._last_wake_attempt: float = 0.0

    def _redact_token(self, token: Optional[str]) -> str:
        if not token or len(token) < 12:
            return "[empty]"
        return f"{token[:6]}...{token[-4:]}"

    def refresh_access_token(self) -> bool:
        """Exchange refresh_token for a new access_token."""
        if not self.refresh_token:
            return False

        logger.info("Refreshing Tesla Fleet API access token using refresh_token %s...",
                    self._redact_token(self.refresh_token))
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.refresh_token,
        }
        if self.client_id:
            payload["client_id"] = self.client_id
        if self.client_secret:
            payload["client_secret"] = self.client_secret

        try:
            resp = requests.post(AUTH_URL, json=payload, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("access_token")
                if data.get("refresh_token"):
                    self.refresh_token = data.get("refresh_token")
                expires_in = data.get("expires_in", 28800)
                self._token_expires_at = time.time() + expires_in - 300
                logger.info("Successfully refreshed Fleet API token (valid for %d seconds).", expires_in)
                self.last_error = None
                return True
            else:
                self.last_error = f"Token refresh failed ({resp.status_code}): {resp.text}"
                logger.error(self.last_error)
                return False
        except Exception as e:
            self.last_error = f"Exception refreshing Fleet API token: {e}"
            logger.error(self.last_error)
            return False

    def _get_headers(self) -> Dict[str, str]:
        if self.refresh_token and time.time() >= self._token_expires_at:
            self.refresh_access_token()

        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Tesla-Solar-Optimizer/2.0"
        }

    def test_connection(self) -> Dict[str, Any]:
        """Diagnostic probe that tests endpoints and returns detailed results."""
        if not self.access_token and self.refresh_token:
            self.refresh_access_token()

        if not self.access_token:
            return {
                "success": False,
                "status_code": 400,
                "message": "No access_token or refresh_token is configured.",
                "details": "Enter your Client ID and Client Secret or paste a Bearer token."
            }

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "User-Agent": "Tesla-Solar-Optimizer/2.0"
        }

        # Try fleet-api endpoint first, then owner-api fallback
        candidate_urls = [
            (self.base_url, "Official Fleet API (" + self.region.upper() + ")"),
            (OWNER_API_BASE, "Tesla Owner API Fallback")
        ]

        attempt_logs = []
        for base, name in candidate_urls:
            url = f"{base}/api/1/vehicles"
            attempt_logs.append(f"Testing {name}: GET {url}")
            try:
                resp = requests.get(url, headers=headers, timeout=12)
                attempt_logs.append(f"  -> HTTP {resp.status_code}")

                if resp.status_code == 200:
                    vehicles = resp.json().get("response", [])
                    self.active_base_url = base
                    self.last_error = None
                    self.last_status_code = 200
                    veh_names = [f"{v.get('display_name')} (VIN: {v.get('vin')})" for v in vehicles]
                    return {
                        "success": True,
                        "status_code": 200,
                        "active_endpoint": base,
                        "endpoint_name": name,
                        "message": f"Connected successfully via {name}!",
                        "vehicles": veh_names,
                        "vehicle_count": len(vehicles),
                        "log": "\n".join(attempt_logs)
                    }
                else:
                    attempt_logs.append(f"  -> Response body: {resp.text}")
                    self.last_status_code = resp.status_code
                    self.last_error = f"{resp.status_code}: {resp.text}"
            except Exception as ex:
                attempt_logs.append(f"  -> Network error: {ex}")
                self.last_error = str(ex)

        return {
            "success": False,
            "status_code": self.last_status_code or 500,
            "message": f"Tesla API query failed: {self.last_error}",
            "log": "\n".join(attempt_logs)
        }

    def initialize(self) -> bool:
        """Fetch vehicles list and select target vehicle."""
        diag = self.test_connection()
        if not diag.get("success"):
            logger.warning("Fleet initialize failed: %s", diag.get("message"))
            return False

        try:
            url = f"{self.active_base_url}/api/1/vehicles"
            resp = requests.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code != 200:
                return False

            vehicles = resp.json().get("response", [])
            if not vehicles:
                logger.warning("Tesla account has 0 vehicles returned.")
                return False

            if self.vin:
                match = [v for v in vehicles if v.get("vin") == self.vin or v.get("display_name") == self.vin]
                self.vehicle_info = match[0] if match else vehicles[0]
            else:
                self.vehicle_info = vehicles[0]

            self.vehicle_id = str(self.vehicle_info.get("id"))
            logger.info("Fleet API active vehicle: %s (VIN: %s, ID: %s)",
                        self.vehicle_info.get("display_name"),
                        self.vehicle_info.get("vin"),
                        self.vehicle_id)
            return True
        except Exception as e:
            logger.error("Exception during initialize: %s", e)
            return False

    def is_awake(self) -> bool:
        """Check if vehicle is online without waking."""
        if not self.vehicle_id:
            return False
        try:
            url = f"{self.active_base_url}/api/1/vehicles/{self.vehicle_id}"
            resp = requests.get(url, headers=self._get_headers(), timeout=10)
            if resp.status_code == 200:
                v = resp.json().get("response", {})
                self.vehicle_info = v
                return v.get("state") == "online"
        except Exception:
            pass
        return False

    def wake_up(self, timeout: int = 60) -> bool:
        """Wake up vehicle via Fleet API."""
        if not self.vehicle_id:
            return False

        now = time.time()
        if now - self._last_wake_attempt < 30:
            return self.is_awake()

        self._last_wake_attempt = now
        logger.info("Waking up vehicle via Fleet API (%s)...", self.vehicle_id)

        try:
            url = f"{self.active_base_url}/api/1/vehicles/{self.vehicle_id}/wake_up"
            resp = requests.post(url, headers=self._get_headers(), timeout=15)
            if resp.status_code == 200 and resp.json().get("response", {}).get("state") == "online":
                logger.info("Vehicle reported online immediately.")
                return True

            end_time = time.time() + timeout
            while time.time() < end_time:
                time.sleep(3)
                if self.is_awake():
                    logger.info("Vehicle is now online.")
                    return True

            logger.warning("Vehicle wake-up timed out after %d seconds.", timeout)
            return False
        except Exception as e:
            logger.warning("Error during wake_up request: %s", e)
            return False

    def get_charge_state(self, wake_if_needed: bool = False) -> Optional[Dict[str, Any]]:
        """Retrieve vehicle charging state."""
        if not self.vehicle_id:
            return None

        online = self.is_awake()
        if not online:
            if wake_if_needed:
                if not self.wake_up():
                    return self._last_known_state
            else:
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
            url = f"{self.active_base_url}/api/1/vehicles/{self.vehicle_id}/vehicle_data?endpoints=charge_state"
            resp = requests.get(url, headers=self._get_headers(), timeout=15)
            if resp.status_code != 200:
                logger.warning("Could not fetch vehicle_data: %d %s", resp.status_code, resp.text)
                return self._last_known_state

            data = resp.json().get("response", {})
            charge_state = data.get("charge_state", {})

            charging_state = charge_state.get("charging_state")
            plugged_in = charging_state != "Disconnected" and charge_state.get("charge_port_door_open", False)

            state_info = {
                "online": True,
                "charging_state": charging_state,
                "plugged_in": plugged_in,
                "battery_level": charge_state.get("battery_level"),
                "charge_limit_soc": charge_state.get("charge_limit_soc"),
                "charge_amps": charge_state.get("charge_amps"),
                "charge_current_request": charge_state.get("charge_current_request"),
                "charger_actual_current": charge_state.get("charger_actual_current", 0),
                "charger_voltage": charge_state.get("charger_voltage", 120),
                "time_to_full_charge": charge_state.get("time_to_full_charge", 0.0),
                "timestamp": time.time()
            }
            self._last_known_state = state_info
            return state_info

        except Exception as e:
            logger.warning("Exception fetching charge_state via Fleet API: %s", e)
            return self._last_known_state

    def _send_command(self, command_path: str, payload: Optional[dict] = None) -> bool:
        if not self.vehicle_id:
            return False

        if not self.is_awake() and not self.wake_up():
            return False

        try:
            url = f"{self.active_base_url}/api/1/vehicles/{self.vehicle_id}/command/{command_path}"
            resp = requests.post(url, headers=self._get_headers(), json=payload or {}, timeout=15)
            if resp.status_code == 200:
                res = resp.json().get("response", {})
                result = bool(res.get("result", False))
                logger.info("Fleet API command '%s' result: %s (reason: %s)",
                            command_path, result, res.get("reason", "none"))
                return result
            else:
                logger.error("Fleet API command '%s' failed: %d - %s", command_path, resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("Exception sending Fleet API command '%s': %s", command_path, e)
            return False

    def set_charging_amps(self, amps: int) -> bool:
        """Set charge current in Amps."""
        return self._send_command("set_charging_amps", {"charging_amps": amps})

    def start_charging(self) -> bool:
        """Send charge_start command."""
        return self._send_command("charge_start")

    def stop_charging(self) -> bool:
        """Send charge_stop command."""
        return self._send_command("charge_stop")
