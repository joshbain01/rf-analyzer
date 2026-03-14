FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        rtl-sdr \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py README.md requirements.txt ./
COPY rf_monitor ./rf_monitor

RUN pip install --no-cache-dir .

RUN mkdir -p /data/scans

ENTRYPOINT ["rf-monitor"]
CMD ["monitor"]
