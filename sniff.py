#!/usr/bin/env python3
"""Subscribe to notifications on the Volta CAN-BLE Gateway and log all frames.

The VPS-1A50 is a CAN BLE Gateway (Embedded One, LLC) that tunnels CAN bus
frames over BLE. This script subscribes to the notify characteristic and
prints every raw frame received, then attempts to decode SOC.

Usage:
    python3 sniff.py [ADDRESS]
"""

import asyncio
import sys
import struct
import datetime

DEVICE_ADDR = "B532E5DA-6704-3640-9142-415175D91C45"

# Embedded One CAN BLE Gateway service/characteristics
GW_SERVICE   = "00035b03-58e6-07dd-021a-08123a000300"
GW_DATA_CHAR = "00035b03-58e6-07dd-021a-08123a000301"  # notify+write
GW_CTRL_CHAR = "00035b03-58e6-07dd-021a-08123a0003ff"  # write

from bleak import BleakClient


def decode_frame(data: bytes):
    """Try to interpret bytes as a CAN frame tunneled over BLE.

    Embedded One gateway typically uses one of these layouts:
      [4-byte CAN ID LE][1-byte DLC][up to 8 bytes data]   (13 bytes max)
      [2-byte CAN ID LE][up to 8 bytes data]                (10 bytes max)
    """
    hex_str = data.hex()
    lines = [f"  raw ({len(data)}B): {' '.join(f'{b:02x}' for b in data)}"]

    if len(data) >= 5:
        # Try 4-byte CAN ID + DLC + data
        can_id_le = struct.unpack_from("<I", data, 0)[0]
        can_id_be = struct.unpack_from(">I", data, 0)[0]
        dlc = data[4]
        payload = data[5:5+dlc] if dlc <= 8 and 5+dlc <= len(data) else data[5:]
        lines.append(f"  CAN ID (LE): 0x{can_id_le:08X}  DLC={dlc}  payload: {payload.hex()}")
        lines.append(f"  CAN ID (BE): 0x{can_id_be:08X}")

    if len(data) >= 2:
        # Try 2-byte CAN ID + data
        can_id2 = struct.unpack_from("<H", data, 0)[0]
        payload2 = data[2:]
        lines.append(f"  CAN ID (2B LE): 0x{can_id2:04X}  payload: {payload2.hex()}")

    # Look for SOC-like values (0-100) in each byte
    candidates = [f"byte[{i}]={b}" for i, b in enumerate(data) if 1 <= b <= 100]
    if candidates:
        lines.append(f"  SOC candidates (1-100): {', '.join(candidates)}")

    # Look for voltage-like values (big/little endian uint16, 200-600 = 20.0-60.0 V)
    for i in range(len(data) - 1):
        for order in ("<H", ">H"):
            v = struct.unpack_from(order, data, i)[0]
            if 200 <= v <= 600:
                lines.append(f"  Voltage? {v/10:.1f}V at byte[{i}] ({order})")
            if 2000 <= v <= 6000:
                lines.append(f"  Voltage? {v/100:.2f}V at byte[{i}] ({order})")

    return "\n".join(lines)


frame_count = 0

def notification_handler(_, data: bytearray):
    global frame_count
    frame_count += 1
    ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"\n[{ts}] Frame #{frame_count}")
    print(decode_frame(bytes(data)))


async def main(address: str):
    print(f"Connecting to CAN BLE Gateway at {address} ...")
    async with BleakClient(address) as client:
        print("Connected. Subscribing to notifications...\n")
        await client.start_notify(GW_DATA_CHAR, notification_handler)

        # Try sending some common CAN gateway wake/request commands
        # Embedded One gateway may need a "start streaming" command on the ctrl char
        probe_commands = [
            bytes([0x01]),                          # simple enable
            bytes([0x02]),                          # alternate enable
            bytes([0x00, 0x00, 0x00, 0x00]),        # null frame
            bytes([0xFF]),                           # broadcast
        ]
        for cmd in probe_commands:
            try:
                await client.write_gatt_char(GW_CTRL_CHAR, cmd, response=False)
                print(f"Sent probe to ctrl char: {cmd.hex()}")
            except Exception as e:
                print(f"Ctrl char write failed ({cmd.hex()}): {e}")
                break
            await asyncio.sleep(0.5)

        print("\nListening for 30s — watching for CAN frames (operate the RV system if nothing arrives)...\n")
        await asyncio.sleep(30)

        await client.stop_notify(GW_DATA_CHAR)
        print(f"\nDone. Received {frame_count} frames total.")


if __name__ == "__main__":
    addr = sys.argv[1] if len(sys.argv) > 1 else DEVICE_ADDR
    asyncio.run(main(addr))
