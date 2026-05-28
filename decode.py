#!/usr/bin/env python3
"""Stream reassembler for the Embedded One CAN BLE Gateway (VPS-1A50).

Protocol framing (reverse-engineered):
  [55 55]  - 2-byte sync word
  [01]     - frame type (CAN frame)
  [LEN]    - total frame length in bytes (including sync + end marker)
  [ID: 4B LE] - CAN ID (29-bit extended, little-endian)
  [DLC]    - CAN data length (0-8)
  [DATA]   - DLC bytes of CAN payload
  [CHK]    - checksum (XOR or sum of body bytes, TBD)
  [04]     - end-of-frame marker

CAN ID 0x00000002: battery pack data (voltages, SOC, temps)
"""

import asyncio
import struct
import sys
import datetime

DEVICE_ADDR = "B532E5DA-6704-3640-9142-415175D91C45"
GW_DATA_CHAR = "00035b03-58e6-07dd-021a-08123a000301"
GW_CTRL_CHAR = "00035b03-58e6-07dd-021a-08123a0003ff"

from bleak import BleakClient

stream_buf = bytearray()


def parse_can_payload(can_id: int, data: bytes):
    """Decode known CAN IDs from the Volta/Embedded One gateway."""
    results = {}

    if can_id == 0x00000002:
        # Pack-level data: SOC + cell voltages
        # Byte 0: SOC (0-100%)
        # Bytes 1-2, 3-4, 5-6, 7-8: cell voltages LE uint16 in mV
        if len(data) >= 1:
            soc = data[0]
            if 0 <= soc <= 100:
                results["soc_%"] = soc

        cells = []
        for i in range(1, len(data) - 1, 2):
            mv = struct.unpack_from("<H", data, i)[0]
            if 2500 <= mv <= 4200:  # valid LiFePO4 range
                cells.append(mv / 1000.0)
        if cells:
            results["cells_V"] = cells

    return results


def try_parse_frames(buf: bytearray):
    """Find and parse complete frames in the byte buffer.
    Returns (parsed_frames_list, remaining_buf).
    """
    frames = []
    i = 0
    while i < len(buf) - 1:
        # Look for sync word 55 55
        if buf[i] == 0x55 and buf[i+1] == 0x55:
            if i + 3 >= len(buf):
                break  # need more bytes
            frame_len = buf[i+2+1]  # byte after type is length
            # Wait for complete frame
            if i + frame_len > len(buf):
                break  # incomplete frame, wait for more data
            frame = bytes(buf[i:i+frame_len])
            # Verify end marker
            if frame[-1] == 0x04:
                frames.append(frame)
                i += frame_len
                continue
        i += 1
    return frames, buf[i:]


def decode_frame(frame: bytes):
    """Decode a complete 55-55-framed CAN gateway message."""
    if len(frame) < 9:
        return None
    # [55][55][type][len][ID:4][DLC][data...][chk][04]
    ftype = frame[2]
    flen  = frame[3]
    can_id = struct.unpack_from("<I", frame, 4)[0]
    dlc    = frame[8]
    if dlc > 8 or 9 + dlc + 2 > len(frame):
        return None
    data = frame[9:9+dlc]
    chk  = frame[9+dlc]
    end  = frame[-1]
    return {
        "type":   ftype,
        "can_id": can_id,
        "dlc":    dlc,
        "data":   data,
        "chk":    chk,
    }


def notification_handler(_, raw: bytearray):
    global stream_buf
    stream_buf.extend(raw)

    frames, stream_buf = try_parse_frames(stream_buf)

    for frame_bytes in frames:
        decoded = decode_frame(frame_bytes)
        if not decoded:
            continue

        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        can_id = decoded["can_id"]
        data   = decoded["data"]
        hex_data = data.hex()

        parsed = parse_can_payload(can_id, data)

        # Always print raw frame
        print(f"[{ts}] CAN_ID=0x{can_id:08X}  data={hex_data}  ({list(data)})")

        if parsed:
            if "soc_%" in parsed:
                print(f"         *** SOC: {parsed['soc_%']}% ***")
            if "cells_V" in parsed:
                vstr = "  ".join(f"{v:.3f}V" for v in parsed["cells_V"])
                print(f"         Cells: {vstr}")
            if "temp_C" in parsed:
                print(f"         Temp: {parsed['temp_C']}°C")


async def main(address: str):
    print(f"Connecting to {address} ...")
    async with BleakClient(address) as client:
        print("Connected. Reassembling CAN frame stream...\n")
        await client.start_notify(GW_DATA_CHAR, notification_handler)

        # Send enable command
        for cmd in [bytes([0x01]), bytes([0x02])]:
            try:
                await client.write_gatt_char(GW_CTRL_CHAR, cmd, response=False)
            except Exception:
                pass

        await asyncio.sleep(15)
        await client.stop_notify(GW_DATA_CHAR)

        # Dump any remaining unframed bytes
        if stream_buf:
            print(f"\nUnframed bytes remaining: {stream_buf.hex()}")
            print("Raw byte stream (hex):     " +
                  " ".join(f"{b:02x}" for b in stream_buf))


if __name__ == "__main__":
    addr = sys.argv[1] if len(sys.argv) > 1 else DEVICE_ADDR
    asyncio.run(main(addr))
