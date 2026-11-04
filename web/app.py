"""
Flask Web Dashboard & REST API
Provides a rich web interface for live solar/car telemetry,
headless Tesla authorization, Modbus testing, and live configuration.
"""

import os
import sys
import logging
import time
import yaml
from flask import Flask, render_template, jsonify, request
import teslapy
from solaredge import SolarEdgeClient, STATUS_MAP

logger = logging.getLogger(__name__)


def create_app(controller_ref=None, config_path="config.yaml"):
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.config["SECRET_KEY"] = os.urandom(24)

    # In-memory storage for active OAuth flow
    oauth_sessions = {}

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/status")
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
                cfg = yaml.safe_load(f)
            email = cfg.get("tesla", {}).get("email", "")

            if not email or email == "your-tesla-email@example.com":
                return jsonify({"authorized": False, "email": ""})

            t = teslapy.Tesla(email)
            authorized = t.authorized
            vehicles = []
            if authorized:
                try:
                    for v in t.vehicle_list():
                        vehicles.append(f"{v.get('display_name')} ({v.get('vin')})")
                except Exception:
                    pass
            return jsonify({"authorized": authorized, "email": email, "vehicles": vehicles})
        except Exception as e:
            return jsonify({"authorized": False, "error": str(e)})

    @app.route("/api/tesla/auth-url", methods=["POST"])
    def api_tesla_auth_url():
        data = request.get_json() or {}
        email = data.get("email", "").strip()
        if not email:
            return jsonify({"error": "Email is required"}), 400

        try:
            t = teslapy.Tesla(email)
            state = t.new_state()
            code_verifier = t.new_code_verifier()
            url = t.authorization_url(state=state, code_verifier=code_verifier)
            oauth_sessions[email] = {
                "state": state,
                "code_verifier": code_verifier
            }
            return jsonify({"url": url, "state": state, "code_verifier": code_verifier})
        except Exception as e:
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
            t.fetch_token(authorization_response=redirect_url, state=state, code_verifier=code_verifier)

            if t.authorized:
                # Update config.yaml with email
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
                if "tesla" not in cfg:
                    cfg["tesla"] = {}
                cfg["tesla"]["email"] = email
                with open(config_path, "w", encoding="utf-8") as f:
                    yaml.dump(cfg, f, default_flow_style=False)

                # Reinitialize controller's tesla client
                if controller_ref:
                    controller_ref.tesla.email = email
                    controller_ref.tesla.initialize()

                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": "Token exchange failed"}), 400
        except Exception as e:
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
                logs.append("❌ Connection FAILED!")
                logs.append("Checklist:")
                logs.append(f" 1. Verify inverter IP {host} is reachable on your LAN.")
                logs.append(f" 2. Ensure Modbus TCP is ENABLED on port {port} via SolarEdge SetApp.")
                logs.append(" 3. Ensure no other application (Home Assistant, etc.) is holding the Modbus connection.")
                return jsonify({"log": "\n".join(logs)})

            logs.append(f"✅ Modbus TCP Connected! Discovered SunSpec Base: {client.base_address}")
            logs.append("Reading inverter registers...")

            telemetry = client.read_generation()
            client.close()

            if telemetry.get("connected"):
                w = telemetry.get("power_watts", 0.0)
                desc = telemetry.get("status_desc", "UNKNOWN")
                code = telemetry.get("status_code", 0)
                logs.append("\n==========================================")
                logs.append(f" Inverter Status: {desc} (Code {code})")
                logs.append(f" Current Solar  : {w:,.1f} W ({(w/1000):.3f} kW)")
                logs.append("==========================================")
                if w > 0:
                    logs.append("☀️ Solar production actively detected!")
                elif code == 2:
                    logs.append("💤 Inverter is SLEEPING (Night/low light). Normal behavior.")
            else:
                logs.append("❌ Connected to port but could not decode SunSpec registers.")

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
