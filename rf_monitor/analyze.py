"""Spectrum analysis, jamming detection, and visualization.

Processes rtl_power CSV output files using Pandas, NumPy, and Matplotlib
to detect anomalies, compare against baselines, and generate reports.
"""

from __future__ import annotations

import json
import logging
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for headless/Pi use

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .config import RFMonitorConfig, parse_frequency
from .utils import compute_noise_floor, detect_noise_floor_elevation, detect_power_anomalies

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSV Loading
# ---------------------------------------------------------------------------

def load_rtl_power_csv(filepath: str) -> pd.DataFrame:
    """Load an rtl_power CSV file into a structured DataFrame.

    rtl_power CSV format (no header):
        date, time, freq_low_hz, freq_high_hz, bin_size_hz, num_samples, dBm_0, dBm_1, ...

    Args:
        filepath: Path to the CSV file.

    Returns:
        DataFrame with named columns.

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: If file is empty or unparseable.
    """
    path = Path(filepath)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {filepath}")
    if path.stat().st_size == 0:
        raise ValueError(f"File is empty: {filepath}")

    df = pd.read_csv(filepath, header=None)
    if df.shape[1] < 7:
        raise ValueError(
            f"Unexpected CSV format in {filepath}: expected at least 7 columns, got {df.shape[1]}."
        )

    base_cols = ["date", "time", "freq_low", "freq_high", "bin_size", "num_samples"]
    num_power_cols = df.shape[1] - 6
    power_cols = [f"power_{i}" for i in range(num_power_cols)]
    df.columns = base_cols + power_cols

    # Parse datetime
    df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"], format="mixed", dayfirst=False)

    return df


def load_multiple_csvs(file_paths: List[str]) -> pd.DataFrame:
    """Load and concatenate multiple rtl_power CSV files.

    Args:
        file_paths: List of CSV file paths.

    Returns:
        Combined DataFrame sorted by datetime.
    """
    frames = []
    for fp in sorted(file_paths):
        try:
            df = load_rtl_power_csv(fp)
            df["source_file"] = Path(fp).name
            frames.append(df)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Skipping %s: %s", fp, exc)

    if not frames:
        raise ValueError("No valid CSV files loaded.")

    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values("datetime", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    return combined


def resolve_file_paths(patterns: List[str]) -> List[str]:
    """Expand glob patterns and return unique file paths.

    Args:
        patterns: List of file paths or glob patterns.

    Returns:
        Deduplicated list of resolved file paths.
    """
    paths = []
    for pattern in patterns:
        expanded = glob(pattern, recursive=True)
        if expanded:
            paths.extend(expanded)
        else:
            # Treat as literal path
            paths.append(pattern)
    return list(dict.fromkeys(paths))  # preserve order, deduplicate


# ---------------------------------------------------------------------------
# Power Data Extraction
# ---------------------------------------------------------------------------

def extract_power_matrix(df: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Extract a 2-D power matrix, frequency axis, and time axis from the DataFrame.

    Each row in the rtl_power output covers freq_low to freq_high with
    num_samples bins. We reconstruct the frequency axis per row and
    build a matrix of (time_steps x frequency_bins).

    Args:
        df: DataFrame from load_rtl_power_csv.

    Returns:
        Tuple of (power_matrix, freq_axis_hz, time_axis):
            power_matrix: 2-D ndarray (rows=time, cols=freq), dBm values.
            freq_axis_hz: 1-D array of frequency bin centers in Hz.
            time_axis: 1-D array of datetime objects.
    """
    power_cols = [c for c in df.columns if c.startswith("power_")]

    # Group by (freq_low, freq_high) to handle multi-hop sweeps
    # Each unique row timestamp may have multiple rows for different frequency hops
    groups = df.groupby("datetime")

    all_times = []
    all_rows = []
    freq_axis = None

    for dt, group in groups:
        group = group.sort_values("freq_low")
        row_powers = []
        row_freqs = []

        for _, scan_row in group.iterrows():
            f_low = float(scan_row["freq_low"])
            f_high = float(scan_row["freq_high"])
            bin_sz = float(scan_row["bin_size"])
            powers = scan_row[power_cols].dropna().values.astype(float)
            n_bins = len(powers)

            if n_bins > 0 and bin_sz > 0:
                freqs = np.linspace(f_low, f_low + bin_sz * (n_bins - 1), n_bins)
                row_freqs.append(freqs)
                row_powers.append(powers)

        if row_powers:
            combined_powers = np.concatenate(row_powers)
            combined_freqs = np.concatenate(row_freqs)
            all_rows.append(combined_powers)
            all_times.append(dt)

            if freq_axis is None:
                freq_axis = combined_freqs

    if not all_rows:
        return np.array([]), np.array([]), np.array([])

    # Ensure uniform row lengths by padding/truncating to the first row's length
    n_freq = len(all_rows[0])
    uniform_rows = []
    for row in all_rows:
        if len(row) == n_freq:
            uniform_rows.append(row)
        elif len(row) > n_freq:
            uniform_rows.append(row[:n_freq])
        else:
            padded = np.full(n_freq, np.nan)
            padded[: len(row)] = row
            uniform_rows.append(padded)

    power_matrix = np.vstack(uniform_rows)
    time_axis = np.array(all_times)

    if freq_axis is None:
        freq_axis = np.array([])

    return power_matrix, freq_axis, time_axis


# ---------------------------------------------------------------------------
# Analysis Routines
# ---------------------------------------------------------------------------

def analyze_scan(
    df: pd.DataFrame,
    threshold_dbm: float = -50.0,
    baseline_df: Optional[pd.DataFrame] = None,
    noise_floor_percentile: float = 10.0,
    elevation_threshold_db: float = 10.0,
) -> Dict[str, Any]:
    """Analyze a scan DataFrame for jamming indicators.

    Detects:
    - Power spikes above threshold (potential jammers)
    - Noise floor elevation vs. baseline (broadband interference)
    - Persistent high-power signals across time

    Args:
        df: DataFrame from load_rtl_power_csv.
        threshold_dbm: Alert threshold in dBm.
        baseline_df: Optional baseline DataFrame for comparison.
        noise_floor_percentile: Percentile for noise floor estimation.
        elevation_threshold_db: dB above baseline for noise floor alert.

    Returns:
        Dict containing analysis results.
    """
    power_matrix, freq_axis, time_axis = extract_power_matrix(df)

    if power_matrix.size == 0:
        return {
            "status": "error",
            "message": "No power data extracted from scan.",
            "anomalies": [],
        }

    # Flatten all power values for global stats
    all_power = power_matrix.flatten()
    all_power = all_power[~np.isnan(all_power)]

    noise_floor = compute_noise_floor(all_power, noise_floor_percentile)
    max_power = float(np.nanmax(all_power))
    mean_power = float(np.nanmean(all_power))
    std_power = float(np.nanstd(all_power))

    # Threshold anomalies
    anomaly_mask = detect_power_anomalies(all_power, threshold_dbm)
    anomaly_count = int(np.sum(anomaly_mask))
    anomaly_fraction = anomaly_count / all_power.size if all_power.size > 0 else 0

    # Per-frequency anomaly summary
    freq_anomalies = []
    if freq_axis.size > 0 and power_matrix.shape[1] == freq_axis.size:
        for col_idx in range(power_matrix.shape[1]):
            col_data = power_matrix[:, col_idx]
            col_data = col_data[~np.isnan(col_data)]
            if col_data.size > 0:
                col_max = float(np.max(col_data))
                if col_max >= threshold_dbm:
                    freq_anomalies.append({
                        "freq_hz": float(freq_axis[col_idx]),
                        "freq_mhz": float(freq_axis[col_idx]) / 1e6,
                        "max_power_dbm": col_max,
                        "mean_power_dbm": float(np.mean(col_data)),
                        "occurrences": int(np.sum(col_data >= threshold_dbm)),
                        "persistence": float(np.sum(col_data >= threshold_dbm) / col_data.size),
                    })

    # Sort anomalies by max power descending
    freq_anomalies.sort(key=lambda x: x["max_power_dbm"], reverse=True)

    # Baseline comparison
    baseline_comparison = None
    if baseline_df is not None:
        bl_matrix, _, _ = extract_power_matrix(baseline_df)
        if bl_matrix.size > 0:
            bl_all = bl_matrix.flatten()
            bl_all = bl_all[~np.isnan(bl_all)]
            bl_floor = compute_noise_floor(bl_all, noise_floor_percentile)
            elevated = detect_noise_floor_elevation(noise_floor, bl_floor, elevation_threshold_db)
            baseline_comparison = {
                "baseline_noise_floor_dbm": bl_floor,
                "current_noise_floor_dbm": noise_floor,
                "elevation_db": noise_floor - bl_floor,
                "is_elevated": elevated,
            }

    # Jamming assessment
    jamming_indicators = []
    if anomaly_fraction > 0.1:
        jamming_indicators.append(
            f"High anomaly density: {anomaly_fraction:.1%} of readings above threshold."
        )
    if baseline_comparison and baseline_comparison["is_elevated"]:
        jamming_indicators.append(
            f"Noise floor elevated by {baseline_comparison['elevation_db']:.1f} dB over baseline."
        )
    # Check for persistent signals (>50% of time steps at any frequency)
    persistent = [a for a in freq_anomalies if a["persistence"] > 0.5]
    if persistent:
        jamming_indicators.append(
            f"{len(persistent)} frequency bin(s) show persistent elevated power (>50% of scans)."
        )

    return {
        "status": "ok",
        "summary": {
            "total_readings": int(all_power.size),
            "time_steps": int(power_matrix.shape[0]),
            "freq_bins": int(power_matrix.shape[1]) if power_matrix.ndim == 2 else 0,
            "noise_floor_dbm": noise_floor,
            "max_power_dbm": max_power,
            "mean_power_dbm": mean_power,
            "std_power_dbm": std_power,
            "threshold_dbm": threshold_dbm,
            "anomaly_count": anomaly_count,
            "anomaly_fraction": anomaly_fraction,
        },
        "freq_anomalies": freq_anomalies[:50],  # Top 50
        "baseline_comparison": baseline_comparison,
        "jamming_indicators": jamming_indicators,
        "jamming_detected": len(jamming_indicators) > 0,
    }


# ---------------------------------------------------------------------------
# Visualization
# ---------------------------------------------------------------------------

def plot_spectrum_heatmap(
    df: pd.DataFrame,
    output_path: str,
    title: str = "RF Spectrum Heatmap",
    figsize: Tuple[int, int] = (14, 6),
) -> str:
    """Generate a heatmap of power levels over time and frequency.

    Args:
        df: DataFrame from load_rtl_power_csv.
        output_path: Path to save the PNG image.
        title: Plot title.
        figsize: Figure size (width, height) in inches.

    Returns:
        Path to the saved image.
    """
    power_matrix, freq_axis, time_axis = extract_power_matrix(df)
    if power_matrix.size == 0:
        logger.warning("No data to plot.")
        return output_path

    fig, ax = plt.subplots(figsize=figsize)

    freq_mhz = freq_axis / 1e6 if freq_axis.size > 0 else np.arange(power_matrix.shape[1])

    im = ax.pcolormesh(
        freq_mhz,
        np.arange(power_matrix.shape[0]),
        power_matrix,
        shading="auto",
        cmap="inferno",
    )
    cbar = fig.colorbar(im, ax=ax, label="Power (dBm)")
    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Scan Index (Time)")
    ax.set_title(title)

    # Add time labels on Y axis if available
    if time_axis.size > 0 and time_axis.size <= 30:
        tick_labels = [str(t)[-8:] if hasattr(t, '__str__') else str(t) for t in time_axis]
        ax.set_yticks(np.arange(len(tick_labels)))
        ax.set_yticklabels(tick_labels, fontsize=7)

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info("Heatmap saved to %s", output_path)
    return output_path


def plot_power_spectrum(
    df: pd.DataFrame,
    output_path: str,
    title: str = "Power Spectrum (Mean & Max)",
    threshold_dbm: Optional[float] = None,
    figsize: Tuple[int, int] = (14, 5),
) -> str:
    """Plot average and max power vs. frequency.

    Args:
        df: DataFrame from load_rtl_power_csv.
        output_path: Path to save the PNG image.
        title: Plot title.
        threshold_dbm: Optional threshold line.
        figsize: Figure size.

    Returns:
        Path to the saved image.
    """
    power_matrix, freq_axis, _ = extract_power_matrix(df)
    if power_matrix.size == 0:
        logger.warning("No data to plot.")
        return output_path

    freq_mhz = freq_axis / 1e6 if freq_axis.size > 0 else np.arange(power_matrix.shape[1])
    mean_power = np.nanmean(power_matrix, axis=0)
    max_power = np.nanmax(power_matrix, axis=0)

    fig, ax = plt.subplots(figsize=figsize)
    ax.plot(freq_mhz, mean_power, label="Mean Power", linewidth=0.8, alpha=0.8)
    ax.fill_between(freq_mhz, mean_power, max_power, alpha=0.3, label="Max Envelope")
    ax.plot(freq_mhz, max_power, label="Max Power", linewidth=0.5, alpha=0.6, color="red")

    if threshold_dbm is not None:
        ax.axhline(y=threshold_dbm, color="orange", linestyle="--", linewidth=1, label=f"Threshold ({threshold_dbm} dBm)")

    ax.set_xlabel("Frequency (MHz)")
    ax.set_ylabel("Power (dBm)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info("Spectrum plot saved to %s", output_path)
    return output_path


def plot_power_timeline(
    df: pd.DataFrame,
    output_path: str,
    freq_band_mhz: Optional[Tuple[float, float]] = None,
    threshold_dbm: Optional[float] = None,
    title: str = "Power Over Time",
    figsize: Tuple[int, int] = (14, 5),
) -> str:
    """Plot max/mean power over time, optionally filtered to a frequency band.

    Args:
        df: DataFrame from load_rtl_power_csv.
        output_path: Path to save the PNG image.
        freq_band_mhz: Optional (start_mhz, end_mhz) to filter.
        threshold_dbm: Optional threshold line.
        title: Plot title.
        figsize: Figure size.

    Returns:
        Path to the saved image.
    """
    power_matrix, freq_axis, time_axis = extract_power_matrix(df)
    if power_matrix.size == 0:
        logger.warning("No data to plot.")
        return output_path

    # Filter to frequency band if specified
    if freq_band_mhz and freq_axis.size > 0:
        f_start_hz = freq_band_mhz[0] * 1e6
        f_end_hz = freq_band_mhz[1] * 1e6
        mask = (freq_axis >= f_start_hz) & (freq_axis <= f_end_hz)
        if mask.any():
            power_matrix = power_matrix[:, mask]
            title += f" ({freq_band_mhz[0]:.0f}-{freq_band_mhz[1]:.0f} MHz)"

    max_per_step = np.nanmax(power_matrix, axis=1)
    mean_per_step = np.nanmean(power_matrix, axis=1)

    fig, ax = plt.subplots(figsize=figsize)
    x_vals = np.arange(len(max_per_step))

    ax.plot(x_vals, mean_per_step, label="Mean Power", linewidth=1)
    ax.plot(x_vals, max_per_step, label="Max Power", linewidth=1, color="red", alpha=0.7)

    if threshold_dbm is not None:
        ax.axhline(y=threshold_dbm, color="orange", linestyle="--", linewidth=1, label=f"Threshold ({threshold_dbm} dBm)")

    ax.set_xlabel("Scan Index")
    ax.set_ylabel("Power (dBm)")
    ax.set_title(title)
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    logger.info("Timeline plot saved to %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Report Generation
# ---------------------------------------------------------------------------

def generate_report(
    file_paths: List[str],
    config: RFMonitorConfig,
    output_dir: str = "./analysis",
    generate_plots: bool = True,
) -> Dict[str, Any]:
    """Run full analysis on one or more scan files and generate a report.

    Args:
        file_paths: List of CSV file paths or glob patterns.
        config: RFMonitorConfig for thresholds and settings.
        output_dir: Directory for output files (plots, JSON report).
        generate_plots: If True, generate PNG visualizations.

    Returns:
        Report dict with analysis results.
    """
    resolved = resolve_file_paths(file_paths)
    if not resolved:
        return {"status": "error", "message": "No files matched the given patterns."}

    logger.info("Analyzing %d file(s)...", len(resolved))
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    try:
        df = load_multiple_csvs(resolved)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}

    # Load baseline if configured
    baseline_df = None
    if config.baseline_file:
        try:
            baseline_df = load_rtl_power_csv(config.baseline_file)
            logger.info("Baseline loaded from %s", config.baseline_file)
        except (FileNotFoundError, ValueError) as exc:
            logger.warning("Could not load baseline: %s", exc)

    # Run analysis
    results = analyze_scan(
        df,
        threshold_dbm=config.alert_threshold,
        baseline_df=baseline_df,
    )
    results["files_analyzed"] = resolved
    results["num_files"] = len(resolved)

    # Generate plots
    if generate_plots and results["status"] == "ok":
        try:
            heatmap_path = str(out_dir / "heatmap.png")
            plot_spectrum_heatmap(df, heatmap_path)
            results["plots"] = {"heatmap": heatmap_path}
        except Exception as exc:
            logger.warning("Failed to generate heatmap: %s", exc)
            results["plots"] = {}

        try:
            spectrum_path = str(out_dir / "spectrum.png")
            plot_power_spectrum(df, spectrum_path, threshold_dbm=config.alert_threshold)
            results["plots"]["spectrum"] = spectrum_path
        except Exception as exc:
            logger.warning("Failed to generate spectrum plot: %s", exc)

        try:
            timeline_path = str(out_dir / "timeline.png")
            plot_power_timeline(df, timeline_path, threshold_dbm=config.alert_threshold)
            results["plots"]["timeline"] = timeline_path
        except Exception as exc:
            logger.warning("Failed to generate timeline plot: %s", exc)

    # Save JSON report
    report_path = str(out_dir / "report.json")
    try:
        with open(report_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        results["report_path"] = report_path
        logger.info("Report saved to %s", report_path)
    except OSError as exc:
        logger.warning("Failed to save report: %s", exc)

    return results
