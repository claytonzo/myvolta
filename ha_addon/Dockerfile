ARG BUILD_FROM=ghcr.io/hassio-addons/base:16.3.2
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip bluez bluez-libs dbus && \
    pip3 install --no-cache-dir bleak paho-mqtt

COPY volta_mqtt.py /volta_mqtt.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
