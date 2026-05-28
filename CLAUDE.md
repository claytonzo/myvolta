# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Reverse-engineering the BLE interface of a Volta Power Systems battery pack in a 2020 Winnebago Travato 59GL. The Volta app stopped working; these scripts read State of Charge (SOC) and other battery stats directly over Bluetooth.

## Dependencies

```bash
pip3 install bleak paho-mqtt
```

Python 3.9 (macOS system Python via Xcode). **Do not use `X | Y` union type syntax** — it requires 3.10+. Use `Optional[X]` or skip annotations instead.

## Device

- **Hardware**: Embedded One CAN BLE Gateway, model VPS-1A50, hw 4.1, fw 1.33BEC
- **BLE address (macOS UUID)**: `B532E5DA-6704-3640-9142-415175D91C45`
- **BLE address (Linux MAC)**: `E8:EB:1B:F9:1A:50`
- **Notify+write characteristic**: `00035b03-58e6-07dd-021a-08123a000301`
- **Write-only ctrl characteristic**: `00035b03-58e6-07dd-021a-08123a0003ff`
- **Do not write to the ctrl characteristic** — sending probe commands causes an `Err\r\n` gateway error and disrupts the stream.
- `stop_notify` always raises `BleakError: Writing is not permitted` on this device — always wrap in `try/except`.

## Running the scripts

```bash
# Scan for BLE devices (find the Volta gateway)
python3 scan.py

# Dump all GATT services and characteristics
python3 enumerate.py "B532E5DA-6704-3640-9142-415175D91C45"

# Capture 10s of raw BLE data to capture.bin + capture.txt
python3 capture.py

# Analyze a captured binary offline
python3 analyze.py capture.bin

# Live monitor — SOC and key stats, updates every second
python3 monitor.py
```

## Protocol: BLE frame format

The gateway wraps CAN bus frames in a fixed 20-byte envelope:

```
[55 55] [type=01] [msgid] [02 00 00 00] [08] [data: 8 bytes] [chk] [04 04]
 sync    frame     msg id  CAN ID LE     DLC  payload          xor   end
```

Frame sync: `55 55` at bytes 0–1. End marker: `04 04` at bytes 18–19. Always exactly 20 bytes. The parser in `monitor.py` and `analyze.py` scans for `55 55` and validates the `04 04` tail.

## Message IDs and decoding

| msgid | Content | Decoding |
|-------|---------|----------|
| `0x00` | Pack voltage + current | bytes[1:3] big-endian uint16 = mV; bytes[3:5] big-endian int16 = mA |
| `0x01` | Pack info | byte[0]=module count, byte[3]=SoH% (constant ~83, NOT SOC) |
| `0x11–0x14` | Cell voltages (4 cells each, bank 1) | LE uint16 pairs in mV; sentinel `0x8000` = no cell |
| `0x24–0x26` | Temperatures (4 each) | LE uint16 in 0.1°C; sentinels `0xFDDC` and `0x8000` = no sensor |

14 active cells (cells 15–16 are always sentinel zeros). Pack is 14S NMC chemistry.

## SOC calculation

**There is no explicit SOC byte in the stream.** SOC is derived from average cell voltage via a calibrated OCV curve. The curve was empirically matched to the physical display (3.6776V avg = 50% SOC):

```python
_SOC_TABLE = [
    (0, 2.80), (10, 3.40), (20, 3.55), (30, 3.62),
    (40, 3.65), (50, 3.675), (60, 3.70), (70, 3.76),
    (80, 3.84), (90, 3.95), (100, 4.15),
]
```

byte[3] of msg `0x01` = 83 is **State of Health** (constant), not SOC. Do not use it as SOC.

## Home Assistant add-on (`ha_addon/`)

Runs on the Raspberry Pi 4 permanently installed in the RV. The Pi's built-in Bluetooth connects directly to the VPS-1A50.

Architecture: BLE notifications → `VoltaState` parser → MQTT publish → HA auto-discovery sensors.

- `config.yaml` — HA add-on metadata; requires `host_dbus: true` and `host_network: true` for Bluetooth
- `Dockerfile` — Alpine + Python3 + bleak + paho-mqtt
- `run.sh` — reads add-on config via `bashio`, passes as CLI args to `volta_mqtt.py`
- `volta_mqtt.py` — main bridge; publishes HA auto-discovery configs to `homeassistant/sensor/volta_*/config` and state JSON to `volta/battery/state`
- `dashboard.yaml` — HA dashboard YAML (gauge, glance, history cards)

The add-on reconnects on BLE errors with backoff. It collects data for `poll_interval` seconds per cycle (default 30s) then disconnects and republishes. The Pi's Bluetooth must not be claimed by HA's built-in Bluetooth integration simultaneously — disable that integration if installed.

MQTT host defaults to `core-mosquitto` (the Mosquitto broker HA add-on's internal hostname).
