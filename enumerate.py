#!/usr/bin/env python3
"""Connect to a BLE device and dump all GATT services and characteristics.

Usage:
    python3 enumerate.py <ADDRESS>

Example:
    python3 enumerate.py "AA:BB:CC:DD:EE:FF"

On macOS the address may be a UUID like "12345678-ABCD-..."
"""

import asyncio
import sys
from bleak import BleakClient


async def enumerate(address: str):
    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        print(f"Connected: {client.is_connected}\n")
        for service in client.services:
            print(f"Service: {service.uuid}  ({service.description})")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid}  [{props}]  ({char.description})")
                # Try to read readable characteristics
                if "read" in char.properties:
                    try:
                        val = await client.read_gatt_char(char.uuid)
                        print(f"         Value (hex): {val.hex()}  ({list(val)})")
                        # Attempt UTF-8 decode
                        try:
                            print(f"         Value (str): {val.decode('utf-8')}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"         Read error: {e}")
                for desc in char.descriptors:
                    print(f"    Desc: {desc.uuid}  ({desc.description})")
            print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 enumerate.py <BLE_ADDRESS_OR_UUID>")
        sys.exit(1)
    asyncio.run(enumerate(sys.argv[1]))
