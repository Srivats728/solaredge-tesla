"""
SolarEdge Modbus TCP Diagnostic Utility
Tests direct LAN communication with your SolarEdge inverter and displays real-time readings.
"""

import sys
import os
import yaml
from solaredge import SolarEdgeClient, STATUS_MAP


def main():
    print("=" * 60)
    print(" SolarEdge Modbus TCP Connection Test")
    print("=" * 60)

    # Load configuration
    host = "192.168.40.169"
    port = 1502
    unit_id = 1

    if os.path.exists("config.yaml"):
        try:
            with open("config.yaml", "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
                se = cfg.get("solaredge", {})
                host = se.get("host", host)
                port = se.get("port", port)
                unit_id = se.get("unit_id", unit_id)
        except Exception:
            pass

    print(f"Target Inverter IP: {host}")
    print(f"Modbus TCP Port   : {port}")
    print(f"Unit / Slave ID   : {unit_id}")
    print("-" * 60)
    print("Attempting connection...")

    client = SolarEdgeClient(host=host, port=port, unit_id=unit_id, timeout=6)
    if not client.connect():
        print("\n[FAILED] Connection FAILED!")
        print("Troubleshooting checklist:")
        print(f" 1. Can you ping {host} from this machine? ('ping {host}')")
        print(f" 2. Is Modbus TCP enabled in your SolarEdge inverter settings?")
        print(f"    (SetApp -> Communication -> RS485 or LAN/Wi-Fi -> Modbus TCP -> Enabled, Port {port})")
        print(" 3. Is the Wi-Fi antenna connected and inverter connected to your Wi-Fi network?")
        print(" 4. SolarEdge only allows ONE Modbus TCP connection at a time. Make sure no other")
        print("    monitoring system (Home Assistant, etc.) is holding the connection open.")
        sys.exit(1)

    print("[OK] Connected to Modbus TCP server successfully!")
    print(f"SunSpec Base Address: {client.base_address}")
    print("\nReading inverter telemetry...")

    data = client.read_generation()
    if not data.get("connected"):
        print("[FAILED] Could not read SunSpec registers. Check Unit ID (typically 1).")
        client.close()
        sys.exit(1)

    power_w = data.get("power_watts", 0.0)
    status_code = data.get("status_code", 0)
    status_desc = data.get("status_desc", "UNKNOWN")

    print("\n" + "=" * 40)
    print(" LIVE SOLAREDGE STATUS")
    print("=" * 40)
    print(f" Inverter Status: {status_desc} (Code: {status_code})")
    print(f" Solar Power    : {power_w:,.1f} W ({power_w / 1000.0:.3f} kW)")
    print("=" * 40)

    if power_w == 0.0 and status_code == 2:
        print("\nNote: Inverter is currently in SLEEPING mode (normal at night or very low light).")
    elif power_w > 0:
        print("\nSolar production detected! Data reading is verified and operational.")

    client.close()


if __name__ == "__main__":
    main()
