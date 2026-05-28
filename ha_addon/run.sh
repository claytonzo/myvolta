#!/usr/bin/with-contenv bashio

DEVICE_ADDR=$(bashio::config 'device_addr')
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_user')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')
POLL_INTERVAL=$(bashio::config 'poll_interval')

# Clean up any stale BLE connection left by a previous run
bashio::log.info "Clearing stale connection for ${DEVICE_ADDR} ..."
bluetoothctl disconnect "${DEVICE_ADDR}" 2>/dev/null || true
sleep 3

# Pre-scan and trust so BlueZ has a persistent entry for the device
bashio::log.info "Pre-scanning for ${DEVICE_ADDR} (20s) ..."
bluetoothctl --timeout 20 scan on 2>/dev/null || true
bluetoothctl trust "${DEVICE_ADDR}" 2>/dev/null \
    && bashio::log.info "Device trusted." \
    || bashio::log.warning "Trust failed — device may not be visible yet"

# Verify Python environment before launching
bashio::log.info "Python: $(python3 --version 2>&1)"
python3 -c "import bleak" 2>&1 && bashio::log.info "bleak: ok" || bashio::log.error "bleak import FAILED"
python3 -c "import paho.mqtt.client" 2>&1 && bashio::log.info "paho-mqtt: ok" || bashio::log.error "paho-mqtt import FAILED"
bashio::log.info "Script: $(ls -la /volta_mqtt.py 2>&1)"

bashio::log.info "Launching volta_mqtt.py ..."
exec python3 -u /volta_mqtt.py \
    --device "$DEVICE_ADDR" \
    --mqtt-host "$MQTT_HOST" \
    --mqtt-port "$MQTT_PORT" \
    --mqtt-user "$MQTT_USER" \
    --mqtt-password "$MQTT_PASSWORD" \
    --interval "$POLL_INTERVAL"
