# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Reverse-engineering the BLE interface of a Volta Power Systems battery pack in a 2020 Winnebago Travato 59GL. The Volta app stopped working; these scripts read State of Charge (SOC) and other battery stats directly over Bluetooth.

## Dependencies

```bash
pip3 install bleak paho-mqtt
```

Python 3.9 (macOS system Python via Xcode). **Do not use `X | Y` union type syntax** — it requires 3.10+. Use `Optional[X]` or skip annotations instead. The Pi runs Python 3.12 (Alpine).

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

**There is no explicit SOC byte in the stream.** `volta_mqtt.py` uses a hybrid approach:

- **At rest** (`|current| < 2A`): recalibrates from OCV (open-circuit voltage) via a lookup table matched to the physical display (3.6776V avg = 50% SOC).
- **During charge/discharge** (`|current| ≥ 2A`): integrates coulombs (`current × Δt / capacity_Ah`) to track SOC smoothly without OCV distortion.
- **Capacity**: 224 Ah (11,600 Wh NPE Pure3 system ÷ 51.8V nominal), adjusted by SoH (83%) for ~186 Ah effective.

```python
_SOC_TABLE = [
    (0, 2.80), (10, 3.40), (20, 3.55), (30, 3.62),
    (40, 3.65), (50, 3.675), (60, 3.70), (70, 3.76),
    (80, 3.84), (90, 3.95), (100, 4.15),
]
```

byte[3] of msg `0x01` = 83 is **State of Health** (constant), not SOC. Do not use it as SOC.

## Home Assistant add-on (`ha_addon/`)

Runs on a Raspberry Pi permanently installed in the RV. The Pi's built-in Bluetooth connects directly to the VPS-1A50.

Architecture: persistent BLE connection → BLE notifications → `VoltaState` parser → MQTT publish every `poll_interval` seconds → HA auto-discovery sensors.

- `config.yaml` — HA add-on metadata; requires `host_dbus: true` and `host_network: true` for Bluetooth
- `Dockerfile` — Alpine + Python3 + bleak + paho-mqtt
- `run.sh` — clears stale BLE state, pre-scans 45s to populate BlueZ cache, then launches `volta_mqtt.py`
- `volta_mqtt.py` — main bridge; publishes HA auto-discovery configs to `homeassistant/sensor/volta_*/config` and state JSON to `volta/battery/state`
- `dashboard.yaml` — HA dashboard YAML (gauge, glance, history cards)

HA sensors published: SOC (%), SoH (%), pack voltage (V), pack current (A), cell min/max/delta (V/mV), max temp (°C), module count, power (W).

The add-on maintains a **persistent BLE connection** and reconnects with backoff (30s on BLE error, 60s on unexpected error). The Pi's Bluetooth must not be claimed by HA's built-in Bluetooth integration simultaneously — disable that integration if installed.

MQTT host defaults to `localhost` (requires `host_network: true`; `core-mosquitto` hostname does not resolve with host networking).

## Deploying changes to the Pi

The git repo lives at `/addons/myvolta/`; HA runs the add-on from `/addons/volta_battery_monitor/`. After pushing:

```bash
cd /addons/myvolta && git pull
cp /addons/myvolta/ha_addon/volta_mqtt.py /addons/volta_battery_monitor/volta_mqtt.py
cp /addons/myvolta/ha_addon/run.sh /addons/volta_battery_monitor/run.sh
```

Then restart the add-on in HA. A rebuild is only needed when `Dockerfile` or `config.yaml` changes.

## BlueZ troubleshooting

**Symptom**: `TimeoutError` on every connect attempt despite device being visible in scan.

**Cause**: BlueZ holds a stale connection (`Connected: yes`) from a previous unclean disconnect, blocking new connections.

**Fix** (from HA terminal):
```bash
bluetoothctl info E8:EB:1B:F9:1A:50   # confirm Connected: yes
bluetoothctl disconnect E8:EB:1B:F9:1A:50
# add-on will reconnect automatically on next retry
```

The `run.sh` startup disconnect should prevent this, but it can still occur after abnormal termination.
