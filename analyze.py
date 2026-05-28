#!/usr/bin/env python3
"""Analyze the captured Volta CAN BLE Gateway stream.

Frame format (reverse-engineered from capture):
  55 55          - 2-byte sync word
  [type: 1]      - always 0x01 (CAN data frame)
  [msgid: 1]     - message ID / data category (see below)
  02 00 00 00    - CAN ID = 0x00000002 (little-endian)
  08             - DLC = 8 (always 8 data bytes)
  [data: 8]      - payload (interpretation depends on msgid)
  [chk: 1]       - checksum (XOR of data bytes)
  04 04          - 2-byte end marker

Message IDs observed:
  0x00  - Pack measurements: [ctr][volt_hi][volt_lo][fe][curr_lo][c8][15][02]
  0x01  - Pack info: [units][00][00][soc%][00][00][rem_Ah][??]
  0x11-0x14  - Cell voltages bank 1 (4 cells per msg, LE uint16 mV each)
  0x21-0x22  - Cell voltages bank 2 (all 0x8000 = no cell)
  0x24-0x26  - Temperatures (4 per msg, LE uint16 in 0.1°C)
  0x27-0x29  - Temperatures (all 0xFDDC = no sensor)
"""

import struct
import sys

SYNC = bytes([0x55, 0x55])
END  = bytes([0x04, 0x04])
FRAME_LEN = 20  # always 20 bytes per frame


def xor_check(data: bytes) -> int:
    result = 0
    for b in data:
        result ^= b
    return result


def reassemble_frames(stream: bytes) -> list[tuple[int, bytes]]:
    """Find all complete 20-byte frames in the stream. Returns (msgid, data_8bytes) pairs."""
    frames = []
    i = 0
    while i < len(stream) - FRAME_LEN + 1:
        if stream[i:i+2] == SYNC and stream[i+18:i+20] == END:
            frame = stream[i:i+20]
            # [55 55][type=01][msgid][02 00 00 00][08][data:8][chk][04 04]
            msgid = frame[3]
            data  = frame[9:17]
            chk   = frame[17]
            frames.append((msgid, data))
            i += FRAME_LEN
        else:
            i += 1
    return frames


SENTINEL_CELL = 0x8000   # no cell populated
SENTINEL_TEMP = 0xFDDC   # no temperature sensor

# OCV→SOC lookup calibrated to this pack:
# 3.6776V average = 50% confirmed on physical display (NMC-style curve)
_SOC_TABLE = [
    (0,   2.80), (10,  3.40), (20,  3.55), (30,  3.62),
    (40,  3.65), (50,  3.675), (60,  3.70), (70,  3.76),
    (80,  3.84), (90,  3.95), (100, 4.15),
]

def soc_from_cell_voltage(v_avg: float) -> int:
    for i in range(len(_SOC_TABLE) - 1):
        s0, v0 = _SOC_TABLE[i]
        s1, v1 = _SOC_TABLE[i + 1]
        if v0 <= v_avg <= v1:
            return round(s0 + (s1 - s0) * (v_avg - v0) / (v1 - v0))
    return 0 if v_avg < _SOC_TABLE[0][1] else 100


def decode_cell_voltages(data: bytes):
    cells = []
    for j in range(0, 8, 2):
        raw = struct.unpack_from("<H", data, j)[0]
        if raw == SENTINEL_CELL:
            cells.append(None)
        else:
            cells.append(raw / 1000.0)
    return cells


def decode_temperatures(data: bytes):
    temps = []
    for j in range(0, 8, 2):
        raw = struct.unpack_from("<H", data, j)[0]
        if raw == SENTINEL_TEMP or raw == 0x8000:
            temps.append(None)
        else:
            temps.append(raw / 10.0)
    return temps


def decode_pack_meas(data: bytes):
    """Message 0x00: pack voltage + current."""
    # Bytes 1-2 (big-endian uint16): pack voltage in mV
    volt_mv = struct.unpack_from(">H", data, 1)[0]
    # Bytes 3-4 (big-endian int16): pack current in mA (negative = discharge)
    curr_ma = struct.unpack_from(">h", data, 3)[0]
    return volt_mv / 1000.0, curr_ma / 1000.0


def decode_pack_info(data: bytes):
    """Message 0x01: battery count, SoH, remaining capacity counter.

    byte[0] = number of battery modules
    byte[3] = State of Health % (constant, not real-time SOC)
    byte[6] = remaining capacity counter (decreases during discharge)
    byte[7] = full-charge reference for byte[6]
    Empirical SOC: byte[6] / (byte[7] - byte[3]) ≈ display reading
    """
    num_units = data[0]
    soh       = data[3]    # State of Health %, constant
    rem_ctr   = data[6]    # remaining capacity counter
    full_ref  = data[7]    # full-charge reference

    # Empirical formula matched to physical display
    denom = full_ref - soh
    soc = round(rem_ctr / denom * 100) if denom > 0 else None
    return num_units, soh, rem_ctr, full_ref, soc


def analyze(path: str):
    with open(path, "rb") as f:
        stream = f.read()

    print(f"Stream length: {len(stream)} bytes\n")

    # Look for ASCII error/status strings
    try:
        text = stream.decode("latin-1")
        import re
        for m in re.finditer(r'[\x20-\x7E]{3,}', text):
            print(f"  ASCII in stream: {repr(m.group())}")
    except Exception:
        pass
    print()

    frames = reassemble_frames(stream)
    print(f"Found {len(frames)} complete frames\n")

    cells = {}    # msgid -> latest cell list
    temps = {}    # msgid -> latest temp list
    pack_volt = None
    pack_curr = None
    soc = None
    soh = None
    num_units = None
    rem_ctr = None
    full_ref = None

    for msgid, data in frames:
        if 0x11 <= msgid <= 0x14:
            cells[msgid] = decode_cell_voltages(data)
        elif msgid in (0x21, 0x22):
            pass  # second bank all empty
        elif 0x24 <= msgid <= 0x26:
            temps[msgid] = decode_temperatures(data)
        elif msgid == 0x00:
            pack_volt, pack_curr = decode_pack_meas(data)
        elif msgid == 0x01:
            num_units, soh, rem_ctr, full_ref, soc = decode_pack_info(data)

    # Compute SOC from average cell voltage (calibrated OCV curve)
    all_live_cells = [v for mid in sorted(cells) for v in cells[mid]
                      if v is not None and v > 0.5]
    soc_voltage = soc_from_cell_voltage(sum(all_live_cells) / len(all_live_cells)) \
                  if all_live_cells else None

    # --- Report ---
    print("=" * 50)
    print("  VOLTA BATTERY STATUS")
    print("=" * 50)

    if soc_voltage is not None:
        print(f"\n  SOC (State of Charge): ~{soc_voltage}%  (from cell voltages, calibrated to display)")
    if num_units is not None:
        print(f"  Battery modules:       {num_units}")
    if soh is not None:
        print(f"  State of Health:       {soh}%")
    if pack_volt is not None:
        print(f"  Pack voltage:          {pack_volt:.3f} V")
    if pack_curr is not None:
        direction = "charging" if pack_curr > 0.1 else ("discharging" if pack_curr < -0.1 else "idle")
        print(f"  Pack current:          {pack_curr:.3f} A  ({direction})")

    print("\n  Cell voltages:")
    all_cells = []
    for msgid in sorted(cells.keys()):
        for i, v in enumerate(cells[msgid]):
            cell_num = (msgid - 0x11) * 4 + i + 1
            if v is not None and v > 0.5:   # filter sentinel zeros
                all_cells.append(v)
                print(f"    Cell {cell_num:2d}: {v:.3f} V")
            elif v is None:
                pass  # skip no-cell slots silently
    if all_cells:
        print(f"  Min: {min(all_cells):.3f} V  Max: {max(all_cells):.3f} V  "
              f"Delta: {(max(all_cells)-min(all_cells))*1000:.1f} mV")

    print("\n  Temperatures:")
    for msgid in sorted(temps.keys()):
        for i, t in enumerate(temps[msgid]):
            sensor_num = (msgid - 0x24) * 4 + i + 1
            if t is not None:
                print(f"    Sensor {sensor_num}: {t:.1f} °C  ({t * 9/5 + 32:.1f} °F)")

    print()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "capture.bin"
    analyze(path)
