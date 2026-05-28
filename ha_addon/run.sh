#!/usr/bin/with-contenv bashio

DEVICE_ADDR=$(bashio::config 'device_addr')
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_user')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')
POLL_INTERVAL=$(bashio::config 'poll_interval')

# Scan for the device and trust it so BlueZ keeps a permanent entry
bashio::log.info "Scanning for Volta gateway (${DEVICE_ADDR}) ..."
bluetoothctl scan on &
SCAN_PID=$!
sleep 20
bluetoothctl trust "${DEVICE_ADDR}" && bashio::log.info "Device trusted." || bashio::log.warning "Trust failed (device may not be visible yet)"
kill $SCAN_PID 2>/dev/null
wait $SCAN_PID 2>/dev/null

exec python3 /volta_mqtt.py \
    --device "$DEVICE_ADDR" \
    --mqtt-host "$MQTT_HOST" \
    --mqtt-port "$MQTT_PORT" \
    --mqtt-user "$MQTT_USER" \
    --mqtt-password "$MQTT_PASSWORD" \
    --interval "$POLL_INTERVAL"
