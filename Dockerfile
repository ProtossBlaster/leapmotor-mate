# HA add-on build: BUILD_FROM is overridden by build.json per architecture
# Standalone test: uses python:3.12-slim default
ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}

LABEL \
    io.hass.name="LeapMotor Mate" \
    io.hass.description="Trip tracking and remote control for Leapmotor vehicles" \
    io.hass.type="addon" \
    io.hass.version="1.0.5"

WORKDIR /app

COPY poller/requirements.txt /tmp/poller-req.txt
COPY web/requirements.txt /tmp/web-req.txt
RUN pip install --no-cache-dir \
    -r /tmp/poller-req.txt \
    -r /tmp/web-req.txt

COPY certs/  /app/certs/
COPY poller/ /app/poller/
COPY web/    /app/web/
COPY run.sh  /run.sh
RUN chmod a+x /run.sh

ENV PYTHONUNBUFFERED=1
ENV CERT_DIR=/app/certs
ENV DB_PATH=/data/leapmotor_mate.db
# Bind all interfaces inside the container; real exposure is controlled by the
# Docker port mapping / HA ingress. run.sh also sets this.
ENV WEB_HOST=0.0.0.0

CMD ["/run.sh"]
