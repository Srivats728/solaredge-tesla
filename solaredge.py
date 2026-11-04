"""
SolarEdge Modbus TCP Client (SunSpec Alliance compliant)
Connects directly to the SolarEdge inverter over LAN and reads live power generation.
"""

import logging
import struct
import time
from typing import Optional, Dict, Any, Tuple
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ModbusException

logger = logging.getLogger(__name__)

STATUS_MAP = {
    1: "OFF",
    2: "SLEEPING (Night)",
    3: "STARTING (Grid Monitoring)",
    4: "ON (Producing)",
    5: "THROTTLED",
    6: "SHUTTING_DOWN",
    7: "FAULT",
    8: "MAINTENANCE",
}


class SolarEdgeClient:
    """Reads real-time power production from SolarEdge inverter using SunSpec Modbus TCP."""

    def __init__(self, host: str, port: int = 1502, unit_id: int = 1, timeout: int = 5):
        self.host = host
        self.port = port
        self.unit_id = unit_id
        self.timeout = timeout
        self.client: Optional[ModbusTcpClient] = None
        self.base_address: Optional[int] = None
        self._last_successful_read = 0.0

    def connect(self) -> bool:
        """Connect to the Modbus TCP server on the inverter."""
        try:
            if self.client and self.client.connected:
                return True

            logger.info("Connecting to SolarEdge inverter at %s:%s (unit=%s)...", self.host, self.port, self.unit_id)
            self.client = ModbusTcpClient(
                host=self.host,
                port=self.port,
                timeout=self.timeout
            )
            connected = self.client.connect()
            if connected:
                logger.info("Connected to SolarEdge inverter successfully.")
                # Locate SunSpec base address if not already known
                if self.base_address is None:
                    self.base_address = self._discover_sunspec_base()
                return True
            else:
                logger.warning("Failed to connect to SolarEdge inverter at %s:%s", self.host, self.port)
                return False
        except Exception as e:
            logger.error("Exception while connecting to SolarEdge inverter: %s", e)
            return False

    def close(self):
        """Close Modbus connection."""
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None

    def _discover_sunspec_base(self) -> int:
        """
        Auto-detect whether the SunSpec identifier 'SunS' (0x53756E53)
        starts at register 40000 or 40001 (or 50000 depending on firmware/offset).
        """
        candidate_bases = [40000, 40001, 0, 50000]
        for base in candidate_bases:
            try:
                # Read 2 registers for 'SunS' magic number
                res = self.client.read_holding_registers(address=base, count=2, slave=self.unit_id)
                if not res.isError() and len(res.registers) == 2:
                    val = (res.registers[0] << 16) | res.registers[1]
                    if val == 0x53756E53:
                        logger.info("Discovered SunSpec base register at address %d", base)
                        return base
            except Exception:
                continue

        # Default fallback to 40000
        logger.info("SunSpec discovery did not find 'SunS' magic number; defaulting base address to 40000.")
        return 40000

    def _to_signed_int16(self, n: int) -> int:
        """Convert unsigned 16-bit register value to signed int16."""
        return struct.unpack('h', struct.pack('H', n & 0xFFFF))[0]

    def read_generation(self) -> Dict[str, Any]:
        """
        Read real-time AC power output (in Watts) and status from the inverter.

        SunSpec Model 101/102/103 (Single/Split/Three phase inverter):
        - Offset from base + 68: Model ID (101, 102, 103)
        - Offset from base + 83: I_AC_Power (int16)
        - Offset from base + 84: I_AC_Power_SF (int16 scale factor)
        - Offset from base + 107: I_Status (uint16)
        """
        result = {
            "connected": False,
            "power_watts": 0.0,
            "status_code": 0,
            "status_desc": "UNKNOWN",
            "timestamp": time.time()
        }

        if not self.connect():
            return result

        try:
            base = self.base_address if self.base_address is not None else 40000

            # Read registers from offset 68 to 108 (approx 42 registers)
            # Register 40068 to 40108 contains Inverter model header, AC Power, SF, and Status.
            start_addr = base + 68
            count = 42

            response = self.client.read_holding_registers(address=start_addr, count=count, slave=self.unit_id)

            if response.isError():
                logger.warning("Modbus error reading registers at %d: %s", start_addr, response)
                self.close()
                return result

            regs = response.registers
            if len(regs) < 40:
                logger.warning("Incomplete register response received: %d registers", len(regs))
                return result

            # Offset 83 relative to base is index 15 from start_addr (83 - 68 = 15)
            # Offset 84 relative to base is index 16 from start_addr (84 - 68 = 16)
            # Offset 107 relative to base is index 39 from start_addr (107 - 68 = 39)
            raw_power = self._to_signed_int16(regs[15])
            raw_sf = self._to_signed_int16(regs[16])
            status_code = regs[39]

            # In SunSpec, 0xFFFF (-32768) indicates not implemented / invalid
            if raw_power == -32768:
                power_watts = 0.0
            else:
                scale_multiplier = 10 ** raw_sf
                power_watts = float(raw_power * scale_multiplier)
                # Generation cannot be negative for car charging calculation
                if power_watts < 0:
                    power_watts = 0.0

            result["connected"] = True
            result["power_watts"] = round(power_watts, 1)
            result["status_code"] = status_code
            result["status_desc"] = STATUS_MAP.get(status_code, f"CODE_{status_code}")
            self._last_successful_read = time.time()

            logger.debug("SolarEdge read: %.1f W | Status: %s (%d)",
                         result["power_watts"], result["status_desc"], status_code)
            return result

        except ModbusException as me:
            logger.error("Modbus protocol error: %s", me)
            self.close()
            return result
        except Exception as ex:
            logger.error("Unexpected error reading SolarEdge data: %s", ex)
            self.close()
            return result
