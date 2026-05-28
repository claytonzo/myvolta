#!/usr/bin/env python3
"""Scan for BLE devices — finds the Volta BMS advertised name and address."""

import asyncio
from bleak import BleakScanner


async def scan(duration: float = 10.0):
    print(f"Scanning for BLE devices for {duration}s... (make sure Volta is powered on)\n")

    devices = await BleakScanner.discover(timeout=duration, return_adv=True)

    print(f"{'Name':<40} {'Address':<40} {'RSSI':>5}  Services/UUIDs")
    print("-" * 110)
    for addr, (device, adv) in devices.items():
        name = device.name or "(no name)"
        uuids = ", ".join(adv.service_uuids) if adv.service_uuids else ""
        print(f"{name:<40} {addr:<40} {adv.rssi:>5}  {uuids}")

    # Highlight anything that looks like Volta
    print("\n--- Possible Volta devices ---")
    found = False
    for addr, (device, adv) in devices.items():
        name = (device.name or "").lower()
        if any(k in name for k in ("volta", "bms", "battery", "rv", "winnebago", "travato")):
            print(f"  >> {device.name}  {addr}  RSSI={adv.rssi}")
            found = True
    if not found:
        print("  None matched keywords — check the full list above and look for unfamiliar names.")


if __name__ == "__main__":
    asyncio.run(scan())
