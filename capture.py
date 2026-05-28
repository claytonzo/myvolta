#!/usr/bin/env python3
"""Capture raw BLE notification bytes to a file for offline analysis.

Collects all bytes for 10 seconds, saves as binary + annotated hex dump.
Run this, then run analyze.py on the output.
"""

import asyncio
import sys
import datetime

DEVICE_ADDR = "B532E5DA-6704-3640-9142-415175D91C45"
GW_DATA_CHAR = "00035b03-58e6-07dd-021a-08123a000301"
GW_CTRL_CHAR = "00035b03-58e6-07dd-021a-08123a0003ff"

from bleak import BleakClient

all_bytes = bytearray()
packets = []  # list of (timestamp, bytes) tuples


def notification_handler(_, raw: bytearray):
    ts = datetime.datetime.now()
    b = bytes(raw)
    all_bytes.extend(b)
    packets.append((ts, b))
    print(f"  [{ts.strftime('%H:%M:%S.%f')[:-3]}] +{len(b)}B: {b.hex()}")


async def main(address: str):
    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        print("Connected. Capturing for 10 seconds...\n")
        await client.start_notify(GW_DATA_CHAR, notification_handler)

        await asyncio.sleep(10)

        # Save raw binary inside the connection context (before stop_notify may throw)
        with open("capture.bin", "wb") as f:
            f.write(all_bytes)
        with open("capture.txt", "w") as f:
            f.write(f"Total bytes: {len(all_bytes)}\n")
            f.write(f"Packets: {len(packets)}\n\n")
            f.write("--- Per-packet log ---\n")
            for ts, b in packets:
                f.write(f"[{ts.strftime('%H:%M:%S.%f')[:-3]}] {len(b):2d}B  {b.hex()}  {list(b)}\n")
            f.write("\n--- Full byte stream (hex) ---\n")
            stream = all_bytes
            for i in range(0, len(stream), 16):
                chunk = stream[i:i+16]
                hex_part = " ".join(f"{b:02x}" for b in chunk)
                asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                f.write(f"{i:04x}  {hex_part:<48}  {asc_part}\n")
        print(f"\nSaved {len(all_bytes)} bytes → capture.bin, capture.txt")
        print(f"Captured {len(packets)} BLE notifications")

        try:
            await client.stop_notify(GW_DATA_CHAR)
        except Exception:
            pass  # characteristic doesn't allow writing CCCD to disable

    # Save raw binary (fallback outside context)
    with open("capture.bin", "wb") as f:
        f.write(all_bytes)

    # Save annotated hex
    with open("capture.txt", "w") as f:
        f.write(f"Total bytes: {len(all_bytes)}\n")
        f.write(f"Packets: {len(packets)}\n\n")
        f.write("--- Per-packet log ---\n")
        for ts, b in packets:
            f.write(f"[{ts.strftime('%H:%M:%S.%f')[:-3]}] {len(b):2d}B  {b.hex()}  {list(b)}\n")
        f.write("\n--- Full byte stream (hex) ---\n")
        # 16 bytes per line
        stream = all_bytes
        for i in range(0, len(stream), 16):
            chunk = stream[i:i+16]
            hex_part = " ".join(f"{b:02x}" for b in chunk)
            asc_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            f.write(f"{i:04x}  {hex_part:<48}  {asc_part}\n")

    print(f"\nSaved {len(all_bytes)} bytes to capture.bin and capture.txt")
    print(f"Captured {len(packets)} BLE notifications")


if __name__ == "__main__":
    addr = sys.argv[1] if len(sys.argv) > 1 else DEVICE_ADDR
    asyncio.run(main(addr))
