# Builds the GHCR image (docker-publish.yml, multi-arch amd64+arm64 via buildx).
# The HA add-on PULLS this prebuilt image, so there's no per-arch build override any more —
# always the slim base. BUILD_FROM stays overridable for ad-hoc local builds only.
ARG BUILD_FROM=python:3.12-slim
FROM ${BUILD_FROM}

LABEL \
    io.hass.name="LeapMotor Mate" \
    io.hass.description="Trip tracking and remote control for Leapmotor vehicles" \
    io.hass.type="addon" \
    io.hass.version="3.15.8"

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

# MateBetaTesterOnly flag. 0 in the official image (the research code stays inert); the CI
# (docker-publish.yml) builds a second ":beta" image with --build-arg MATE_RESEARCH=1, which
# turns on full-signal capture + the encrypted research export. Inherited by poller + web.
ARG MATE_RESEARCH=0
ENV MATE_RESEARCH=${MATE_RESEARCH}

# Declare the web port so Docker Desktop's "Run" pre-fills the port mapping. Without
# this the Run dialog shows "No ports exposed in this image" and the user can't reach
# the UI (a frequent first-run dead end).
EXPOSE 4000

# Persist the data dir even when the user forgets `-v ...:/data`: Docker creates an
# anonymous volume, so trips/charges/login survive a container recreate instead of
# living in the throwaway container layer.
VOLUME /data

# Liveness: hit /healthz (200 while awaiting setup or polling recently, 503 if wedged).
# Uses python (no curl in the slim image). start-period covers first boot.
HEALTHCHECK --interval=60s --timeout=10s --start-period=45s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:4000/healthz', timeout=8)" || exit 1

CMD ["/run.sh"]
