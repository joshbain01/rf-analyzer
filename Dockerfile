FROM python:3.11-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.lock ./
RUN apt-get update && apt-get install -y --no-install-recommends \
    rtl-sdr \
    python3-numpy \
    python3-pandas \
    python3-matplotlib \
    build-essential \
    gcc \
    g++ \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml setup.py README.md requirements.txt ./
COPY rf_monitor ./rf_monitor

RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir .

RUN mkdir -p /data/scans

ENTRYPOINT ["rf-monitor"]
CMD ["monitor"]
