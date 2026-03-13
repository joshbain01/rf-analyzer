FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

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
