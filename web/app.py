"""
Flask Web Dashboard & REST API
Provides a rich web interface for live solar/car telemetry,
headless Tesla authorization, Modbus testing, and live configuration.
"""

import os
import sys
import logging
import time
import urllib.parse
import yaml
import requests
from flask import Flask, render_template, jsonify, request
from flask.json.provider import DefaultJSONProvider
import teslapy
from solaredge import SolarEdgeClient, STATUS_MAP
from tesla_fleet import TeslaFleetClient

logger = logging.getLogger(__name__)


class CustomJSONProvider(DefaultJSONProvider):
    """Ensures bytes objects are automatically decoded to UTF-8 strings."""
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="ignore")
        return super().default(obj)


def create_app(controller_ref=None, config_path="config.yaml"):
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.json_provider_class = CustomJSONProvider
    app.json = CustomJSONProvider(app)
    app.config["SECRET_KEY"] = os.urandom(24)


    # In-memory storage for active OAuth flow
    oauth_sessions = {}

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/setup-status")
    def api_setup_status():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            completed = cfg.get("setup_completed", False)
            tesla_auth = False
            email = cfg.get("tesla", {}).get("email", "")
            if email and email != "your-tesla-email@example.com":
                t = teslapy.Tesla(email)
                tesla_auth = t.authorized
            return jsonify({"setup_completed": completed, "tesla_authorized": tesla_auth})
        except Exception as e:
            return jsonify({"setup_completed": False, "tesla_authorized": False, "error": str(e)})

    @app.route("/api/setup-complete", methods=["POST"])
    def api_setup_complete():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            cfg["setup_completed"] = True
            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False)
            return jsonify({"success": True})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    def api_status():
        nonlocal controller_ref
        ctrl = controller_ref
        if not ctrl:
            return jsonify({"status": "initializing"})

        solar_data = ctrl.last_solar_data or {}
        tesla_data = ctrl.last_tesla_data or {}

        return jsonify({
            "solaredge": {
                "connected": solar_data.get("connected", False),
                "power_watts": solar_data.get("power_watts", 0.0),
                "status_code": solar_data.get("status_code", 0),
                "status_desc": solar_data.get("status_desc", "UNKNOWN"),
                "host": ctrl.solaredge.host,
                "port": ctrl.solaredge.port
            },
            "tesla": {
                "online": tesla_data.get("online", False),
                "charging_state": tesla_data.get("charging_state", "Unknown"),
                "plugged_in": tesla_data.get("plugged_in"),
                "battery_level": tesla_data.get("battery_level"),
                "charge_limit_soc": tesla_data.get("charge_limit_soc"),
                "charge_amps": tesla_data.get("charge_amps"),
                "charger_actual_current": tesla_data.get("charger_actual_current", 0),
                "charger_voltage": tesla_data.get("charger_voltage", 120),
            },
            "controller": {
                "mode": ctrl.mode,
                "is_charging": ctrl.is_charging,
                "current_set_amps": ctrl.current_set_amps,
                "start_threshold_w": ctrl.start_threshold_w,
                "baseline_house_w": ctrl.baseline_house_w,
                "stop_threshold_w": ctrl.stop_threshold_w,
                "min_amps": ctrl.min_amps,
                "max_amps": ctrl.max_amps,
            }
        })

    @app.route("/api/mode", methods=["POST"])
    def api_mode():
        nonlocal controller_ref
        data = request.get_json() or {}
        new_mode = data.get("mode", "auto").lower()

        if new_mode in ["auto", "force", "off"]:
            controller_ref.set_mode(new_mode)
            return jsonify({"success": True, "message": f"Mode changed to {new_mode.upper()}"})
        return jsonify({"success": False, "error": "Invalid mode"}), 400

    @app.route("/api/settings", methods=["GET", "POST"])
    def api_settings():
        nonlocal controller_ref
        if request.method == "GET":
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f)
                return jsonify(cfg)
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        # POST: Save settings
        try:
            new_data = request.get_json() or {}
            with open(config_path, "r", encoding="utf-8") as f:
                current_cfg = yaml.safe_load(f) or {}

            # Deep update
            for section in ["solaredge", "charging", "whatsapp"]:
                if section in new_data:
                    if section not in current_cfg:
                        current_cfg[section] = {}
                    current_cfg[section].update(new_data[section])

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(current_cfg, f, default_flow_style=False)

            # Reload into running controller
            controller_ref.reload_config(current_cfg)
            return jsonify({"success": True, "message": "Settings saved and applied!"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/tesla/status")
    def api_tesla_status():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            tesla_cfg = cfg.get("tesla", {})
            provider = tesla_cfg.get("provider", "teslapy").lower()

            if provider == "fleet" and (tesla_cfg.get("fleet", {}).get("access_token") or tesla_cfg.get("fleet", {}).get("client_id")):
                fleet_cfg = tesla_cfg.get("fleet", {})
                client = TeslaFleetClient(fleet_cfg)
                diag = client.test_connection()
                if diag.get("success"):
                    client.initialize()
                    if controller_ref and not controller_ref.tesla:
                        controller_ref.tesla = client
                return jsonify({
                    "provider": "fleet",
                    "authorized": diag.get("success", False),
                    "vehicles": diag.get("vehicles", []),
                    "region": fleet_cfg.get("region", "na"),
                    "has_token": bool(fleet_cfg.get("access_token")),
                    "masked_token": client._redact_token(fleet_cfg.get("access_token")),
                    "diagnostic_message": diag.get("message"),
                    "diagnostic_log": diag.get("log")
                })
            else:
                email = tesla_cfg.get("email", "")
                if not email or email == "your-tesla-email@example.com":
                    return jsonify({"provider": "teslapy", "authorized": False, "email": ""})

                t = teslapy.Tesla(email)
                authorized = t.authorized
                vehicles = []
                if authorized:
                    try:
                        for v in t.vehicle_list():
                            vehicles.append(f"{v.get('display_name')} ({v.get('vin')})")
                    except Exception:
                        pass
                return jsonify({
                    "provider": "teslapy",
                    "authorized": authorized,
                    "email": email,
                    "vehicles": vehicles
                })
        except Exception as e:
            return jsonify({"authorized": False, "error": str(e)})

    @app.route("/api/tesla/set-provider", methods=["POST"])
    def api_set_provider():
        nonlocal controller_ref
        data = request.get_json() or {}
        provider = data.get("provider", "teslapy").lower()

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        if "tesla" not in cfg:
            cfg["tesla"] = {}
        cfg["tesla"]["provider"] = provider
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)

        if controller_ref:
            try:
                controller_ref.tesla = create_tesla_client(cfg["tesla"])
                controller_ref.tesla.initialize()
            except Exception as e:
                logger.warning("Provider switch initialize deferred: %s", e)

        return jsonify({"success": True, "provider": provider})


    @app.route("/api/fleet/oauth-url", methods=["POST"])
    def api_fleet_oauth_url():
        data = request.get_json() or {}
        client_id = data.get("client_id", "").strip()
        redirect_uri = data.get("redirect_uri", "").strip() or "https://auth.tesla.com/void/callback"
        scopes = data.get("scope", "openid vehicle_device_data vehicle_charging_cmds offline_access")

        if not client_id:
            return jsonify({"error": "Client ID is required"}), 400

        state = os.urandom(16).hex()
        params = {
            "client_id": client_id,
            "locale": "en-US",
            "prompt": "login",
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scopes,
            "state": state
        }
        encoded_params = urllib.parse.urlencode(params)
        auth_url = f"https://auth.tesla.com/oauth2/v3/authorize?{encoded_params}"

        oauth_sessions[client_id] = {
            "state": state,
            "redirect_uri": redirect_uri
        }

        return jsonify({"url": auth_url, "state": state, "redirect_uri": redirect_uri})

    @app.route("/api/fleet/exchange-code", methods=["POST"])
    def api_fleet_exchange_code():
        nonlocal controller_ref
        data = request.get_json() or {}
        client_id = data.get("client_id", "").strip()
        client_secret = data.get("client_secret", "").strip()
        code_input = data.get("code", "").strip()
        redirect_uri = data.get("redirect_uri", "").strip() or "https://auth.tesla.com/void/callback"
        region = data.get("region", "na").strip().lower()

        if not (client_id and client_secret and code_input):
            return jsonify({"error": "Client ID, Client Secret, and redirect URL/code are required"}), 400

        # Extract code from URL if full URL was pasted
        code = code_input
        if "code=" in code_input:
            try:
                parsed = urllib.parse.urlparse(code_input)
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    code = qs["code"][0]
            except Exception:
                pass

        token_payload = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "audience": "https://fleet-api.prd.na.vn.cloud.tesla.com" if region == "na" else "https://fleet-api.prd.eu.vn.cloud.tesla.com"
        }

        try:
            logger.info("Exchanging authorization code for Fleet API Bearer access token...")
            resp = requests.post("https://auth.tesla.com/oauth2/v3/token", json=token_payload, timeout=20)
            if resp.status_code != 200:
                logger.error("Fleet token exchange failed: %d - %s", resp.status_code, resp.text)
                return jsonify({"success": False, "error": f"Tesla Auth returned error ({resp.status_code}): {resp.text}"}), 400

            token_data = resp.json()
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")

            if not access_token:
                return jsonify({"success": False, "error": "No access_token returned by Tesla"}), 400

            # Update configuration
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}

            if "tesla" not in cfg:
                cfg["tesla"] = {}
            cfg["tesla"]["provider"] = "fleet"
            cfg["tesla"]["fleet"] = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "region": region,
                "redirect_uri": redirect_uri
            }

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(cfg, f, default_flow_style=False)

            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass

            # Test connection with new token
            fleet_client = TeslaFleetClient(cfg["tesla"]["fleet"])
            diag = fleet_client.test_connection()
            if diag.get("success"):
                fleet_client.initialize()
                if controller_ref:
                    controller_ref.tesla = fleet_client

            return jsonify({
                "success": True,
                "authorized": diag.get("success", False),
                "message": diag.get("message"),
                "access_token": access_token,
                "refresh_token": refresh_token,
                "vehicles": diag.get("vehicles", []),
                "diagnostic_log": diag.get("log")
            })

        except Exception as e:
            logger.error("Exception during Fleet token exchange: %s", e, exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/fleet/configure", methods=["POST"])
    def api_fleet_configure():
        nonlocal controller_ref
        data = request.get_json() or {}
        access_token = data.get("access_token", "").strip()
        refresh_token = data.get("refresh_token", "").strip()
        client_id = data.get("client_id", "").strip()
        client_secret = data.get("client_secret", "").strip()
        region = data.get("region", "na").strip().lower()

        if not (access_token or refresh_token):
            return jsonify({"success": False, "error": "Access token or Refresh token is required."}), 400

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

        if "tesla" not in cfg:
            cfg["tesla"] = {}
        cfg["tesla"]["provider"] = "fleet"
        cfg["tesla"]["fleet"] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "client_id": client_id or cfg.get("tesla", {}).get("fleet", {}).get("client_id"),
            "client_secret": client_secret or cfg.get("tesla", {}).get("fleet", {}).get("client_secret"),
            "region": region
        }

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, default_flow_style=False)

        try:
            os.chmod(config_path, 0o600)
        except Exception:
            pass

        # Test connection
        fleet_client = TeslaFleetClient(cfg["tesla"]["fleet"])
        diag = fleet_client.test_connection()
        if diag.get("success"):
            fleet_client.initialize()
            if controller_ref:
                controller_ref.tesla = fleet_client
            return jsonify({
                "success": True,
                "authorized": True,
                "message": diag.get("message"),
                "vehicles": diag.get("vehicles", []),
                "diagnostic_log": diag.get("log")
            })
        else:
            return jsonify({
                "success": False,
                "authorized": False,
                "error": diag.get("message"),
                "diagnostic_log": diag.get("log")
            }), 400

    @app.route("/api/tesla/auth-url", methods=["POST"])
    def api_tesla_auth_url():
        data = request.get_json() or {}
        email = data.get("email", "").strip()
        if not email:
            return jsonify({"error": "Email is required"}), 400

        try:
            t = teslapy.Tesla(email)
            raw_state = t.new_state()
            raw_verifier = t.new_code_verifier()

            # Ensure raw_verifier passed to TeslaPy's authorization_url is BYTES (not str)
            verifier_bytes = raw_verifier if isinstance(raw_verifier, bytes) else raw_verifier.encode("utf-8")
            state_bytes = raw_state if isinstance(raw_state, bytes) else raw_state.encode("utf-8")

            url = t.authorization_url(state=state_bytes, code_verifier=verifier_bytes)
            if isinstance(url, bytes):
                url = url.decode("utf-8")

            state_str = raw_state.decode("utf-8") if isinstance(raw_state, bytes) else str(raw_state)
            code_verifier_str = raw_verifier.decode("utf-8") if isinstance(raw_verifier, bytes) else str(raw_verifier)

            oauth_sessions[email] = {
                "state": state_str,
                "code_verifier": code_verifier_str,
                "raw_verifier": verifier_bytes,
                "raw_state": state_bytes
            }
            return jsonify({"url": url, "state": state_str, "code_verifier": code_verifier_str})
        except Exception as e:
            logger.error("Exception generating Tesla auth URL: %s", e, exc_info=True)
            return jsonify({"error": str(e)}), 500

    @app.route("/api/tesla/complete-auth", methods=["POST"])
    def api_tesla_complete_auth():
        nonlocal controller_ref
        data = request.get_json() or {}
        email = data.get("email", "").strip()
        redirect_url = data.get("redirect_url", "").strip()
        state = data.get("state")
        code_verifier = data.get("code_verifier")

        if not (email and redirect_url):
            return jsonify({"error": "Missing parameters"}), 400

        try:
            t = teslapy.Tesla(email)
            cached_session = oauth_sessions.get(email, {})
            verifier_to_use = cached_session.get("raw_verifier") or code_verifier
            state_to_use = cached_session.get("raw_state") or state

            try:
                t.fetch_token(authorization_response=redirect_url, state=state_to_use, code_verifier=verifier_to_use)
            except Exception:
                # Fallback to string or bytes conversion
                alt_verifier = code_verifier.encode("utf-8") if isinstance(code_verifier, str) else str(code_verifier)
                t.fetch_token(authorization_response=redirect_url, state=state, code_verifier=alt_verifier)

            if t.authorized:
                # Update config.yaml with email and set provider to teslapy
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "tesla" not in cfg:
                    cfg["tesla"] = {}
                cfg["tesla"]["provider"] = "teslapy"
                cfg["tesla"]["email"] = email
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, default_flow_style=False)

                # Reinitialize controller's tesla client as TeslaController
                if controller_ref:
                    controller_ref.tesla = TeslaController(email=email)
                    controller_ref.tesla.initialize()

                vehicles = []
                try:
                    for v in t.vehicle_list():
                        vehicles.append(f"{v.get('display_name')} (VIN: {v.get('vin')})")
                except Exception:
                    pass

                return jsonify({"success": True, "vehicles": vehicles})
            else:
                return jsonify({"success": False, "error": "Token exchange failed"}), 400
        except Exception as e:
            logger.error("Exception in complete-auth: %s", e, exc_info=True)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/test-modbus")
    def api_test_modbus():
        logs = []
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            se_cfg = cfg.get("solaredge", {})
            host = se_cfg.get("host", "192.168.40.169")
            port = se_cfg.get("port", 1502)
            unit_id = se_cfg.get("unit_id", 1)

            logs.append(f"Target Inverter: {host}:{port} (Unit ID: {unit_id})")
            logs.append("Connecting to Modbus TCP...")

            client = SolarEdgeClient(host=host, port=port, unit_id=unit_id, timeout=5)
            if not client.connect():
                logs.append("[ERROR] Connection failed.")
                logs.append("Checklist:")
                logs.append(f" 1. Verify inverter IP {host} is reachable on your LAN.")
                logs.append(f" 2. Ensure Modbus TCP is ENABLED on port {port} via SolarEdge SetApp.")
                logs.append(" 3. Ensure no other application (Home Assistant, etc.) is holding the Modbus connection.")
                return jsonify({"log": "\n".join(logs)})

            logs.append(f"[OK] Modbus TCP Connected. SunSpec Base Address: {client.base_address}")
            logs.append("Reading inverter registers...")

            telemetry = client.read_generation()
            client.close()

            if telemetry.get("connected"):
                w = telemetry.get("power_watts", 0.0)
                desc = telemetry.get("status_desc", "UNKNOWN")
                code = telemetry.get("status_code", 0)
                logs.append("\n------------------------------------------")
                logs.append(f" Inverter Status: {desc} (Code {code})")
                logs.append(f" Current Solar  : {w:,.1f} W ({(w/1000):.3f} kW)")
                logs.append("------------------------------------------")
                if w > 0:
                    logs.append("[OK] Solar production actively detected.")
                elif code == 2:
                    logs.append("[INFO] Inverter is in SLEEPING mode (Night or low light).")
            else:
                logs.append("[ERROR] Connected to port but could not decode SunSpec registers.")

            return jsonify({"log": "\n".join(logs)})
        except Exception as e:
            logs.append(f"Exception during test: {e}")
            return jsonify({"log": "\n".join(logs)})

    @app.route("/api/logs")
    def api_logs():
        log_paths = [
            "solaredge_tesla.log",
            "/var/log/solaredge-tesla.log",
            "/opt/solaredge-tesla/solaredge_tesla.log"
        ]
        content = "No log file found yet."
        for p in log_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        content = "".join(lines[-150:])
                    break
                except Exception as e:
                    content = f"Error reading log file: {e}"
        return jsonify({"logs": content})

    return app
