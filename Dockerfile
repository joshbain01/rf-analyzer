FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    RF_MONITOR_CONFIG=/config/config.json \
    RF_MONITOR_OUTPUT_DIR=/data/logs \
    RF_MONITOR_FREQ_START=100M \
    RF_MONITOR_FREQ_END=500M \
    RF_MONITOR_BIN_SIZE=50k \
    RF_MONITOR_INTEGRATION_TIME=0.4s \
    RF_MONITOR_INTERVAL=60 \
    RF_MONITOR_GAIN=40 \
    RF_MONITOR_ALERT_THRESHOLD=-50 \
    RF_MONITOR_MAX_LOG_AGE=7

# rtl_power binary is provided by rtl-sdr.
RUN apt-get update \
    && apt-get install -y --no-install-recommends rtl-sdr libusb-1.0-0 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . /app

RUN pip install --no-cache-dir .

RUN mkdir -p /data/logs /config

VOLUME ["/data", "/config"]

ENTRYPOINT ["rf-monitor"]
CMD ["--help"]
