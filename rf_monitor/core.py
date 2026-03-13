"""Core RTL-SDR execution, single scan, and continuous monitoring loop.

Wraps the rtl_power command-line tool via subprocess and provides
execution time estimation, graceful shutdown, and real-time alerting.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from .config import RFMonitorConfig, parse_frequency
from .utils import (
    check_hardware_health,
    check_rtl_power_available,
    detect_power_anomalies,
    estimate_scan_time,
    find_rtl_power,
    rotate_logs,
    timestamped_filename,
    validate_timing,
)

logger = logging.getLogger(__name__)

# Graceful shutdown flag
_shutdown_requested = False


def _handle_signal(signum: int, frame: Any) -> None:
    """Signal handler for graceful shutdown."""
    global _shutdown_requested
    _shutdown_requested = True
    logger.info("Shutdown signal received (signal %d). Finishing current scan...", signum)


def build_rtl_power_command(
    config: RFMonitorConfig,
    output_file: str,
    single_shot: bool = True,
) -> List[str]:
    """Construct the rtl_power command-line arguments.

    Args:
        config: Validated RFMonitorConfig.
        output_file: Path for the output CSV file.
        single_shot: If True, add the -1 flag for a single sweep.

    Returns:
        List of command-line arguments (no shell interpretation).
    """
    rtl_power_path = find_rtl_power()
    if rtl_power_path is None:
        raise FileNotFoundError("rtl_power binary not found on PATH.")

    freq_range = config.get_rtl_power_freq_range()
    cmd = [
        rtl_power_path,
        "-f", freq_range,
        "-g", str(config.gain),
        "-i", config.integration_time,
    ]

    if single_shot:
        cmd.append("-1")

    cmd.append(output_file)
    return cmd


def run_single_scan(
    config: RFMonitorConfig,
    output_file: Optional[str] = None,
    analyze_callback: Optional[Callable[[str, RFMonitorConfig], None]] = None,
) -> str:
    """Execute a single rtl_power scan.

    Args:
        config: Validated RFMonitorConfig.
        output_file: Explicit output file path. If None, auto-generate.
        analyze_callback: Optional function to call after scan with (output_path, config).

    Returns:
        Path to the generated CSV file.

    Raises:
        FileNotFoundError: If rtl_power is not installed.
        RuntimeError: If rtl_power exits with an error.
    """
    available, msg = check_rtl_power_available()
    if not available:
        raise FileNotFoundError(msg)

    # Prepare output directory and filename
    output_dir = Path(config.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if output_file is None:
        output_file = str(output_dir / timestamped_filename())
    else:
        # Ensure parent directory exists
        Path(output_file).parent.mkdir(parents=True, exist_ok=True)

    # Log execution time estimate
    est = estimate_scan_time(
        config.get_freq_start_hz(),
        config.get_freq_end_hz(),
        config.get_integration_seconds(),
        config.get_hop_bandwidth_hz(),
    )
    logger.info("Estimated scan time: %.1fs", est)

    # Hardware health check
    for warning in check_hardware_health():
        logger.warning(warning)

    # Build and run command
    cmd = build_rtl_power_command(config, output_file)
    logger.info("Running: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=max(300, est * 3),  # generous timeout
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"rtl_power timed out after {max(300, est * 3):.0f}s. "
            "The scan may be too large. Try reducing frequency range or increasing bin size."
        )

    if result.stdout:
        logger.debug("rtl_power stdout: %s", result.stdout.strip())
    if result.stderr:
        stderr = result.stderr.strip()
        # rtl_power writes informational messages to stderr
        if result.returncode != 0:
            logger.error("rtl_power stderr: %s", stderr)
        else:
            logger.debug("rtl_power stderr: %s", stderr)

    if result.returncode != 0:
        raise RuntimeError(
            f"rtl_power exited with code {result.returncode}.\n"
            f"stderr: {result.stderr.strip()}\n"
            "Troubleshooting:\n"
            "  - Ensure the RTL-SDR USB dongle is connected.\n"
            "  - Check that kernel DVB drivers are blacklisted.\n"
            "  - Try running 'rtl_test' to verify device access.\n"
            "  - If permission denied, add your user to the 'plugdev' group."
        )

    logger.info("Scan complete. Output: %s", output_file)

    # Optional post-scan analysis
    if analyze_callback is not None:
        analyze_callback(output_file, config)

    return output_file


def quick_analyze(output_file: str, config: RFMonitorConfig) -> None:
    """Perform a quick inline analysis after a scan.

    Reads the CSV, checks for power values above the alert threshold,
    and logs findings.

    Args:
        output_file: Path to the scan CSV file.
        config: Configuration with alert threshold.
    """
    try:
        import pandas as pd
    except ImportError:
        logger.warning("pandas not installed; skipping quick analysis.")
        return

    try:
        df = _load_rtl_power_csv(output_file)
    except Exception as exc:
        logger.warning("Could not load scan data for analysis: %s", exc)
        return

    if df.empty:
        logger.warning("Scan file is empty: %s", output_file)
        return

    power_cols = [c for c in df.columns if isinstance(c, (int, float)) or str(c).replace('.', '', 1).replace('-', '', 1).isdigit()]
    if not power_cols:
        # Try to identify power columns (everything after the 6th column is power data)
        all_cols = df.columns.tolist()
        if len(all_cols) > 6:
            power_cols = all_cols[6:]

    if not power_cols:
        logger.warning("No power columns found in scan data.")
        return

    power_values = df[power_cols].values.flatten()
    power_values = power_values[~np.isnan(power_values)]

    if power_values.size == 0:
        logger.warning("No numeric power data found.")
        return

    max_power = float(np.max(power_values))
    mean_power = float(np.mean(power_values))
    anomaly_count = int(np.sum(power_values >= config.alert_threshold))

    logger.info(
        "Quick analysis: max=%.1f dBm, mean=%.1f dBm, anomalies (>= %.1f dBm): %d",
        max_power, mean_power, config.alert_threshold, anomaly_count,
    )

    if anomaly_count > 0:
        logger.warning(
            "ALERT: %d readings above threshold (%.1f dBm). Possible interference detected!",
            anomaly_count, config.alert_threshold,
        )


def _load_rtl_power_csv(filepath: str) -> "pd.DataFrame":
    """Load an rtl_power CSV file into a DataFrame.

    rtl_power CSV format:
        date, time, freq_low, freq_high, bin_size, num_samples, dBm1, dBm2, ...

    Args:
        filepath: Path to the CSV file.

    Returns:
        pandas DataFrame.
    """
    import pandas as pd

    df = pd.read_csv(filepath, header=None)
    # Name the known columns
    if df.shape[1] >= 6:
        base_cols = ["date", "time", "freq_low", "freq_high", "bin_size", "num_samples"]
        power_cols = [f"power_{i}" for i in range(df.shape[1] - 6)]
        df.columns = base_cols + power_cols
    return df


def run_monitor(
    config: RFMonitorConfig,
    alert_callback: Optional[Callable[[str], None]] = None,
) -> None:
    """Run continuous monitoring with configurable intervals.

    Executes periodic scans, performs log rotation, and checks for
    anomalies. Handles SIGINT/SIGTERM for graceful shutdown.

    Args:
        config: Validated RFMonitorConfig.
        alert_callback: Optional function to call with alert messages.

    Raises:
        FileNotFoundError: If rtl_power is not available.
    """
    global _shutdown_requested
    _shutdown_requested = False

    available, msg = check_rtl_power_available()
    if not available:
        raise FileNotFoundError(msg)

    # Validate timing
    est = estimate_scan_time(
        config.get_freq_start_hz(),
        config.get_freq_end_hz(),
        config.get_integration_seconds(),
        config.get_hop_bandwidth_hz(),
    )
    timing_ok, timing_msg = validate_timing(est, config.interval, config.duty_cycle_limit)
    if not timing_ok:
        logger.error(timing_msg)
        raise ValueError(timing_msg)
    logger.info(timing_msg)

    # Register signal handlers
    original_sigint = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        # SIGTERM not available on all platforms
        original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, _handle_signal)
    except (OSError, AttributeError):
        original_sigterm = None

    # Determine duration limit
    duration_seconds = config.get_duration_seconds()
    start_time = time.monotonic()
    scan_count = 0

    logger.info(
        "Starting continuous monitoring. Interval: %ds, Duration: %s",
        config.interval,
        f"{duration_seconds:.0f}s" if duration_seconds else "indefinite",
    )
    logger.info("Press Ctrl+C to stop.")

    try:
        while not _shutdown_requested:
            # Check duration limit
            if duration_seconds and (time.monotonic() - start_time) >= duration_seconds:
                logger.info("Duration limit reached. Stopping.")
                break

            # Log rotation
            rotate_logs(config.output_dir, config.max_log_age)

            # Run scan
            scan_start = time.monotonic()
            try:
                output_file = run_single_scan(config, analyze_callback=quick_analyze)
                scan_count += 1
                logger.info("Scan #%d complete: %s", scan_count, output_file)
            except (RuntimeError, FileNotFoundError) as exc:
                logger.error("Scan failed: %s", exc)
                if alert_callback:
                    alert_callback(f"Scan failure: {exc}")

            scan_elapsed = time.monotonic() - scan_start
            sleep_time = max(0, config.interval - scan_elapsed)

            if sleep_time <= 0:
                logger.warning(
                    "Scan took %.1fs, exceeding interval %ds. Scans may overlap.",
                    scan_elapsed, config.interval,
                )

            # Sleep in small increments to allow responsive shutdown
            sleep_end = time.monotonic() + sleep_time
            while time.monotonic() < sleep_end and not _shutdown_requested:
                time.sleep(min(1.0, sleep_end - time.monotonic()))

    finally:
        # Restore original signal handlers
        signal.signal(signal.SIGINT, original_sigint)
        if original_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, original_sigterm)
            except (OSError, AttributeError):
                pass
        _shutdown_requested = False

    logger.info("Monitoring stopped. Total scans: %d", scan_count)
