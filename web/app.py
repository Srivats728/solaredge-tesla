"""
Minimalist Flask Web Dashboard & REST API
Clean, resilient, and responsive interface for SolarEdge - Tesla Solar Charging Optimizer.
Zero emojis, strict Tesla monochrome styling, and robust error handling.
"""

import os
import sys
import logging
import time
import yaml
from flask import Flask, render_template, jsonify, request
from flask.json.provider import DefaultJSONProvider

from solaredge import SolarEdgeClient, STATUS_MAP
from vehicle import create_vehicle_client, BaseVehicleClient

logger = logging.getLogger(__name__)


class CustomJSONProvider(DefaultJSONProvider):
    """Ensures bytes objects are automatically decoded to UTF-8 strings."""
    def default(self, obj):
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="ignore")
        return super().default(obj)


def mask_secret(val: str) -> str:
    """Mask secret tokens/keys showing only first 4 and last 4 chars."""
    if not val or len(val) < 8:
        return "••••••••" if val else ""
    return f"{val[:4]}••••{val[-4:]}"


def create_app(controller_ref=None, config_path="config.yaml"):
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.json_provider_class = CustomJSONProvider
    app.json = CustomJSONProvider(app)
    app.config["SECRET_KEY"] = os.urandom(24)

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

        solar_w = solar_data.get("power_watts", 0.0)
        baseline_w = ctrl.baseline_house_w
        surplus_w = max(0.0, solar_w - baseline_w)

        # Vehicle metadata
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

            # Deep merge sections
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

            # Live reload into running controller
            if controller_ref:
                controller_ref.reload_config(current_cfg)

            return jsonify({"success": True, "message": "Configuration saved and active."})
        except Exception as e:
            logger.error("Error saving config: %s", e)
            return jsonify({"success": False, "error": str(e)}), 500

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
                if provider_name == "tessie":
                    logs.append("Tip: Ensure your Tessie API Key from dash.tessie.com/settings/api is correct.")
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

            # If test succeeds, optionally update controller's vehicle client
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
