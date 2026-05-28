#!/usr/bin/env python3
"""Volta VPS-1A50 → MQTT bridge with Home Assistant auto-discovery.

Connects to the Volta CAN BLE Gateway, decodes battery data, and publishes
to MQTT so Home Assistant can display it via sensor entities.
"""

import argparse
import asyncio
import json
import logging
import signal
import struct
import datetime

import paho.mqtt.client as mqtt
from bleak import BleakClient, BleakError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("volta")

GW_DATA_CHAR = "00035b03-58e6-07dd-021a-08123a000301"

_SOC_TABLE = [
    (0,   2.80), (10,  3.40), (20,  3.55), (30,  3.62),
    (40,  3.65), (50,  3.675), (60,  3.70), (70,  3.76),
    (80,  3.84), (90,  3.95), (100, 4.15),
]

HA_SENSORS = [
    # (unique_id, name, unit, device_class, state_class, value_key, icon)
    ("soc",        "Battery SOC",        "%",   "battery",     "measurement", "soc",        None),
    ("soh",        "Battery SoH",        "%",   None,          "measurement", "soh",        "mdi:battery-heart"),
    ("voltage",    "Pack Voltage",       "V",   "voltage",     "measurement", "volt_V",     None),
    ("current",    "Pack Current",       "A",   "current",     "measurement", "curr_A",     None),
    ("cell_min",   "Cell Voltage Min",   "V",   "voltage",     "measurement", "cell_min_V", None),
    ("cell_max",   "Cell Voltage Max",   "V",   "voltage",     "measurement", "cell_max_V", None),
    ("cell_delta", "Cell Delta",         "mV",  None,          "measurement", "cell_delta_mV", "mdi:battery-alert"),
    ("temp_max",   "Battery Temp Max",   "°C",  "temperature", "measurement", "temp_max_C", None),
    ("modules",    "Battery Modules",    None,  None,          None,          "modules",    "mdi:battery-charging"),
]


def _soc_from_voltage(v_avg):
    for i in range(len(_SOC_TABLE) - 1):
        s0, v0 = _SOC_TABLE[i]
        s1, v1 = _SOC_TABLE[i + 1]
        if v0 <= v_avg <= v1:
            return round(s0 + (s1 - s0) * (v_avg - v0) / (v1 - v0))
    return 0 if v_avg < _SOC_TABLE[0][1] else 100


class VoltaState:
    def __init__(self):
        self.modules = None
        self.soh = None
        self.volt_V = None
        self.curr_A = None
        self.cells = {}
        self.temps = {}
        self.buf = bytearray()

    def parse_frame(self, msgid, data):
        if msgid == 0x01:
            self.modules = data[0]
            self.soh = data[3]

        elif msgid == 0x00:
            self.volt_V = struct.unpack_from(">H", data, 1)[0] / 1000.0
            self.curr_A = struct.unpack_from(">h", data, 3)[0] / 1000.0

        elif 0x11 <= msgid <= 0x14:
            base = (msgid - 0x11) * 4 + 1
            for i in range(4):
                raw = struct.unpack_from("<H", data, i * 2)[0]
                if 2500 <= raw <= 4200:
                    self.cells[base + i] = raw / 1000.0

        elif 0x24 <= msgid <= 0x26:
            base = (msgid - 0x24) * 4 + 1
            for i in range(4):
                raw = struct.unpack_from("<H", data, i * 2)[0]
                if raw not in (0xFDDC, 0x8000) and raw < 1000:
                    self.temps[base + i] = raw / 10.0

    def ingest(self, raw):
        self.buf.extend(raw)
        i = 0
        while i < len(self.buf) - 1:
            if self.buf[i] == 0x55 and self.buf[i + 1] == 0x55:
                if i + 20 > len(self.buf):
                    break
                frame = bytes(self.buf[i:i + 20])
                if frame[-2:] == b'\x04\x04':
                    self.parse_frame(frame[3], frame[9:17])
                    i += 20
                    continue
            i += 1
        self.buf = self.buf[i:]

    def snapshot(self):
        cells = [v for v in self.cells.values() if v and v > 0.5]
        soc = _soc_from_voltage(sum(cells) / len(cells)) if cells else None
        temps = [t for t in self.temps.values() if t is not None]

        return {
            "soc":          soc,
            "soh":          self.soh,
            "volt_V":       round(self.volt_V, 3) if self.volt_V is not None else None,
            "curr_A":       round(self.curr_A, 3) if self.curr_A is not None else None,
            "cell_min_V":   round(min(cells), 3) if cells else None,
            "cell_max_V":   round(max(cells), 3) if cells else None,
            "cell_delta_mV": round((max(cells) - min(cells)) * 1000, 1) if len(cells) > 1 else None,
            "temp_max_C":   round(max(temps), 1) if temps else None,
            "modules":      self.modules,
            "timestamp":    datetime.datetime.now().isoformat(),
        }


def publish_discovery(client, base_topic):
    device = {
        "identifiers": ["volta_vps1a50"],
        "name": "Volta Battery Pack",
        "model": "VPS-1A50",
        "manufacturer": "Volta Power Systems / Embedded One",
    }

    for uid, name, unit, dev_class, state_class, key, icon in HA_SENSORS:
        payload = {
            "unique_id": f"volta_{uid}",
            "name": name,
            "state_topic": f"{base_topic}/state",
            "value_template": f"{{{{ value_json.{key} }}}}",
            "device": device,
            "availability_topic": f"{base_topic}/availability",
        }
        if unit:
            payload["unit_of_measurement"] = unit
        if dev_class:
            payload["device_class"] = dev_class
        if state_class:
            payload["state_class"] = state_class
        if icon:
            payload["icon"] = icon

        config_topic = f"homeassistant/sensor/volta_{uid}/config"
        client.publish(config_topic, json.dumps(payload), retain=True)
        log.info("Published discovery: %s", config_topic)


async def monitor_forever(device_addr, publish_interval, state, publish_fn):
    """Maintain a persistent BLE connection and publish data every publish_interval seconds."""
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    while not stop_event.is_set():
        try:
            log.info("Connecting to %s ...", device_addr)
            async with BleakClient(device_addr, timeout=30.0) as client:
                log.info("Connected.")
                await client.start_notify(GW_DATA_CHAR, lambda _, raw: state.ingest(bytearray(raw)))
                last_pub = loop.time()
                while not stop_event.is_set():
                    await asyncio.sleep(5)
                    now = loop.time()
                    if now - last_pub >= publish_interval:
                        publish_fn(state.snapshot())
                        last_pub = now
                log.info("Disconnecting cleanly ...")
        except BleakError as e:
            if stop_event.is_set():
                break
            log.warning("BLE disconnected (%s): %s — reconnecting in 30s", type(e).__name__, e)
            await asyncio.sleep(30)
        except Exception as e:
            if stop_event.is_set():
                break
            log.error("Unexpected error (%s): %s — reconnecting in 60s", type(e).__name__, e, exc_info=True)
            await asyncio.sleep(60)

    log.info("Shutdown complete.")


def run(args):
    mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="volta_monitor", clean_session=True)
    if args.mqtt_user:
        mqttc.username_pw_set(args.mqtt_user, args.mqtt_password)

    base = "volta/battery"

    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
        else:
            log.info("MQTT connected to %s:%s", args.mqtt_host, args.mqtt_port)
            client.publish(f"{base}/availability", "online", retain=True)
            publish_discovery(client, base)

    mqttc.on_connect = on_connect
    mqttc.will_set(f"{base}/availability", "offline", retain=True)
    mqttc.connect(args.mqtt_host, args.mqtt_port, keepalive=60)
    mqttc.loop_start()

    state = VoltaState()

    def publish_fn(snap):
        mqttc.publish(f"{base}/state", json.dumps(snap), retain=False)
        log.info("Published: SOC=%s%% V=%.3f A=%+.3f",
                 snap["soc"], snap["volt_V"] or 0, snap["curr_A"] or 0)

    try:
        asyncio.run(monitor_forever(args.device, args.interval, state, publish_fn))
    finally:
        mqttc.publish(f"{base}/availability", "offline", retain=True)
        mqttc.loop_stop()
        mqttc.disconnect()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--device",        default="E8:EB:1B:F9:1A:50")
    p.add_argument("--mqtt-host",     default="localhost")
    p.add_argument("--mqtt-port",     type=int, default=1883)
    p.add_argument("--mqtt-user",     default="")
    p.add_argument("--mqtt-password", default="")
    p.add_argument("--interval",      type=int, default=30,
                   help="seconds to collect BLE data per cycle")
    run(p.parse_args())
