# rf-monitor

RF spectrum monitoring and jamming detection CLI tool using RTL-SDR.

`rf-monitor` wraps `rtl_power` to capture periodic spectrum snapshots and detect interference in target bands. This repository now supports **Docker-only deployment** for Raspberry Pi.

## Deployment Model (Container-Only)

This project is operated with Docker Compose only.

- **Host responsibilities (Raspberry Pi OS):**
  - Docker Engine + Docker Compose plugin
  - USB device access for RTL-SDR dongle
  - RTL-SDR kernel/udev preparation (`docker/host-prep-rtlsdr.sh`)
- **Container responsibilities:**
  - Python runtime
  - `rf-monitor` package installation
  - `rtl-sdr` user-space tooling (`rtl_power`, `rtl_test`)
  - Running `rf-monitor` CLI commands

> Containers package the app and user-space dependencies, but host USB and kernel driver readiness are still required for RTL-SDR hardware.

## Quick Start (Raspberry Pi)

### 1) Install Docker on host

Install Docker Engine and Compose plugin on Raspberry Pi OS, then verify:

```bash
docker --version
docker compose version
```

### 2) Prepare host for RTL-SDR USB access

```bash
sudo bash docker/host-prep-rtlsdr.sh
```

This helper installs host RTL-SDR tools, blacklists conflicting DVB modules, reloads udev rules, checks `lsusb`, and runs `rtl_test -t`.

### 3) Configure runtime environment

Optional: export environment variables or place them in a shell/.env context used by Compose:

```bash
export RF_MONITOR_FREQ_START=100M
export RF_MONITOR_FREQ_END=500M
export RF_MONITOR_BIN_SIZE=50k
export RF_MONITOR_INTERVAL=60
export RF_MONITOR_GAIN=40
export RF_MONITOR_ALERT_THRESHOLD=-50
```

### 4) Build and run

```bash
docker compose build
docker compose up -d
```

### 5) Check logs

```bash
docker compose logs -f rf-monitor
```

## Compose Runtime Details

`compose.yml` runs one service (`rf-monitor`) with:

- USB passthrough: `/dev/bus/usb:/dev/bus/usb`
- Persistent output volume: `./data/scans:/data/scans`
- Restart policy: `unless-stopped`
- Default command: `rf-monitor monitor`
- Default output path in container: `RF_MONITOR_OUTPUT_DIR=/data/scans`

## CLI Usage in Container

Run one-off commands against the same image:

```bash
# One-off scan
docker compose run --rm rf-monitor scan --analyze

# Show merged config
docker compose run --rm rf-monitor config show

# Verify tool availability in container
docker compose run --rm rf-monitor --help
docker compose run --rm rf-monitor scan --help
```

## Persistent Data

Scan output is persisted at:

- **Host path:** `./data/scans`
- **Container path:** `/data/scans`

If you set a different output directory, ensure it is backed by a mounted volume.

## Verification Steps

### Verify RTL-SDR from host

```bash
lsusb | grep -Ei 'Realtek|RTL|0bda:'
rtl_test -t
```

### Verify app from container

```bash
docker compose run --rm rf-monitor --version
docker compose run --rm rf-monitor scan --freq-start 100M --freq-end 110M
```

## Operations

```bash
# Rebuild image after code changes
docker compose build --no-cache

# Restart service
docker compose restart rf-monitor

# Stop service
docker compose down

# Pull/rebuild workflow (for updates)
git pull
docker compose build
docker compose up -d
```

## Troubleshooting

- `No supported devices found in rtl_test`:
  - Replug dongle, then rerun `sudo bash docker/host-prep-rtlsdr.sh`
  - Confirm `lsusb` sees the Realtek device
  - Reboot host if module blacklisting was just applied
- `usb_open error -3` / permissions issues:
  - Ensure `/dev/bus/usb` is mapped in compose
  - Ensure host udev rules are installed and active
- `rtl_power not found` in container:
  - Rebuild image: `docker compose build --no-cache`
- No output files generated:
  - Check `RF_MONITOR_OUTPUT_DIR` and confirm volume mount exists
  - Inspect logs: `docker compose logs -f rf-monitor`

## Development and Tests

Local development/test workflow remains available:

```bash
pip install -e ".[dev]"
pytest -v
```

Deployment support in this repository is Docker-only.

## Legal Notice

This tool is intended for authorized educational, diagnostic, and research purposes only. Do not transmit or intentionally interfere with RF signals.

## License

MIT License. See [LICENSE](LICENSE).
