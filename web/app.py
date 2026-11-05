"""
Minimalist Flask Web Dashboard & REST API
resilient, clean, and zero-emoji interface for SolarEdge - Tesla Solar Charging Optimizer.
Supports free methods: Local Bluetooth (BLE), Official Tesla Fleet API, Home Assistant, and Simulator.
"""

import os
import sys
import logging
import time
import yaml
import requests
from flask import Flask, render_template, jsonify, request
from flask.json.provider import DefaultJSONProvider
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization

from solaredge import SolarEdgeClient, STATUS_MAP
from vehicle import create_vehicle_client, BaseVehicleClient, REGIONS, AUTH_URL

logger = logging.getLogger(__name__)


class CustomJSONProvider(DefaultJSONProvider):
    """Ensures bytes objects are automatically decoded to UTF-8 strings."""
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="ignore")
        return super().default(obj)


def generate_or_get_ec_keys(base_dir="."):
    """Generate or retrieve EC SECP256R1 key pair for Tesla Virtual Key."""
    priv_path = os.path.join(base_dir, "private-key.pem")
    well_known_dir = os.path.join(base_dir, ".well-known", "appspecific")
    os.makedirs(well_known_dir, exist_ok=True)
    pub_path = os.path.join(well_known_dir, "com.tesla.3p.public-key.pem")

    if os.path.exists(pub_path) and os.path.exists(priv_path):
        try:
            with open(pub_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    return content
        except Exception:
            pass

    try:
        key = ec.generate_private_key(ec.SECP256R1())
        priv_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption()
        )
        pub_pem = key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        with open(priv_path, "wb") as f:
            f.write(priv_pem)
        with open(pub_path, "wb") as f:
            f.write(pub_pem)
        try:
            os.chmod(priv_path, 0o600)
        except Exception:
            pass
        return pub_pem.decode("utf-8").strip()
    except Exception as e:
        logger.error("Failed to generate EC keys: %s", e)
        return ""


def create_app(controller_ref=None, config_path="config.yaml"):
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.json_provider_class = CustomJSONProvider
    app.json = CustomJSONProvider(app)
    app.config["SECRET_KEY"] = os.urandom(24)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/.well-known/appspecific/com.tesla.3p.public-key.pem")
    def serve_public_key():
        pub = generate_or_get_ec_keys()
        if pub:
            return pub, 200, {"Content-Type": "text/plain"}
        return "Public key not generated", 404

    @app.route("/api/status")
    def api_status():
        nonlocal controller_ref
        ctrl = controller_ref
        if not ctrl:
            return jsonify({"status": "initializing"})

        solar_data = ctrl.last_solar_data or {}
        tesla_data = ctrl.last_tesla_data or {}

        solar_w = solar_data.get("power_watts", 0.0)
        baseline_w = ctrl.baseline_house_w
        surplus_w = max(0.0, solar_w - baseline_w)

        veh_info = ctrl.tesla.get_info() if ctrl.tesla else {}

        return jsonify({
            "solaredge": {
                "connected": solar_data.get("connected", False),
                "power_watts": solar_w,
                "power_kw": round(solar_w / 1000.0, 2),
                "status_code": solar_data.get("status_code", 0),
                "status_desc": solar_data.get("status_desc", "STANDBY"),
                "host": ctrl.solaredge.host,
                "port": ctrl.solaredge.port
            },
            "vehicle": {
                "provider": veh_info.get("provider", "unknown"),
                "name": veh_info.get("name", "Tesla Model 3"),
                "vin": veh_info.get("vin", ""),
                "online": tesla_data.get("online", False),
                "charging_state": tesla_data.get("charging_state", "Disconnected"),
                "plugged_in": tesla_data.get("plugged_in"),
                "battery_level": tesla_data.get("battery_level"),
                "charge_limit_soc": tesla_data.get("charge_limit_soc", 90),
                "charge_amps": tesla_data.get("charge_amps", 5),
                "charger_actual_current": tesla_data.get("charger_actual_current", 0),
                "charger_voltage": tesla_data.get("charger_voltage", 120),
                "time_to_full_charge": tesla_data.get("time_to_full_charge", 0.0)
            },
            "controller": {
                "mode": ctrl.mode,
                "is_charging": ctrl.is_charging,
                "current_set_amps": ctrl.current_set_amps or 5,
                "target_amps": ctrl.target_amps,
                "start_threshold_w": ctrl.start_threshold_w,
                "baseline_house_w": baseline_w,
                "stop_threshold_w": ctrl.stop_threshold_w,
                "surplus_watts": surplus_w,
                "surplus_kw": round(surplus_w / 1000.0, 2),
                "min_amps": ctrl.min_amps,
                "max_amps": ctrl.max_amps,
                "voltage": ctrl.voltage
            }
        })

    @app.route("/api/mode", methods=["POST"])
    def api_mode():
        nonlocal controller_ref
        data = request.get_json() or {}
        new_mode = data.get("mode", "auto").lower()

        if new_mode in ["auto", "force", "off"]:
            if controller_ref:
                controller_ref.set_mode(new_mode)
            return jsonify({"success": True, "mode": new_mode, "message": f"Mode changed to {new_mode.upper()}"})
        return jsonify({"success": False, "error": "Invalid mode. Must be 'auto', 'force', or 'off'."}), 400

    @app.route("/api/config", methods=["GET", "POST"])
    def api_config():
        nonlocal controller_ref
        if request.method == "GET":
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                return jsonify(cfg)
            except Exception as e:
                return jsonify({"error": str(e)}), 500

        # POST: Save updated configuration
        try:
            updates = request.get_json() or {}
            with open(config_path, "r", encoding="utf-8") as f:
                current_cfg = yaml.safe_load(f) or {}

            for section in ["solaredge", "vehicle", "charging", "whatsapp"]:
                if section in updates:
                    if section not in current_cfg:
                        current_cfg[section] = {}
                    if isinstance(updates[section], dict):
                        current_cfg[section].update(updates[section])
                    else:
                        current_cfg[section] = updates[section]

            with open(config_path, "w", encoding="utf-8") as f:
                yaml.dump(current_cfg, f, default_flow_style=False)

            try:
                os.chmod(config_path, 0o600)
            except Exception:
                pass

            if controller_ref:
                controller_ref.reload_config(current_cfg)

            return jsonify({"success": True, "message": "Configuration saved and active."})
        except Exception as e:
            logger.error("Error saving config: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/fleet/generate-keys", methods=["POST"])
    def api_fleet_generate_keys():
        try:
            pub = generate_or_get_ec_keys()
            return jsonify({
                "success": True,
                "public_key": pub,
                "message": "Public/private EC SECP256R1 keys active in .well-known/appspecific/com.tesla.3p.public-key.pem"
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/fleet/register-partner", methods=["POST"])
    def api_fleet_register_partner():
        data = request.get_json() or {}
        client_id = data.get("client_id", "").strip()
        client_secret = data.get("client_secret", "").strip()
        domain = data.get("domain", "").strip()
        region = data.get("region", "na").strip().lower()

        if not (client_id and client_secret and domain):
            return jsonify({"success": False, "error": "Client ID, Client Secret, and Domain are required."}), 400

        clean_domain = domain.replace("https://", "").replace("http://", "").split("/")[0].strip()
        base_url = REGIONS.get(region, REGIONS["na"])
        logs = []

        # Step 1: Obtain Partner Token
        logs.append(f"Requesting Partner Token from Tesla OAuth for Client ID {client_id[:6]}...")
        try:
            token_payload = {
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "openid vehicle_device_data vehicle_cmds vehicle_charging_cmds",
                "audience": base_url
            }
            token_resp = requests.post(AUTH_URL, data=token_payload, timeout=15)
            logs.append(f"OAuth response: HTTP {token_resp.status_code}")
            if token_resp.status_code != 200:
                logs.append(f"OAuth error: {token_resp.text}")
                return jsonify({
                    "success": False,
                    "error": f"Failed to acquire Partner Token ({token_resp.status_code}): {token_resp.text}",
                    "log": "\n".join(logs)
                }), 400

            partner_token = token_resp.json().get("access_token")
        except Exception as ex:
            logs.append(f"Exception during OAuth: {ex}")
            return jsonify({"success": False, "error": str(ex), "log": "\n".join(logs)}), 500

        # Step 2: Register partner domain with Tesla
        reg_url = f"{base_url}/api/1/partner_accounts"
        logs.append(f"Registering domain '{clean_domain}' with Tesla at {reg_url}...")
        try:
            reg_resp = requests.post(
                reg_url,
                headers={
                    "Authorization": f"Bearer {partner_token}",
                    "Content-Type": "application/json"
                },
                json={"domain": clean_domain},
                timeout=20
            )
            logs.append(f"Tesla response: HTTP {reg_resp.status_code}")
            logs.append(f"Response body: {reg_resp.text}")

            if reg_resp.status_code in [200, 201]:
                # Update config.yaml with client_id, client_secret, domain
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "vehicle" not in cfg:
                    cfg["vehicle"] = {}
                cfg["vehicle"]["provider"] = "fleet"
                if "fleet" not in cfg["vehicle"]:
                    cfg["vehicle"]["fleet"] = {}
                cfg["vehicle"]["fleet"]["client_id"] = client_id
                cfg["vehicle"]["fleet"]["client_secret"] = client_secret
                cfg["vehicle"]["fleet"]["domain"] = clean_domain
                cfg["vehicle"]["fleet"]["region"] = region
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, default_flow_style=False)

                logs.append("[SUCCESS] Partner account registered! HTTP 412 is cleared.")
                logs.append(f"Virtual Key link: https://tesla.com/_ak/{clean_domain}")
                return jsonify({
                    "success": True,
                    "message": f"Partner account registered successfully with domain {clean_domain}!",
                    "domain": clean_domain,
                    "enroll_url": f"https://tesla.com/_ak/{clean_domain}",
                    "log": "\n".join(logs)
                })
            else:
                return jsonify({
                    "success": False,
                    "error": f"Registration returned HTTP {reg_resp.status_code}: {reg_resp.text}",
                    "log": "\n".join(logs)
                }), 400
        except Exception as ex:
            logs.append(f"Exception during registration: {ex}")
            return jsonify({"success": False, "error": str(ex), "log": "\n".join(logs)}), 500

    @app.route("/api/test-inverter", methods=["POST"])
    def api_test_inverter():
        data = request.get_json() or {}
        host = data.get("host")
        port = data.get("port")
        unit_id = data.get("unit_id")

        if not host:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                se = cfg.get("solaredge", {})
                host = se.get("host", "192.168.40.169")
                port = int(se.get("port", 1502))
                unit_id = int(se.get("unit_id", 1))
            except Exception:
                host = "192.168.40.169"
                port = 1502
                unit_id = 1

        port = int(port or 1502)
        unit_id = int(unit_id or 1)

        logs = [
            f"Testing SolarEdge Inverter connection...",
            f"Target: {host}:{port} (Unit ID: {unit_id})"
        ]

        client = SolarEdgeClient(host=host, port=port, unit_id=unit_id, timeout=4)
        if not client.connect():
            logs.append("[FAIL] Could not establish Modbus TCP connection.")
            logs.append("Troubleshooting tips:")
            logs.append(f"  1. Ensure inverter IP ({host}) is reachable from this server.")
            logs.append(f"  2. Verify Modbus TCP is enabled on port {port} using SolarEdge SetApp.")
            logs.append("  3. Check that no other LAN process has an exclusive lock on port 1502.")
            return jsonify({"success": False, "log": "\n".join(logs)})

        logs.append(f"[OK] Modbus TCP connected. SunSpec Base Address: {client.base_address}")
        reading = client.read_generation()
        client.close()

        if reading.get("connected"):
            w = reading.get("power_watts", 0.0)
            desc = reading.get("status_desc", "UNKNOWN")
            code = reading.get("status_code", 0)
            logs.append(f"[OK] Inverter Status: {desc} (Code {code})")
            logs.append(f"[OK] Real-time Generation: {w:,.1f} W ({(w/1000):.3f} kW)")
            return jsonify({
                "success": True,
                "power_watts": w,
                "status_desc": desc,
                "status_code": code,
                "log": "\n".join(logs)
            })
        else:
            logs.append("[FAIL] Modbus connected but SunSpec registers could not be decoded.")
            return jsonify({"success": False, "log": "\n".join(logs)})

    @app.route("/api/test-vehicle", methods=["POST"])
    def api_test_vehicle():
        nonlocal controller_ref
        data = request.get_json() or {}
        provider = data.get("provider")

        veh_cfg = {}
        if provider:
            veh_cfg = data
        else:
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                veh_cfg = cfg.get("vehicle", {})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500

        provider_name = veh_cfg.get("provider", "simulator").lower()
        logs = [f"Testing vehicle connection with provider '{provider_name}'..."]

        try:
            test_client = create_vehicle_client(veh_cfg)
            init_ok = test_client.initialize()
            if not init_ok:
                logs.append(f"[FAIL] Vehicle provider '{provider_name}' failed to initialize.")
                if provider_name == "ble":
                    logs.append("Tip: Ensure VIN is entered, private-key.pem exists, and Bluetooth is active within ~30ft of the car.")
                elif provider_name == "fleet":
                    logs.append("Tip: Ensure Client ID, Client Secret, and Refresh Token are configured and partner domain is registered.")
                elif provider_name == "homeassistant":
                    logs.append("Tip: Ensure Home Assistant URL and Long-Lived Access Token are valid.")
                return jsonify({"success": False, "log": "\n".join(logs)})

            logs.append(f"[OK] Provider initialized: {test_client.get_info().get('name')}")
            state = test_client.get_charge_state(wake_if_needed=False) or {}
            online = state.get("online", False)
            charging_state = state.get("charging_state", "Unknown")
            battery = state.get("battery_level")

            logs.append(f"[OK] Vehicle Online: {online}")
            logs.append(f"[OK] Charging State: {charging_state}")
            logs.append(f"[OK] Battery Level: {battery}%" if battery is not None else "[INFO] Battery level unavailable (vehicle asleep)")

            if controller_ref:
                controller_ref.tesla = test_client

            return jsonify({
                "success": True,
                "online": online,
                "charging_state": charging_state,
                "battery_level": battery,
                "name": test_client.get_info().get("name"),
                "log": "\n".join(logs)
            })
        except Exception as ex:
            logger.error("Exception testing vehicle: %s", ex)
            logs.append(f"[ERROR] Exception: {ex}")
            return jsonify({"success": False, "log": "\n".join(logs)})

    @app.route("/api/logs")
    def api_logs():
        log_paths = [
            "solaredge_tesla.log",
            "/var/log/solaredge-tesla.log",
            "/opt/solaredge-tesla/solaredge_tesla.log"
        ]
        content = "No logs recorded yet."
        for p in log_paths:
            if os.path.exists(p):
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                        content = "".join(lines[-120:])
                    break
                except Exception as e:
                    content = f"Error reading log file: {e}"
        return jsonify({"logs": content})

    return app
