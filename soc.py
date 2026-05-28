#!/usr/bin/env python3
"""Read SOC (State of Charge) from the Volta BMS over BLE.

Usage:
    python3 soc.py <ADDRESS> [CHAR_UUID]

Run enumerate.py first to find the right characteristic UUID.
This script tries common BMS characteristic UUIDs automatically,
then falls back to the one you specify.

Common patterns found in BLE BMSes:
  - Standard Battery Service:  0x2A19  (Battery Level, 1 byte, 0-100%)
  - JBD/Overkill BMS:          custom 0xFF02 notify / 0xFF01 write
  - REC BMS, Victron, etc.:    vendor-specific
"""

import asyncio
import sys
import struct
from bleak import BleakClient


# Standard Bluetooth SIG Battery Level characteristic
BATTERY_LEVEL_UUID = "00002a19-0000-1000-8000-00805f9b34fb"

# Common vendor BMS UUIDs (JBD-style, used by many Chinese BMS units)
JBD_SERVICE       = "0000ff00-0000-1000-8000-00805f9b34fb"
JBD_NOTIFY_CHAR   = "0000ff01-0000-1000-8000-00805f9b34fb"
JBD_WRITE_CHAR    = "0000ff02-0000-1000-8000-00805f9b34fb"

# Request packet for JBD basic info (returns voltage, current, SOC, etc.)
JBD_READ_ALL      = bytes([0xDD, 0xA5, 0x03, 0x00, 0xFF, 0xFD, 0x77])


def parse_jbd_basic(data: bytes):
    """Parse JBD BMS 'basic info' response (0x03 register)."""
    if len(data) < 23 or data[0] != 0xDD or data[1] != 0x03:
        return None
    total_voltage  = struct.unpack_from(">H", data, 4)[0] / 100.0   # V
    current        = struct.unpack_from(">h", data, 6)[0] / 100.0   # A (signed)
    remain_cap     = struct.unpack_from(">H", data, 8)[0] / 100.0   # Ah
    nominal_cap    = struct.unpack_from(">H", data, 10)[0] / 100.0  # Ah
    soc            = data[23]                                         # %
    return {
        "voltage_V":    total_voltage,
        "current_A":    current,
        "remaining_Ah": remain_cap,
        "capacity_Ah":  nominal_cap,
        "soc_%":        soc,
    }


async def read_standard_battery(client: BleakClient):
    """Try the Bluetooth SIG standard Battery Level characteristic."""
    try:
        val = await client.read_gatt_char(BATTERY_LEVEL_UUID)
        return {"soc_%": val[0], "source": "standard BT Battery Service"}
    except Exception:
        return None


async def read_jbd(client: BleakClient):
    """Try JBD-style BMS protocol (notify/write pair)."""
    result = {}
    event = asyncio.Event()

    def handler(_, data: bytearray):
        parsed = parse_jbd_basic(bytes(data))
        if parsed:
            result.update(parsed)
            result["source"] = "JBD BMS protocol"
        event.set()

    try:
        await client.start_notify(JBD_NOTIFY_CHAR, handler)
        await client.write_gatt_char(JBD_WRITE_CHAR, JBD_READ_ALL, response=False)
        await asyncio.wait_for(event.wait(), timeout=5.0)
        await client.stop_notify(JBD_NOTIFY_CHAR)
        return result if result else None
    except Exception as e:
        return None


async def read_custom(client: BleakClient, char_uuid: str):
    """Read a specific characteristic and print raw bytes for manual decoding."""
    try:
        val = await client.read_gatt_char(char_uuid)
        print(f"Raw bytes: {val.hex()}")
        print(f"As uint8 list: {list(val)}")
        if len(val) >= 2:
            print(f"As little-endian uint16: {struct.unpack_from('<H', val)[0]}")
            print(f"As big-endian uint16:    {struct.unpack_from('>H', val)[0]}")
        return {"raw_hex": val.hex(), "source": "custom char"}
    except Exception as e:
        print(f"Error reading {char_uuid}: {e}")
        return None


async def main(address: str, char_uuid: str | None):
    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        print(f"Connected.\n")

        # 1. Try standard Battery Level characteristic
        result = await read_standard_battery(client)
        if result:
            print(f"[Standard BT] SOC: {result['soc_%']}%")
            return

        # 2. Try JBD BMS protocol
        result = await read_jbd(client)
        if result:
            print(f"[JBD BMS] SOC: {result['soc_%']}%")
            print(f"  Voltage:   {result.get('voltage_V')} V")
            print(f"  Current:   {result.get('current_A')} A")
            print(f"  Remaining: {result.get('remaining_Ah')} Ah / {result.get('capacity_Ah')} Ah")
            return

        # 3. User-specified characteristic
        if char_uuid:
            print(f"Trying specified characteristic {char_uuid}:")
            await read_custom(client, char_uuid)
            return

        print("Could not auto-detect SOC. Run enumerate.py to inspect all characteristics,")
        print("then re-run: python3 soc.py <ADDRESS> <CHAR_UUID>")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 soc.py <BLE_ADDRESS_OR_UUID> [CHAR_UUID]")
        sys.exit(1)
    address = sys.argv[1]
    char_uuid = sys.argv[2] if len(sys.argv) > 2 else None
    asyncio.run(main(address, char_uuid))
