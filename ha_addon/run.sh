#!/usr/bin/with-contenv bashio

DEVICE_ADDR=$(bashio::config 'device_addr')
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_user')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')
POLL_INTERVAL=$(bashio::config 'poll_interval')

# Pre-scan to populate BlueZ cache and trust device
bashio::log.info "Pre-scanning for ${DEVICE_ADDR} ..."
bluetoothctl scan on &
SCAN_PID=$!
sleep 20
bluetoothctl trust "${DEVICE_ADDR}" \
    && bashio::log.info "Device trusted." \
    || bashio::log.warning "Trust failed — device may not be visible yet"
kill $SCAN_PID 2>/dev/null
wait $SCAN_PID 2>/dev/null

# Verify Python environment before launching
bashio::log.info "Python: $(python3 --version 2>&1)"
python3 -c "import bleak; print('bleak ok')" 2>&1 \
    && bashio::log.info "bleak: ok" \
    || bashio::log.error "bleak import FAILED"
python3 -c "import paho.mqtt.client" 2>&1 \
    && bashio::log.info "paho-mqtt: ok" \
    || bashio::log.error "paho-mqtt import FAILED"
bashio::log.info "Script: $(ls -la /volta_mqtt.py 2>&1)"

bashio::log.info "Launching volta_mqtt.py ..."
exec python3 -u /volta_mqtt.py \
    --device "$DEVICE_ADDR" \
    --mqtt-host "$MQTT_HOST" \
    --mqtt-port "$MQTT_PORT" \
    --mqtt-user "$MQTT_USER" \
    --mqtt-password "$MQTT_PASSWORD" \
    --interval "$POLL_INTERVAL"
