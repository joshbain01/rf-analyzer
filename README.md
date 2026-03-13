# rf-monitor

RF spectrum monitoring and jamming detection CLI tool using RTL-SDR.

**rf-monitor** wraps the `rtl_power` utility to capture periodic spectrum snapshots, detect RF interference or jamming in target bands (VHF/UHF), and support automated, configurable monitoring. Built for affordability using RTL-SDR hardware (~$25), with optimizations for deployment on Raspberry Pi—suitable for dynamic environments like airborne platforms.

## Features

- **Single scans** and **continuous monitoring** with configurable intervals
- **Jamming detection** via power threshold anomalies, noise floor elevation, and persistence analysis
- **Baseline comparison** to detect changes in the RF environment over time
- **Heatmaps and spectrum plots** generated with Matplotlib
- **Hierarchical configuration**: defaults → config file → environment variables → CLI flags
- **Raspberry Pi optimizations**: CPU temperature monitoring, duty cycle validation, timing estimates
- **Log rotation** to manage storage on constrained devices
- **Graceful shutdown** via SIGINT/SIGTERM handling
- **Extensible** via Python entry points for custom analyzers and alert handlers

## Prerequisites

### RTL-SDR Hardware & Software

1. **RTL-SDR USB dongle** (e.g., RTL-SDR Blog V3/V4, ~$25)
2. **rtl-sdr library** with the `rtl_power` binary:

```bash
# Debian/Ubuntu/Raspberry Pi OS
sudo apt update && sudo apt install rtl-sdr

# macOS (Homebrew)
brew install librtlsdr

# Verify installation
rtl_test -t
```

3. **Blacklist kernel DVB drivers** (Linux only):

```bash
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtlsdr.conf
sudo modprobe -r dvb_usb_rtl28xxu
```

### Frequency Range

- **Native RTL-SDR range**: ~500 kHz to 1.75 GHz
- Covers VHF (30-300 MHz) and UHF (300 MHz-1 GHz) bands relevant to radar warning receivers (e.g., AN/APR-39, AN/APR-48)
- **For higher bands** (S-band 2-4 GHz, X-band 8-12 GHz): use an upconverter (e.g., Ham-It-Up, ~$50) and set the `upconverter_offset` configuration

## Installation

```bash
# Clone the repository
git clone <repo-url>
cd rf-monitor

# Install with pip (creates the rf-monitor command)
pip install .

# Or install in development mode
pip install -e ".[dev]"

# Verify
rf-monitor --version
rf-monitor --help
```

### Dependencies

Listed in `requirements.txt`:
- `click` — CLI framework
- `pydantic` — Configuration validation
- `pandas` — Data loading and manipulation
- `matplotlib` — Visualization
- `numpy` — Numerical computation

## Usage

### Single Scan

```bash
# Basic scan with defaults (100-900 MHz)
rf-monitor scan

# Custom frequency range with analysis
rf-monitor scan --freq-start 100M --freq-end 200M --gain 40 --analyze

# Specify output file
rf-monitor scan -o my_scan.csv --integration 1s

# Narrow band for specific threat detection
rf-monitor scan --freq-start 400M --freq-end 500M --bin-size 1k --analyze
```

### Continuous Monitoring

```bash
# Monitor every 30 seconds (default)
rf-monitor monitor

# Custom interval with duration limit
rf-monitor monitor --interval 60 --duration 1h --freq-start 100M --freq-end 500M

# High-sensitivity monitoring with alerts
rf-monitor monitor --alert-threshold -40 --interval 30 -vv

# Auto-rotate logs older than 3 days
rf-monitor monitor --max-log-age 3

# Stop with Ctrl+C for graceful shutdown
```

#### Cron Integration

As an alternative to the built-in monitoring loop:

```cron
# Run a single scan every 5 minutes
*/5 * * * * /usr/local/bin/rf-monitor scan --analyze >> /var/log/rf-monitor.log 2>&1

# Daily analysis report
0 6 * * * /usr/local/bin/rf-monitor analyze /home/pi/logs/*.csv --output-dir /home/pi/reports/$(date +\%Y\%m\%d)
```

### Post-Scan Analysis

```bash
# Analyze a single file
rf-monitor analyze logs/spectrum_20250101_120000.csv

# Batch analysis with glob patterns
rf-monitor analyze logs/spectrum_20250101_*.csv

# Compare against baseline
rf-monitor analyze logs/*.csv --baseline baseline.csv --alert-threshold -40

# JSON output without plots
rf-monitor analyze logs/*.csv --no-plots --json-output

# Custom output directory
rf-monitor analyze logs/*.csv --output-dir ./report
```

### Configuration Management

```bash
# Generate default config file (~/.rf-monitor/config.json)
rf-monitor config init

# Generate config to custom path
rf-monitor config init -o ./my_config.json

# Validate a config file
rf-monitor config validate ./my_config.json

# Show current merged configuration
rf-monitor config show

# Use a specific config file for any command
rf-monitor --config-path ./my_config.json scan
```

### Configuration Hierarchy

Settings are resolved in this order (highest priority first):

1. **CLI flags**: `--freq-start 200M`
2. **Environment variables**: `RF_MONITOR_FREQ_START=200M`
3. **Config file**: `~/.rf-monitor/config.json` or via `--config-path`
4. **Built-in defaults**

Available environment variables:

| Variable | Config Key | Example |
|---|---|---|
| `RF_MONITOR_FREQ_START` | `freq_start` | `200M` |
| `RF_MONITOR_FREQ_END` | `freq_end` | `500M` |
| `RF_MONITOR_BIN_SIZE` | `bin_size` | `10k` |
| `RF_MONITOR_INTEGRATION_TIME` | `integration_time` | `0.4s` |
| `RF_MONITOR_GAIN` | `gain` | `40` |
| `RF_MONITOR_OUTPUT_DIR` | `output_dir` | `./logs` |
| `RF_MONITOR_INTERVAL` | `interval` | `30` |
| `RF_MONITOR_ALERT_THRESHOLD` | `alert_threshold` | `-50` |

## How It Provides Meaningful Data

### Jamming Detection

rf-monitor identifies interference through three indicators:

1. **Power threshold anomalies**: Signals exceeding the alert threshold (default -50 dBm) are flagged. In a clean spectrum, most readings should be at the noise floor (-70 to -90 dBm).

2. **Noise floor elevation**: When the overall noise floor rises significantly above a recorded baseline, it suggests broadband jamming or wideband interference.

3. **Persistence analysis**: A signal that appears at the same frequency across >50% of scan sweeps is likely intentional rather than transient, indicating a sustained jammer.

### Target Use Case

For radar warning receiver protection (e.g., VHF/UHF threats against AN/APR-39):
- Monitor 100-900 MHz to cover common radar and communication bands
- Set alert threshold to -40 to -50 dBm based on your environment's noise floor
- Capture a baseline scan in a known-clean environment for comparison
- Use batch analysis to track interference patterns during movement

## Raspberry Pi Deployment

### Containerized Deploy (Portable)

For testing or portable deployments on Pi, you can run rf-monitor in Docker.

Requirements on Pi:
- Docker Engine + Compose plugin installed
- RTL-SDR USB dongle connected

Quick start:

```bash
# On the Pi, in the rf-monitor repo directory
mkdir -p config logs

# Generate an initial config file persisted on host volume
docker compose -f docker-compose.pi.yml run --rm rf-monitor config init -o /config/config.json

# (Optional) Edit config/config.json for your frequency range and interval

# Start continuous monitoring container
docker compose -f docker-compose.pi.yml up -d --build

# Check logs
docker compose -f docker-compose.pi.yml logs -f rf-monitor

# Run a one-off scan test
docker compose -f docker-compose.pi.yml run --rm rf-monitor scan -v
```

Notes:
- The compose file uses `privileged: true` for simplest USB pass-through while testing on Pi.
- Scan CSV output is persisted to `./logs` on the host.
- Config is persisted in `./config/config.json` on the host.

Makefile shortcuts:

```bash
make docker-build
make docker-up
make docker-logs
make docker-scan
make docker-down
```

### One-Command Deploy

From your development machine, deploy directly to a Pi over SSH:

```bash
# Deploy and install (rsync + remote install.sh)
make deploy PI=pi@192.168.1.100

# Or using the script directly
bash deploy/deploy.sh pi@192.168.1.100
```

This will:
1. Sync the project to `/opt/rf-monitor/src` on the Pi
2. Install system dependencies (`rtl-sdr`, `python3-venv`, etc.)
3. Create a dedicated `rf-monitor` system user
4. Build a Python venv at `/opt/rf-monitor/venv`
5. Install udev rules for RTL-SDR USB access without root
6. Blacklist the kernel DVB driver
7. Install and enable a systemd service
8. Set up logrotate for scan data

### Prerequisites for Deploy

- SSH key auth to the Pi: `ssh-copy-id pi@192.168.1.100`
- `rsync` installed on dev machine
- Pi running Raspberry Pi OS (Debian/Ubuntu-based)

### Service Management

```bash
# From your dev machine via Makefile:
make pi-start   PI=pi@192.168.1.100
make pi-stop    PI=pi@192.168.1.100
make pi-status  PI=pi@192.168.1.100
make pi-logs    PI=pi@192.168.1.100    # live tail
make pi-scan    PI=pi@192.168.1.100    # one-off test scan

# Or directly on the Pi:
sudo systemctl start rf-monitor
sudo systemctl stop rf-monitor
sudo systemctl status rf-monitor
sudo journalctl -u rf-monitor -f
```

### Configuring the Deployment

Edit `/etc/rf-monitor/env` on the Pi to set environment variable overrides:

```bash
RF_MONITOR_FREQ_START=100M
RF_MONITOR_FREQ_END=500M
RF_MONITOR_BIN_SIZE=50k
RF_MONITOR_INTERVAL=60
RF_MONITOR_GAIN=40
RF_MONITOR_ALERT_THRESHOLD=-50
RF_MONITOR_OUTPUT_DIR=/var/log/rf-monitor/scans
```

Then restart the service: `sudo systemctl restart rf-monitor`

### File Layout on Pi

```
/opt/rf-monitor/
├── src/          # Synced source code
└── venv/         # Python virtual environment (pip install happens here)

/etc/rf-monitor/
└── env           # Environment variable overrides

/var/log/rf-monitor/
└── scans/        # Timestamped scan CSV files

/etc/systemd/system/
└── rf-monitor.service

/etc/udev/rules.d/
└── 99-rtlsdr.rules

/etc/logrotate.d/
└── rf-monitor
```

### Uninstalling

```bash
# On the Pi:
sudo bash /opt/rf-monitor/src/deploy/install.sh --uninstall
```

This stops the service, removes the venv and systemd unit, but preserves logs and config for manual cleanup.

### Updating

Re-run the deploy to sync new code and reinstall:

```bash
make deploy PI=pi@192.168.1.100
```

The install script rebuilds the venv each time. To sync code without reinstalling (e.g., for config-only changes):

```bash
make deploy-sync PI=pi@192.168.1.100
```

## Raspberry Pi Optimization

### Recommended Settings for Pi 3/4

```json
{
  "freq_start": "100M",
  "freq_end": "500M",
  "bin_size": "50k",
  "integration_time": "0.4s",
  "interval": 60,
  "duty_cycle_limit": 0.5
}
```

### Performance Tips

- **Narrow the frequency range**: Scanning 100-500 MHz instead of 100-900 MHz halves scan time
- **Increase bin size**: 50k instead of 10k reduces data points by 5x
- **Use longer intervals**: 60s instead of 30s gives the CPU time to cool
- **Monitor CPU temperature**: rf-monitor automatically warns above 70C
- **Add passive cooling**: A heatsink on the Pi's SoC helps during continuous monitoring
- **Use a powered USB hub**: RTL-SDR draws ~300mA; direct Pi USB may cause instability

### Timing Validation

rf-monitor estimates scan duration before starting and warns if:
- Scan time exceeds the monitoring interval (overlapping scans)
- Duty cycle exceeds the configured limit (default 50%)

## Extensibility

rf-monitor defines entry points for plugins:

```python
# In your plugin's setup.py
entry_points={
    "rf_monitor.analyzers": [
        "my_analyzer = my_package.analyzer:MyAnalyzer",
    ],
    "rf_monitor.alert_handlers": [
        "slack_alerts = my_package.alerts:SlackHandler",
    ],
}
```

## Project Structure

```
rf-monitor/
├── rf_monitor/              # Python package
│   ├── __init__.py
│   ├── cli.py               # Click CLI entry point
│   ├── config.py            # Pydantic config + hierarchical loading
│   ├── core.py              # rtl_power subprocess, monitoring loop
│   ├── analyze.py           # Pandas/NumPy/Matplotlib analysis
│   └── utils.py             # Timestamps, log rotation, Pi health
├── deploy/                  # Deployment artifacts
│   ├── install.sh           # Pi-side install (venv, systemd, udev)
│   ├── deploy.sh            # Dev→Pi rsync + remote install
│   ├── rf-monitor.service   # systemd unit file
│   ├── 99-rtlsdr.rules      # udev rules for USB device access
│   ├── rf-monitor.logrotate # System logrotate config
│   └── env.example          # Environment variable template
├── tests/
│   └── test_cli.py          # 55 pytest tests
├── pyproject.toml           # PEP 621 packaging
├── setup.py                 # Legacy setuptools (parallel support)
├── Makefile                 # Build, test, deploy, Pi service management
├── requirements.txt
├── config.json.example
├── README.md
└── LICENSE
```

## Testing

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
make test

# With coverage
make test-cov
```

## Troubleshooting

| Problem | Solution |
|---|---|
| `rtl_power not found` | Install rtl-sdr: `sudo apt install rtl-sdr` |
| `device not found` | Plug in RTL-SDR dongle; check `lsusb` for Realtek device |
| `permission denied` | Add user to `plugdev` group: `sudo usermod -aG plugdev $USER` |
| `kernel driver active` | Blacklist DVB driver (see Prerequisites) |
| `scan time exceeds interval` | Reduce freq range, increase bin size, or increase interval |
| `CPU temperature high` | Add cooling, reduce scan density, increase interval |

## Legal Notice

This tool is intended for **authorized educational, diagnostic, and research purposes only**.

- **Receive-only monitoring** of RF spectrum is generally permitted in most jurisdictions
- **Do not transmit** or cause intentional interference — this violates FCC Part 15 (US), the Wireless Telegraphy Act (UK), and equivalent regulations worldwide
- **Do not intercept** the content of communications you are not authorized to receive
- Consult local regulations before deploying in any operational environment
- Use in military/defense contexts must comply with applicable authorization and operational security requirements

## License

MIT License. See [LICENSE](LICENSE) for details.
