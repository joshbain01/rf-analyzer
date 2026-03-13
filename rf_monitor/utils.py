"""Utility functions for rf-monitor.

Includes timestamping, log rotation, hardware health checks (Raspberry Pi),
and anomaly detection primitives.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Timestamping
# ---------------------------------------------------------------------------

def generate_timestamp() -> str:
    """Generate a timestamp string for filenames: YYYYMMDD_HHMMSS."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def timestamped_filename(prefix: str = "spectrum", ext: str = "csv") -> str:
    """Create a timestamped filename like 'spectrum_20250101_120000.csv'.

    Args:
        prefix: Filename prefix.
        ext: File extension (without dot).

    Returns:
        Timestamped filename string.
    """
    return f"{prefix}_{generate_timestamp()}.{ext}"


# ---------------------------------------------------------------------------
# Log Rotation
# ---------------------------------------------------------------------------

def rotate_logs(directory: str, max_age_days: int, pattern: str = "*.csv") -> List[Path]:
    """Delete files older than max_age_days in the given directory.

    Args:
        directory: Path to the directory containing log files.
        max_age_days: Maximum age in days. Files older than this are deleted.
            If 0, rotation is disabled.
        pattern: Glob pattern for files to consider.

    Returns:
        List of deleted file paths.
    """
    if max_age_days <= 0:
        return []

    log_dir = Path(directory)
    if not log_dir.is_dir():
        return []

    cutoff = time.time() - (max_age_days * 86400)
    deleted: List[Path] = []

    for filepath in log_dir.glob(pattern):
        if filepath.is_file() and filepath.stat().st_mtime < cutoff:
            try:
                filepath.unlink()
                deleted.append(filepath)
                logger.info("Rotated old log: %s", filepath)
            except OSError as exc:
                logger.warning("Failed to delete %s: %s", filepath, exc)

    if deleted:
        logger.info("Log rotation removed %d file(s) from %s.", len(deleted), directory)
    return deleted


# ---------------------------------------------------------------------------
# Hardware Checks (Raspberry Pi)
# ---------------------------------------------------------------------------

def is_raspberry_pi() -> bool:
    """Check if running on a Raspberry Pi."""
    try:
        with open("/proc/device-tree/model", "r") as f:
            model = f.read()
        return "raspberry pi" in model.lower()
    except (FileNotFoundError, PermissionError):
        return False


def get_cpu_temperature() -> Optional[float]:
    """Read CPU temperature on a Raspberry Pi via vcgencmd.

    Returns:
        Temperature in Celsius, or None if unavailable.
    """
    vcgencmd = shutil.which("vcgencmd")
    if vcgencmd is None:
        # Fallback: try thermal_zone
        thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal_path.exists():
            try:
                raw = thermal_path.read_text().strip()
                return int(raw) / 1000.0
            except (ValueError, OSError):
                return None
        return None

    try:
        result = subprocess.run(
            [vcgencmd, "measure_temp"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            # Output like: temp=42.0'C
            temp_str = result.stdout.strip().replace("temp=", "").replace("'C", "")
            return float(temp_str)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def check_hardware_health() -> List[str]:
    """Run hardware health checks and return a list of warnings.

    Returns:
        List of warning message strings. Empty if all is well.
    """
    warnings: List[str] = []

    temp = get_cpu_temperature()
    if temp is not None:
        if temp >= 80.0:
            warnings.append(
                f"CPU temperature is critically high: {temp:.1f}°C. "
                "Risk of thermal throttling. Consider adding cooling or reducing scan density."
            )
        elif temp >= 70.0:
            warnings.append(
                f"CPU temperature is elevated: {temp:.1f}°C. "
                "Monitor for throttling if running continuous scans."
            )
        else:
            logger.debug("CPU temperature: %.1f°C (OK)", temp)

    return warnings


# ---------------------------------------------------------------------------
# Execution Time Estimation
# ---------------------------------------------------------------------------

def estimate_scan_time(
    freq_start_hz: int,
    freq_end_hz: int,
    integration_seconds: float,
    hop_bandwidth_hz: int,
    overhead_per_hop: float = 0.1,
) -> float:
    """Estimate the wall-clock time for an rtl_power scan.

    The estimate is based on:
        hops = ceil((freq_end - freq_start) / hop_bandwidth)
        time = hops * (integration_time + overhead_per_hop)

    Args:
        freq_start_hz: Start frequency in Hz.
        freq_end_hz: End frequency in Hz.
        integration_seconds: Integration time per hop in seconds.
        hop_bandwidth_hz: Bandwidth per hop in Hz.
        overhead_per_hop: Estimated per-hop overhead in seconds for tuning, etc.

    Returns:
        Estimated scan time in seconds.
    """
    span = freq_end_hz - freq_start_hz
    if span <= 0 or hop_bandwidth_hz <= 0:
        return 0.0
    hops = max(1, -(-span // hop_bandwidth_hz))  # ceil division
    return hops * (integration_seconds + overhead_per_hop)


def validate_timing(
    estimated_scan_time: float,
    interval: int,
    duty_cycle_limit: float = 0.5,
) -> Tuple[bool, str]:
    """Validate that the scan time fits within the monitoring interval.

    Args:
        estimated_scan_time: Estimated scan duration in seconds.
        interval: Monitoring interval in seconds.
        duty_cycle_limit: Maximum acceptable duty cycle (0.0-1.0).

    Returns:
        Tuple of (is_ok, message). is_ok is False if duty cycle is exceeded.
    """
    if interval <= 0:
        return False, "Interval must be positive."

    duty_cycle = estimated_scan_time / interval
    if estimated_scan_time >= interval:
        return False, (
            f"Estimated scan time ({estimated_scan_time:.1f}s) exceeds interval ({interval}s). "
            "Scans will overlap. Reduce frequency range, increase bin size, "
            "decrease integration time, or increase the interval."
        )
    if duty_cycle > duty_cycle_limit:
        return False, (
            f"Duty cycle {duty_cycle:.0%} exceeds limit {duty_cycle_limit:.0%}. "
            f"Estimated scan: {estimated_scan_time:.1f}s, interval: {interval}s. "
            "This may overheat hardware. Reduce scan density or increase interval."
        )
    return True, (
        f"Timing OK. Estimated scan: {estimated_scan_time:.1f}s, "
        f"interval: {interval}s, duty cycle: {duty_cycle:.0%}."
    )


# ---------------------------------------------------------------------------
# rtl_power Binary Check
# ---------------------------------------------------------------------------

def find_rtl_power() -> Optional[str]:
    """Locate the rtl_power binary on the system PATH.

    Returns:
        Absolute path to rtl_power, or None if not found.
    """
    return shutil.which("rtl_power")


def check_rtl_power_available() -> Tuple[bool, str]:
    """Check if rtl_power is installed and accessible.

    Returns:
        Tuple of (available, message).
    """
    path = find_rtl_power()
    if path:
        return True, f"rtl_power found at: {path}"
    return False, (
        "rtl_power not found on system PATH.\n"
        "Install the rtl-sdr package:\n"
        "  Linux (Debian/Ubuntu/Raspberry Pi OS): sudo apt install rtl-sdr\n"
        "  macOS (Homebrew): brew install librtlsdr\n"
        "Ensure the RTL-SDR USB device is connected and kernel drivers are blacklisted:\n"
        "  echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/blacklist-rtlsdr.conf\n"
        "  sudo modprobe -r dvb_usb_rtl28xxu\n"
        "Then reconnect the device."
    )


# ---------------------------------------------------------------------------
# Anomaly Detection Primitives
# ---------------------------------------------------------------------------

def detect_power_anomalies(
    power_values: np.ndarray,
    threshold_dbm: float,
) -> np.ndarray:
    """Identify indices where power exceeds the given threshold.

    Args:
        power_values: 1-D NumPy array of power readings in dBm.
        threshold_dbm: Power threshold in dBm.

    Returns:
        Boolean array, True where power >= threshold.
    """
    return power_values >= threshold_dbm


def compute_noise_floor(power_values: np.ndarray, percentile: float = 10.0) -> float:
    """Estimate the noise floor as a low percentile of the power distribution.

    Args:
        power_values: 1-D NumPy array of power readings in dBm.
        percentile: Percentile to use (default 10th percentile).

    Returns:
        Estimated noise floor in dBm.
    """
    if power_values.size == 0:
        return float("nan")
    return float(np.percentile(power_values, percentile))


def detect_noise_floor_elevation(
    current_floor: float,
    baseline_floor: float,
    elevation_threshold: float = 10.0,
) -> bool:
    """Check if the current noise floor is elevated compared to baseline.

    Args:
        current_floor: Current estimated noise floor in dBm.
        baseline_floor: Baseline noise floor in dBm.
        elevation_threshold: How many dB above baseline is considered elevated.

    Returns:
        True if noise floor is elevated beyond threshold.
    """
    return (current_floor - baseline_floor) >= elevation_threshold
