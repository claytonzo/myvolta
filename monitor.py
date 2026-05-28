#!/usr/bin/env python3
"""Live Volta battery monitor — prints SOC and key stats every update cycle.

Usage:
    python3 monitor.py

Protocol: Embedded One CAN BLE Gateway (VPS-1A50)
  Frame: 55 55 [type=01] [msgid] [CAN_ID: 4B LE] [DLC=08] [data: 8B] [chk] 04 04

Message IDs:
  0x00  pack measurements (voltage, current)
  0x01  pack info (module count, SOC%)
  0x11-0x14  cell voltages bank 1 (4 cells each, LE uint16 mV)
  0x24-0x26  temperatures (4 each, LE uint16 in 0.1 °C)
"""

import asyncio
import struct
import datetime
import sys

DEVICE_ADDR = "B532E5DA-6704-3640-9142-415175D91C45"
GW_DATA_CHAR = "00035b03-58e6-07dd-021a-08123a000301"

from bleak import BleakClient

buf = bytearray()
state = {
    "soc": None,
    "modules": None,
    "volt_V": None,
    "curr_A": None,
    "cells": {},     # cell_num -> voltage_V
    "temps": {},     # sensor_num -> temp_C
    "last_update": None,
}


# OCV→SOC lookup calibrated to this pack (3.6776V avg = 50% on physical display)
_SOC_TABLE = [
    (0,   2.80), (10,  3.40), (20,  3.55), (30,  3.62),
    (40,  3.65), (50,  3.675), (60,  3.70), (70,  3.76),
    (80,  3.84), (90,  3.95), (100, 4.15),
]

def _soc_from_voltage(v_avg: float) -> int:
    for i in range(len(_SOC_TABLE) - 1):
        s0, v0 = _SOC_TABLE[i]
        s1, v1 = _SOC_TABLE[i + 1]
        if v0 <= v_avg <= v1:
            return round(s0 + (s1 - s0) * (v_avg - v0) / (v1 - v0))
    return 0 if v_avg < _SOC_TABLE[0][1] else 100


def _update_soc():
    cells = [v for v in state["cells"].values() if v and v > 0.5]
    if cells:
        state["soc"] = _soc_from_voltage(sum(cells) / len(cells))


def parse_frame(msgid: int, data: bytes):
    if msgid == 0x01:
        state["modules"] = data[0]
        state["soh"] = data[3]   # State of Health (constant)

    elif msgid == 0x00:
        volt_mv = struct.unpack_from(">H", data, 1)[0]
        curr_ma = struct.unpack_from(">h", data, 3)[0]
        state["volt_V"] = volt_mv / 1000.0
        state["curr_A"] = curr_ma / 1000.0

    elif 0x11 <= msgid <= 0x14:
        base = (msgid - 0x11) * 4 + 1
        for i in range(4):
            raw = struct.unpack_from("<H", data, i * 2)[0]
            if 2500 <= raw <= 4200:
                state["cells"][base + i] = raw / 1000.0
        _update_soc()

    elif 0x24 <= msgid <= 0x26:
        base = (msgid - 0x24) * 4 + 1
        for i in range(4):
            raw = struct.unpack_from("<H", data, i * 2)[0]
            if raw not in (0xFDDC, 0x8000) and raw < 1000:
                state["temps"][base + i] = raw / 10.0


def try_parse(stream: bytearray):
    i = 0
    while i < len(stream) - 1:
        if stream[i] == 0x55 and stream[i + 1] == 0x55:
            if i + 20 > len(stream):
                break
            frame = bytes(stream[i:i + 20])
            if frame[-2:] == b'\x04\x04':
                msgid = frame[3]
                data = frame[9:17]
                parse_frame(msgid, data)
                state["last_update"] = datetime.datetime.now()
                i += 20
                continue
        i += 1
    return stream[i:]


printed_once = set()

def display():
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    s = state

    soc_str  = f"{s['soc']}%" if s["soc"] is not None else "---"
    volt_str = f"{s['volt_V']:.2f} V" if s["volt_V"] is not None else "---"
    curr_str = f"{s['curr_A']:+.3f} A" if s["curr_A"] is not None else "---"

    direction = ""
    if s["curr_A"] is not None:
        if s["curr_A"] > 0.1:
            direction = " ▲ charging"
        elif s["curr_A"] < -0.1:
            direction = " ▼ idle/discharge"

    print(f"\r[{ts}]  SOC: {soc_str:>5}   Pack: {volt_str}   Current: {curr_str}{direction}        ",
          end="", flush=True)


def notification_handler(_, raw: bytearray):
    global buf
    buf.extend(raw)
    buf = try_parse(buf)
    display()


async def main():
    print(f"Connecting to Volta VPS-1A50 at {DEVICE_ADDR} ...")
    async with BleakClient(DEVICE_ADDR) as client:
        print("Connected. Monitoring — press Ctrl+C to stop.\n")
        await client.start_notify(GW_DATA_CHAR, notification_handler)
        try:
            while True:
                await asyncio.sleep(1)
                display()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        print("\n\nFinal state:")
        print(f"  SOC:          ~{state['soc']}%  (empirical formula)")
        print(f"  SoH:          {state.get('soh')}%  (State of Health)")
        print(f"  Pack voltage: {state['volt_V']:.3f} V")
        print(f"  Pack current: {state['curr_A']:+.3f} A")
        if state["cells"]:
            cvs = list(state["cells"].values())
            print(f"  Cells ({len(cvs)}):   min={min(cvs):.3f}V  max={max(cvs):.3f}V  "
                  f"delta={1000*(max(cvs)-min(cvs)):.0f}mV")
        if state["temps"]:
            tvs = list(state["temps"].values())
            mn, mx = min(tvs), max(tvs)
            print(f"  Temps ({len(tvs)}):   min={mn:.1f}°C ({mn*9/5+32:.1f}°F)  "
                  f"max={mx:.1f}°C ({mx*9/5+32:.1f}°F)")
        try:
            await client.stop_notify(GW_DATA_CHAR)
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
