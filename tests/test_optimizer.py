"""
Comprehensive Beta Test Suite for SolarEdge - Tesla Solar Optimizer
Internally tests all core components:
1. SunSpec Modbus register decoding, scale factors, and connection error handling.
2. Charging math: Level 1 boundaries (5A-12A), baseline deduction, floor rounding.
3. Debouncing & hysteresis: 120s start delay, 180s stop cloud buffer, amp change cooldown.
4. Vehicle clients: Simulator, Tessie REST mock, Home Assistant REST mock, Factory dispatcher.
5. Mode switching: Auto, Force 12A, Off.
6. Flask REST API: /api/status, /api/mode, /api/config, /api/test-vehicle, /api/test-inverter.
"""

import math
import os
import time
import pytest
from unittest.mock import MagicMock, patch

from solaredge import SolarEdgeClient, STATUS_MAP
from vehicle import (
    BaseVehicleClient,
    SimulatorVehicleClient,
    TeslaControlBLEClient,
    TeslaFleetDirectClient,
    HomeAssistantClient,
    create_vehicle_client
)
from notifier import WhatsAppNotifier
from controller import SolarChargingController
from web.app import create_app


# ==============================================================================
# 1. SUNSPEC MODBUS REGISTER DECODING & ERROR HANDLING
# ==============================================================================
class TestSolarEdgeDecoding:
    """Tests for SunSpec Modbus protocol compliance and register decoding."""

    def test_signed_int16_conversion(self):
        client = SolarEdgeClient(host="127.0.0.1")
        # 0x0000 -> 0
        assert client._to_signed_int16(0x0000) == 0
        # Positive values
        assert client._to_signed_int16(2500) == 2500
        # Negative scale factors: 0xFFFF is -1 in int16
        assert client._to_signed_int16(0xFFFF) == -1
        # -2 scale factor: 0xFFFE
        assert client._to_signed_int16(0xFFFE) == -2
        # -3 scale factor: 0xFFFD
        assert client._to_signed_int16(0xFFFD) == -3

    def test_scale_factor_math(self):
        """Verify real-world SolarEdge scale factors (e.g. 2450 with SF=-1 -> 245.0W, 25 with SF=2 -> 2500W)."""
        client = SolarEdgeClient(host="127.0.0.1")

        # Mock Modbus client and registers
        mock_mb = MagicMock()
        client.client = mock_mb
        client.base_address = 40000

        # Simulate registers: 42 registers returned from address 40068
        # index 15 (offset 83) = Power, index 16 (offset 84) = SF, index 39 (offset 107) = Status
        regs = [0] * 42
        regs[15] = 2500          # 2500
        regs[16] = 0x0000        # Scale factor 0 -> 2500 * 10^0 = 2500W
        regs[39] = 4             # Status: 4 (ON / Producing)

        mock_resp = MagicMock()
        mock_resp.isError.return_value = False
        mock_resp.registers = regs
        mock_mb.read_holding_registers.return_value = mock_resp
        mock_mb.connected = True

        res = client.read_generation()
        assert res["connected"] is True
        assert res["power_watts"] == 2500.0
        assert res["status_code"] == 4
        assert res["status_desc"] == "ON (Producing)"

    def test_negative_scale_factor(self):
        """Test reading with scale factor -1 (0xFFFF)."""
        client = SolarEdgeClient(host="127.0.0.1")
        mock_mb = MagicMock()
        client.client = mock_mb
        client.base_address = 40000

        regs = [0] * 42
        regs[15] = 18550         # Raw power: 18550
        regs[16] = 0xFFFF        # Scale factor: -1 -> 1855.0 W
        regs[39] = 4             # Status: ON

        mock_resp = MagicMock()
        mock_resp.isError.return_value = False
        mock_resp.registers = regs
        mock_mb.read_holding_registers.return_value = mock_resp
        mock_mb.connected = True

        res = client.read_generation()
        assert res["connected"] is True
        assert res["power_watts"] == 1855.0

    def test_inverter_sleeping_night_mode(self):
        """Test inverter in sleeping state (Status 2, Power 0)."""
        client = SolarEdgeClient(host="127.0.0.1")
        mock_mb = MagicMock()
        client.client = mock_mb
        client.base_address = 40000

        regs = [0] * 42
        regs[15] = 0             # 0 Watts
        regs[16] = 0
        regs[39] = 2             # Status: 2 (SLEEPING)

        mock_resp = MagicMock()
        mock_resp.isError.return_value = False
        mock_resp.registers = regs
        mock_mb.read_holding_registers.return_value = mock_resp
        mock_mb.connected = True

        res = client.read_generation()
        assert res["connected"] is True
        assert res["power_watts"] == 0.0
        assert res["status_code"] == 2
        assert "SLEEPING" in res["status_desc"]

    def test_inverter_disconnect_handling(self):
        """Verify client handles unreachable IP cleanly without raising uncaught exceptions."""
        client = SolarEdgeClient(host="192.0.2.1", port=1502, timeout=1)  # Non-routable TEST-NET IP
        res = client.read_generation()
        assert res["connected"] is False
        assert res["power_watts"] == 0.0


# ==============================================================================
# 2. CHARGING CONTROLLER SOLAR MATH & BOUNDARIES
# ==============================================================================
class TestChargingMath:
    """Tests solar surplus calculation and Level 1 (5A-12A) clamping."""

    @pytest.fixture
    def controller(self):
        mock_se = MagicMock()
        mock_veh = SimulatorVehicleClient()
        mock_notif = MagicMock()
        cfg = {
            "voltage": 120,
            "min_amps": 5,
            "max_amps": 12,
            "start_threshold_watts": 1700,
            "baseline_house_watts": 600,
            "stop_threshold_watts": 1100,
            "start_delay_seconds": 120,
            "stop_delay_seconds": 180,
            "amp_change_cooldown_seconds": 45
        }
        return SolarChargingController(mock_se, mock_veh, mock_notif, cfg)

    def test_solar_math_exact_values(self, controller):
        # Baseline = 600W, Voltage = 120V
        # 1800W solar: Available = 1800 - 600 = 1200W -> 1200 / 120 = 10A
        assert controller.calculate_target_amps(1800) == 10

        # 1500W solar: Available = 1500 - 600 = 900W -> 900 / 120 = 7.5 -> floor = 7A
        assert controller.calculate_target_amps(1500) == 7

        # 1200W solar: Available = 1200 - 600 = 600W -> 600 / 120 = 5A
        assert controller.calculate_target_amps(1200) == 5

    def test_max_amps_clamping(self, controller):
        # 3000W solar: Available = 3000 - 600 = 2400W -> 2400 / 120 = 20A -> Clamped to max 12A
        assert controller.calculate_target_amps(3000) == 12
        assert controller.calculate_target_amps(5000) == 12

    def test_min_amps_clamping(self, controller):
        # 800W solar: Available = 800 - 600 = 200W -> 200 / 120 = 1.66A -> Clamped to min 5A
        assert controller.calculate_target_amps(800) == 5
        # 400W solar: Available = 0W -> Clamped to min 5A
        assert controller.calculate_target_amps(400) == 5


# ==============================================================================
# 3. HYSTERESIS, DEBOUNCE, & CLOUD HANDLING
# ==============================================================================
class TestDebounceAndHysteresis:
    """Tests the anti-cycling start/stop timers and cloud stepping."""

    @pytest.fixture
    def setup_ctrl(self):
        mock_se = MagicMock()
        veh = SimulatorVehicleClient()
        notif = MagicMock()
        cfg = {
            "voltage": 120,
            "min_amps": 5,
            "max_amps": 12,
            "start_threshold_watts": 1700,
            "baseline_house_watts": 600,
            "stop_threshold_watts": 1100,
            "start_delay_seconds": 120,
            "stop_delay_seconds": 180,
            "amp_change_cooldown_seconds": 45
        }
        ctrl = SolarChargingController(mock_se, veh, notif, cfg)
        return ctrl, mock_se, veh, notif

    def test_start_delay_timer(self, setup_ctrl):
        ctrl, mock_se, veh, notif = setup_ctrl

        # Inverter produces 2000W (> 1700W start threshold)
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 2000.0, "status_code": 4, "status_desc": "ON"
        }

        # Step 1: Initial high solar reading starts timer
        ctrl.evaluate_step()
        assert ctrl.high_solar_since is not None
        assert ctrl.is_charging is False
        assert veh.is_charging is False

        # Step 2: 60 seconds later, still under 120s delay -> Should not start yet
        with patch("time.time", return_value=ctrl.high_solar_since + 60):
            ctrl.evaluate_step()
            assert ctrl.is_charging is False
            assert veh.is_charging is False

        # Step 3: 125 seconds later (> 120s delay) -> Vehicle charging should start!
        with patch("time.time", return_value=ctrl.high_solar_since + 125):
            ctrl.evaluate_step()
            assert ctrl.is_charging is True
            assert veh.is_charging is True
            # (2000 - 600) / 120 = 11.66 -> 11 A
            assert ctrl.current_set_amps == 11
            assert veh.charge_amps == 11

    def test_start_timer_aborted_by_cloud(self, setup_ctrl):
        ctrl, mock_se, veh, notif = setup_ctrl

        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 2000.0, "status_code": 4, "status_desc": "ON"
        }
        ctrl.evaluate_step()
        assert ctrl.high_solar_since is not None

        # Solar drops below 1700W before 120s timer expires
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 1400.0, "status_code": 4, "status_desc": "ON"
        }
        ctrl.evaluate_step()
        # Timer should be reset to None
        assert ctrl.high_solar_since is None
        assert ctrl.is_charging is False

    def test_stop_delay_and_amp_stepdown(self, setup_ctrl):
        ctrl, mock_se, veh, notif = setup_ctrl

        # Put into actively charging state at 10A
        ctrl.is_charging = True
        veh.is_charging = True
        ctrl.current_set_amps = 10
        veh.charge_amps = 10

        # Solar drops to 800W (< 1100W stop threshold)
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 800.0, "status_code": 4, "status_desc": "ON"
        }

        # Step 1: Solar drops -> Immediately steps down to min 5A while starting 180s stop timer
        ctrl.evaluate_step()
        assert ctrl.low_solar_since is not None
        assert ctrl.is_charging is True       # Still charging (cloud buffer)
        assert ctrl.current_set_amps == 5     # Stepped down to 5A to minimize grid draw
        assert veh.charge_amps == 5

        # Step 2: 90 seconds in (< 180s) -> Still charging at 5A
        with patch("time.time", return_value=ctrl.low_solar_since + 90):
            ctrl.evaluate_step()
            assert ctrl.is_charging is True

        # Step 3: 185 seconds in (> 180s) -> Charging safely stopped
        with patch("time.time", return_value=ctrl.low_solar_since + 185):
            ctrl.evaluate_step()
            assert ctrl.is_charging is False
            assert veh.is_charging is False
            assert ctrl.low_solar_since is None

    def test_stop_timer_cancelled_by_solar_recovery(self, setup_ctrl):
        ctrl, mock_se, veh, notif = setup_ctrl

        ctrl.is_charging = True
        veh.is_charging = True
        ctrl.current_set_amps = 10
        veh.charge_amps = 10

        # Solar drops below 1100W
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 900.0, "status_code": 4, "status_desc": "ON"
        }
        ctrl.evaluate_step()
        assert ctrl.low_solar_since is not None

        # Solar recovers to 2200W before stop timer expires
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 2200.0, "status_code": 4, "status_desc": "ON"
        }
        ctrl.evaluate_step()
        # Timer cancelled, still charging
        assert ctrl.low_solar_since is None
        assert ctrl.is_charging is True


# ==============================================================================
# 4. VEHICLE PROVIDER IMPLEMENTATIONS
# ==============================================================================
class TestVehicleProviders:
    """Tests all vehicle integration clients and factory."""

    def test_simulator_client(self):
        sim = SimulatorVehicleClient()
        assert sim.initialize() is True
        assert sim.is_awake() is True
        info = sim.get_info()
        assert info["provider"] == "simulator"

        state = sim.get_charge_state()
        assert state["online"] is True
        assert state["charging_state"] == "Stopped"

        # Test control commands
        assert sim.set_charging_amps(12) is True
        assert sim.charge_amps == 12

        assert sim.start_charging() is True
        assert sim.is_charging is True
        assert sim.get_charge_state()["charging_state"] == "Charging"

        assert sim.stop_charging() is True
        assert sim.is_charging is False
        assert sim.get_charge_state()["charging_state"] == "Stopped"

    def test_ble_client_with_mocks(self, tmp_path):
        key_file = tmp_path / "private-key.pem"
        key_file.write_text("dummy_key")
        client = TeslaControlBLEClient({
            "vin": "5YJ3E1EB8NF123456",
            "key_file": str(key_file),
            "binary_path": "tesla-control"
        })
        assert client.initialize() is True
        assert client.is_awake() is True

        mock_completed = MagicMock()
        mock_completed.returncode = 0
        mock_completed.stdout = "Success"
        mock_completed.stderr = ""

        with patch("subprocess.run", return_value=mock_completed) as mock_sub:
            assert client.set_charging_amps(9) is True
            assert client.charge_amps == 9
            assert client.start_charging() is True
            assert client.is_charging is True
            assert client.stop_charging() is True
            assert client.is_charging is False

    def test_fleet_direct_client_with_mocks(self):
        client = TeslaFleetDirectClient({
            "client_id": "test_id",
            "client_secret": "test_sec",
            "access_token": "valid_token",
            "refresh_token": "valid_refresh",
            "vin": "5YJ3E1EB8NF123456",
            "region": "na"
        })

        def mock_fleet_get(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            if "/vehicles/5YJ3E1EB8NF123456/vehicle_data" in url:
                resp.json.return_value = {
                    "response": {
                        "charge_state": {
                            "charging_state": "Charging",
                            "battery_level": 80,
                            "charge_amps": 12,
                            "charge_current_request": 12,
                            "charger_actual_current": 12,
                            "charger_voltage": 120,
                            "charge_port_door_open": True
                        }
                    }
                }
            elif "/vehicles/5YJ3E1EB8NF123456" in url:
                resp.json.return_value = {"response": {"state": "online"}}
            elif "/vehicles" in url:
                resp.json.return_value = {
                    "response": [
                        {"vin": "5YJ3E1EB8NF123456", "display_name": "My Model 3"}
                    ]
                }
            return resp

        with patch("requests.get", side_effect=mock_fleet_get), patch("requests.post") as mock_post:
            assert client.initialize() is True
            assert client.vehicle_name == "My Model 3"
            assert client.is_awake() is True

            state = client.get_charge_state()
            assert state["online"] is True
            assert state["battery_level"] == 80
            assert state["charging_state"] == "Charging"
            assert state["charge_amps"] == 12

            # Mock REST command success
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {"response": {"result": True}}
            assert client.set_charging_amps(8) is True
            assert client.start_charging() is True
            assert client.stop_charging() is True

    def test_home_assistant_client_with_mocks(self):
        client = HomeAssistantClient({
            "url": "http://192.168.40.50:8123",
            "token": "ha_test_token",
            "soc_entity": "sensor.battery_sensor",
            "amps_entity": "number.charging_amps",
            "switch_entity": "switch.charger"
        })

        with patch("requests.get") as mock_get, patch("requests.post") as mock_post:
            # Mock HA ping /api/
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"message": "API running."}
            assert client.initialize() is True

            # Mock state reading
            def mock_get_state(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                if "sensor.battery_sensor" in url:
                    resp.json.return_value = {"state": "82"}
                elif "number.charging_amps" in url:
                    resp.json.return_value = {"state": "11"}
                elif "switch.charger" in url:
                    resp.json.return_value = {"state": "on"}
                return resp

            mock_get.side_effect = mock_get_state
            state = client.get_charge_state()
            assert state["battery_level"] == 82
            assert state["charging_state"] == "Charging"
            assert state["charge_amps"] == 11

            # Mock commands
            mock_post.return_value.status_code = 200
            assert client.set_charging_amps(12) is True
            assert client.start_charging() is True
            assert client.stop_charging() is True

    def test_factory_dispatcher(self):
        sim = create_vehicle_client({"provider": "simulator"})
        assert isinstance(sim, SimulatorVehicleClient)

        fleet = create_vehicle_client({"provider": "fleet", "fleet": {"client_id": "c"}})
        assert isinstance(fleet, TeslaFleetDirectClient)

        ble = create_vehicle_client({"provider": "ble", "ble": {"vin": "5YJ..."}})
        assert isinstance(ble, TeslaControlBLEClient)

        ha = create_vehicle_client({"provider": "homeassistant", "homeassistant": {"token": "t"}})
        assert isinstance(ha, HomeAssistantClient)

        fallback = create_vehicle_client({})
        assert isinstance(fallback, TeslaFleetDirectClient) or isinstance(fallback, SimulatorVehicleClient)


# ==============================================================================
# 5. MODE SWITCHING (AUTO, FORCE, OFF)
# ==============================================================================
class TestOperatingModes:
    def test_mode_force_and_off(self):
        mock_se = MagicMock()
        veh = SimulatorVehicleClient()
        notif = MagicMock()
        cfg = {"min_amps": 5, "max_amps": 12, "voltage": 120}
        ctrl = SolarChargingController(mock_se, veh, notif, cfg)

        # Force mode -> sets max amps (12A) and starts charge immediately
        ctrl.set_mode("force")
        assert ctrl.mode == "force"
        assert ctrl.is_charging is True
        assert veh.is_charging is True
        assert veh.charge_amps == 12

        # Off mode -> stops charging immediately
        ctrl.set_mode("off")
        assert ctrl.mode == "off"
        assert ctrl.is_charging is False
        assert veh.is_charging is False


# ==============================================================================
# 6. FLASK REST API ENDPOINTS
# ==============================================================================
class TestFlaskAPI:
    """Tests the web server routes, telemetry, and configuration management."""

    @pytest.fixture
    def test_app(self, tmp_path):
        cfg_file = tmp_path / "test_config.yaml"
        cfg_content = """
solaredge:
  host: "192.168.40.169"
  port: 1502
vehicle:
  provider: "simulator"
charging:
  voltage: 120
  min_amps: 5
  max_amps: 12
  start_threshold_watts: 1700
  baseline_house_watts: 600
  stop_threshold_watts: 1100
whatsapp:
  enabled: false
"""
        cfg_file.write_text(cfg_content)

        mock_se = MagicMock()
        mock_se.host = "192.168.40.169"
        mock_se.port = 1502
        veh = SimulatorVehicleClient()
        notif = MagicMock()
        ctrl = SolarChargingController(mock_se, veh, notif, {
            "voltage": 120, "min_amps": 5, "max_amps": 12,
            "start_threshold_watts": 1700, "baseline_house_watts": 600, "stop_threshold_watts": 1100
        })

        app = create_app(controller_ref=ctrl, config_path=str(cfg_file))
        app.config["TESTING"] = True
        return app.test_client(), ctrl, cfg_file

    def test_get_index(self, test_app):
        client, ctrl, cfg_file = test_app
        res = client.get("/")
        assert res.status_code == 200
        html = res.data.decode("utf-8")
        assert "SOLAREDGE // TESLA" in html
        assert "AUTO (SOLAR)" in html
        assert "FORCE 12A" in html

    def test_get_status(self, test_app):
        client, ctrl, cfg_file = test_app
        # Populate simulated data in controller
        ctrl.last_solar_data = {"connected": True, "power_watts": 2400.0, "status_desc": "ON", "status_code": 4}
        ctrl.last_tesla_data = {
            "online": True, "charging_state": "Charging", "battery_level": 75,
            "charge_amps": 12, "charger_voltage": 120, "plugged_in": True
        }
        ctrl.is_charging = True
        ctrl.current_set_amps = 12

        res = client.get("/api/status")
        assert res.status_code == 200
        data = res.get_json()

        assert data["solaredge"]["power_watts"] == 2400.0
        assert data["solaredge"]["power_kw"] == 2.4
        assert data["controller"]["surplus_watts"] == 1800.0
        assert data["vehicle"]["battery_level"] == 75
        assert data["vehicle"]["charging_state"] == "Charging"
        assert data["controller"]["current_set_amps"] == 12

    def test_post_mode(self, test_app):
        client, ctrl, cfg_file = test_app
        # Switch to force
        res = client.post("/api/mode", json={"mode": "force"})
        assert res.status_code == 200
        assert ctrl.mode == "force"

        # Switch to off
        res = client.post("/api/mode", json={"mode": "off"})
        assert res.status_code == 200
        assert ctrl.mode == "off"

        # Invalid mode
        res = client.post("/api/mode", json={"mode": "invalid_mode"})
        assert res.status_code == 400

    def test_get_and_post_config(self, test_app):
        client, ctrl, cfg_file = test_app
        # GET config
        res = client.get("/api/config")
        assert res.status_code == 200
        data = res.get_json()
        assert data["solaredge"]["host"] == "192.168.40.169"

        # POST updated config
        new_cfg = {
            "solaredge": {"host": "192.168.40.180"},
            "charging": {"start_threshold_watts": 1850}
        }
        post_res = client.post("/api/config", json=new_cfg)
        assert post_res.status_code == 200

        # Verify applied to controller
        assert ctrl.solaredge.host == "192.168.40.180"
        assert ctrl.start_threshold_w == 1850

    def test_test_vehicle_endpoint(self, test_app):
        client, ctrl, cfg_file = test_app
        res = client.post("/api/test-vehicle", json={"provider": "simulator"})
        assert res.status_code == 200
        data = res.get_json()
        assert data["success"] is True
        assert data["online"] is True
        assert "Model 3" in data["name"]


# ==============================================================================
# 7. FULL END-TO-END SCENARIO SIMULATION
# ==============================================================================
class TestEndToEndSolarLifecycle:
    """Simulates a complete real-world solar charging day lifecycle."""

    def test_full_lifecycle(self):
        mock_se = MagicMock()
        veh = SimulatorVehicleClient()
        notif = MagicMock()
        cfg = {
            "voltage": 120,
            "min_amps": 5,
            "max_amps": 12,
            "start_threshold_watts": 1700,
            "baseline_house_watts": 600,
            "stop_threshold_watts": 1100,
            "start_delay_seconds": 120,
            "stop_delay_seconds": 180,
            "amp_change_cooldown_seconds": 45
        }
        ctrl = SolarChargingController(mock_se, veh, notif, cfg)

        # 1. Early morning: Inverter asleep (0W)
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 0.0, "status_code": 2, "status_desc": "SLEEPING (Night)"
        }
        ctrl.evaluate_step()
        assert ctrl.is_charging is False
        assert veh.is_charging is False

        # 2. Mid morning: Solar rising, 1400W (below 1700W threshold)
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 1400.0, "status_code": 4, "status_desc": "ON (Producing)"
        }
        ctrl.evaluate_step()
        assert ctrl.is_charging is False
        assert ctrl.high_solar_since is None

        # 3. Peak sunshine: 2600W (> 1700W threshold)
        base_time = 1000000.0
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 2600.0, "status_code": 4, "status_desc": "ON (Producing)"
        }

        with patch("time.time", return_value=base_time):
            ctrl.evaluate_step()
            assert ctrl.high_solar_since == base_time
            assert ctrl.is_charging is False

        # 4. Sustained sunshine: 130 seconds later -> Charging must start!
        with patch("time.time", return_value=base_time + 130):
            ctrl.evaluate_step()
            assert ctrl.is_charging is True
            assert veh.is_charging is True
            # (2600 - 600) / 120 = 16.66A -> Clamped to max 12A
            assert ctrl.current_set_amps == 12
            assert veh.charge_amps == 12

        # 5. Partial shade: Solar drops to 1600W (above 1100W stop threshold)
        # Surplus = 1600 - 600 = 1000W -> 1000 / 120 = 8.33 -> 8A
        # Wait beyond amp cooldown (50s after start)
        with patch("time.time", return_value=base_time + 185):
            mock_se.read_generation.return_value = {
                "connected": True, "power_watts": 1600.0, "status_code": 4, "status_desc": "ON (Producing)"
            }
            ctrl.evaluate_step()
            assert ctrl.is_charging is True
            assert ctrl.current_set_amps == 8
            assert veh.charge_amps == 8

        # 6. Heavy cloud: Solar drops to 700W (< 1100W stop threshold)
        cloud_time = base_time + 300
        mock_se.read_generation.return_value = {
            "connected": True, "power_watts": 700.0, "status_code": 4, "status_desc": "ON (Producing)"
        }
        with patch("time.time", return_value=cloud_time):
            ctrl.evaluate_step()
            assert ctrl.is_charging is True       # Cloud buffer active
            assert ctrl.current_set_amps == 5     # Dropped immediately to 5A to avoid grid drain

        # 7. Cloud persists for 200 seconds (> 180s stop delay) -> Charging safely stops
        with patch("time.time", return_value=cloud_time + 200):
            ctrl.evaluate_step()
            assert ctrl.is_charging is False
            assert veh.is_charging is False

