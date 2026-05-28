#!/usr/bin/with-contenv bashio

DEVICE_ADDR=$(bashio::config 'device_addr')
MQTT_HOST=$(bashio::config 'mqtt_host')
MQTT_PORT=$(bashio::config 'mqtt_port')
MQTT_USER=$(bashio::config 'mqtt_user')
MQTT_PASSWORD=$(bashio::config 'mqtt_password')
POLL_INTERVAL=$(bashio::config 'poll_interval')

exec python3 /volta_mqtt.py \
    --device "$DEVICE_ADDR" \
    --mqtt-host "$MQTT_HOST" \
    --mqtt-port "$MQTT_PORT" \
    --mqtt-user "$MQTT_USER" \
    --mqtt-password "$MQTT_PASSWORD" \
    --interval "$POLL_INTERVAL"
