"""CLI entry point for rf-monitor.

Uses Click for command group structure with subcommands:
    rf-monitor scan      - Single spectrum scan
    rf-monitor monitor   - Continuous monitoring
    rf-monitor config    - Configuration management
    rf-monitor analyze   - Post-scan analysis and visualization
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

import click

from . import __version__
from .config import (
    DEFAULT_CONFIG_PATH,
    RFMonitorConfig,
    generate_default_config,
    load_config,
    save_config,
)

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------

def setup_logging(verbose: int) -> None:
    """Configure logging based on verbosity level.

    Args:
        verbose: 0=WARNING, 1=INFO, 2+=DEBUG.
    """
    level_map = {0: logging.WARNING, 1: logging.INFO}
    level = level_map.get(verbose, logging.DEBUG)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Main CLI Group
# ---------------------------------------------------------------------------

@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog=(
        "RF COMPLIANCE NOTICE: This tool is for authorized educational and diagnostic use only. "
        "Comply with all applicable laws (e.g., FCC Part 15 in the US). "
        "Monitoring RF spectrum is generally allowed for receive-only; transmitting or "
        "willful interference is prohibited.\n\n"
        "RTL-SDR native frequency range: ~500 kHz to 1.75 GHz. For higher bands "
        "(e.g., S-band/X-band radar), use an upconverter and set --upconverter-offset."
    ),
)
@click.version_option(__version__, "-V", "--version")
@click.option(
    "-v", "--verbose", count=True, default=0,
    help="Increase verbosity (use -v for INFO, -vv for DEBUG).",
)
@click.option(
    "--config-path", type=click.Path(), default=None, envvar="RF_MONITOR_CONFIG",
    help=f"Path to config file. Default: {DEFAULT_CONFIG_PATH}",
)
@click.pass_context
def cli(ctx: click.Context, verbose: int, config_path: Optional[str]) -> None:
    """rf-monitor: RF spectrum monitoring and jamming detection using RTL-SDR.

    Wraps rtl_power to capture periodic spectrum snapshots and detect
    RF interference or jamming in target bands (e.g., VHF/UHF for
    radar warning receivers like AN/APR-39).

    \b
    Quick Start:
        rf-monitor scan --freq-start 100M --freq-end 200M
        rf-monitor monitor --interval 30 --freq-start 100M --freq-end 900M
        rf-monitor analyze logs/*.csv
        rf-monitor config init
    """
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["config_path"] = config_path


# ---------------------------------------------------------------------------
# Shared Options
# ---------------------------------------------------------------------------

def common_scan_options(f):
    """Decorator for shared scan-related options."""
    f = click.option("--freq-start", type=str, default=None,
                     help="Start frequency (e.g., 100M, 2.4G). Overrides config.")(f)
    f = click.option("--freq-end", type=str, default=None,
                     help="End frequency (e.g., 900M, 1.7G). Overrides config.")(f)
    f = click.option("--bin-size", type=str, default=None,
                     help="Bin size / resolution (e.g., 10k, 1M). Overrides config.")(f)
    f = click.option("--integration", "integration_time", type=str, default=None,
                     help="Integration time per hop (e.g., 0.4s, 1s). Overrides config.")(f)
    f = click.option("--gain", type=int, default=None,
                     help="RTL-SDR gain in dB (0-50, 0=auto). Overrides config.")(f)
    f = click.option("--output-dir", type=click.Path(), default=None,
                     help="Output directory for CSV files. Overrides config.")(f)
    return f


def build_overrides(**kwargs) -> dict:
    """Filter out None values from CLI kwargs for config overrides."""
    return {k: v for k, v in kwargs.items() if v is not None}


# ---------------------------------------------------------------------------
# scan command
# ---------------------------------------------------------------------------

@cli.command()
@common_scan_options
@click.option(
    "--output", "-o", "output_file", type=click.Path(), default=None,
    help="Explicit output file path. Default: auto-generated timestamped name.",
)
@click.option(
    "--analyze", "run_analyze", is_flag=True, default=False,
    help="Run quick anomaly analysis immediately after scan.",
)
@click.option(
    "--alert-threshold", type=float, default=None,
    help="Power threshold in dBm for anomaly detection. Overrides config.",
)
@click.pass_context
def scan(
    ctx: click.Context,
    freq_start: Optional[str],
    freq_end: Optional[str],
    bin_size: Optional[str],
    integration_time: Optional[str],
    gain: Optional[int],
    output_dir: Optional[str],
    output_file: Optional[str],
    run_analyze: bool,
    alert_threshold: Optional[float],
) -> None:
    """Perform a single spectrum scan using rtl_power.

    Captures one complete sweep across the configured frequency range
    and saves the data as a timestamped CSV file.

    \b
    Examples:
        rf-monitor scan
        rf-monitor scan --freq-start 100M --freq-end 200M --gain 40
        rf-monitor scan --freq-start 400M --freq-end 500M --analyze
        rf-monitor scan -o my_scan.csv --integration 1s
    """
    from .core import quick_analyze as _quick_analyze, run_single_scan

    overrides = build_overrides(
        freq_start=freq_start,
        freq_end=freq_end,
        bin_size=bin_size,
        integration_time=integration_time,
        gain=gain,
        output_dir=output_dir,
        alert_threshold=alert_threshold,
    )

    try:
        config = load_config(ctx.obj["config_path"], cli_overrides=overrides)
    except Exception as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)

    callback = _quick_analyze if run_analyze else None

    try:
        result_path = run_single_scan(config, output_file=output_file, analyze_callback=callback)
        click.echo(f"Scan complete: {result_path}")
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    except (RuntimeError, ValueError) as exc:
        click.echo(f"Scan failed: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# monitor command
# ---------------------------------------------------------------------------

@cli.command()
@common_scan_options
@click.option(
    "--interval", type=int, default=None,
    help="Seconds between scans (min 5). Overrides config.",
)
@click.option(
    "--duration", type=str, default=None,
    help="Maximum monitoring duration (e.g., 1h, 30m). Default: indefinite.",
)
@click.option(
    "--max-log-age", type=int, default=None,
    help="Delete log files older than N days. Overrides config.",
)
@click.option(
    "--alert-threshold", type=float, default=None,
    help="Power threshold in dBm for real-time alerts. Overrides config.",
)
@click.pass_context
def monitor(
    ctx: click.Context,
    freq_start: Optional[str],
    freq_end: Optional[str],
    bin_size: Optional[str],
    integration_time: Optional[str],
    gain: Optional[int],
    output_dir: Optional[str],
    interval: Optional[int],
    duration: Optional[str],
    max_log_age: Optional[int],
    alert_threshold: Optional[float],
) -> None:
    """Run continuous spectrum monitoring with periodic scans.

    Executes rtl_power at regular intervals, saves timestamped output,
    performs log rotation, and raises alerts when power exceeds thresholds.
    Handles Ctrl+C for graceful shutdown.

    \b
    Examples:
        rf-monitor monitor --interval 30
        rf-monitor monitor --interval 60 --duration 1h --freq-start 100M --freq-end 500M
        rf-monitor monitor --alert-threshold -40 -vv

    \b
    Cron Integration (alternative to built-in loop):
        # Run a single scan every 5 minutes via cron:
        */5 * * * * /usr/local/bin/rf-monitor scan --analyze >> /var/log/rf-monitor.log 2>&1
    """
    from .core import run_monitor

    overrides = build_overrides(
        freq_start=freq_start,
        freq_end=freq_end,
        bin_size=bin_size,
        integration_time=integration_time,
        gain=gain,
        output_dir=output_dir,
        interval=interval,
        duration=duration,
        max_log_age=max_log_age,
        alert_threshold=alert_threshold,
    )

    try:
        config = load_config(ctx.obj["config_path"], cli_overrides=overrides)
    except Exception as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)

    def alert_handler(message: str) -> None:
        click.echo(f"[ALERT] {message}", err=True)

    try:
        run_monitor(config, alert_callback=alert_handler)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(f"Timing validation failed: {exc}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nMonitoring stopped by user.")


# ---------------------------------------------------------------------------
# config command group
# ---------------------------------------------------------------------------

@cli.group()
@click.pass_context
def config(ctx: click.Context) -> None:
    """Manage rf-monitor configuration files.

    \b
    Subcommands:
        init     - Generate a default config file
        validate - Check a config file for errors
        show     - Display the current loaded configuration
    """
    pass


@config.command("init")
@click.option(
    "--output", "-o", "output_path", type=click.Path(), default=None,
    help=f"Output path for config file. Default: {DEFAULT_CONFIG_PATH}",
)
@click.option(
    "--force", is_flag=True, default=False,
    help="Overwrite existing config file without confirmation.",
)
@click.pass_context
def config_init(ctx: click.Context, output_path: Optional[str], force: bool) -> None:
    """Generate a default configuration file with annotated comments.

    \b
    Examples:
        rf-monitor config init
        rf-monitor config init -o ./my_config.json
        rf-monitor config init --force
    """
    dest = Path(output_path) if output_path else DEFAULT_CONFIG_PATH

    if dest.exists() and not force:
        click.echo(f"Config file already exists: {dest}")
        if not click.confirm("Overwrite?"):
            click.echo("Aborted.")
            return

    dest.parent.mkdir(parents=True, exist_ok=True)
    config_data = generate_default_config()
    with open(dest, "w") as f:
        json.dump(config_data, f, indent=2)

    click.echo(f"Default config written to: {dest}")
    click.echo("Edit this file to customize your monitoring parameters.")
    click.echo(
        "REMINDER: Comply with all applicable RF regulations. "
        "This tool is for authorized diagnostic and educational use."
    )


@config.command("validate")
@click.argument("config_file", type=click.Path(exists=True), required=False)
@click.pass_context
def config_validate(ctx: click.Context, config_file: Optional[str]) -> None:
    """Validate a configuration file for correctness.

    \b
    Examples:
        rf-monitor config validate
        rf-monitor config validate ./my_config.json
    """
    path = config_file or ctx.obj.get("config_path")

    try:
        cfg = load_config(path)
        click.echo("Configuration is valid.")
        click.echo(f"  Frequency range: {cfg.freq_start} - {cfg.freq_end}")
        click.echo(f"  Bin size: {cfg.bin_size}")
        click.echo(f"  Integration: {cfg.integration_time}")
        click.echo(f"  Gain: {cfg.gain} dB")
        click.echo(f"  Interval: {cfg.interval}s")
        click.echo(f"  Alert threshold: {cfg.alert_threshold} dBm")

        # Timing estimate
        from .utils import estimate_scan_time, validate_timing
        est = estimate_scan_time(
            cfg.get_freq_start_hz(),
            cfg.get_freq_end_hz(),
            cfg.get_integration_seconds(),
            cfg.get_hop_bandwidth_hz(),
        )
        ok, msg = validate_timing(est, cfg.interval, cfg.duty_cycle_limit)
        status = "OK" if ok else "WARNING"
        click.echo(f"  Timing: [{status}] {msg}")
    except Exception as exc:
        click.echo(f"Validation FAILED: {exc}", err=True)
        sys.exit(1)


@config.command("show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Display the current loaded configuration.

    Shows merged values from defaults, config file, and environment variables.

    \b
    Examples:
        rf-monitor config show
        rf-monitor --config-path ./my.json config show
    """
    try:
        cfg = load_config(ctx.obj.get("config_path"))
        click.echo(json.dumps(cfg.model_dump(), indent=2))
    except Exception as exc:
        click.echo(f"Failed to load config: {exc}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# analyze command
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("files", nargs=-1, required=True)
@click.option(
    "--output-dir", "-o", type=click.Path(), default="./analysis",
    help="Directory for analysis output (plots, report). Default: ./analysis",
)
@click.option(
    "--alert-threshold", type=float, default=None,
    help="Power threshold in dBm for anomaly detection. Overrides config.",
)
@click.option(
    "--baseline", type=click.Path(exists=True), default=None,
    help="Path to a baseline CSV for comparison analysis.",
)
@click.option(
    "--no-plots", is_flag=True, default=False,
    help="Skip generating PNG plot files.",
)
@click.option(
    "--json-output", is_flag=True, default=False,
    help="Print the full JSON report to stdout.",
)
@click.pass_context
def analyze(
    ctx: click.Context,
    files: tuple,
    output_dir: str,
    alert_threshold: Optional[float],
    baseline: Optional[str],
    no_plots: bool,
    json_output: bool,
) -> None:
    """Analyze scan CSV files for jamming and interference detection.

    Processes one or more rtl_power CSV files, detects anomalies,
    and generates summary reports with optional visualizations.

    \b
    Examples:
        rf-monitor analyze logs/spectrum_20250101_*.csv
        rf-monitor analyze scan1.csv scan2.csv --alert-threshold -40
        rf-monitor analyze logs/*.csv --baseline baseline.csv --output-dir ./report
        rf-monitor analyze logs/*.csv --no-plots --json-output
    """
    from .analyze import generate_report

    overrides = build_overrides(
        alert_threshold=alert_threshold,
        baseline_file=baseline or None,
    )

    try:
        cfg = load_config(ctx.obj.get("config_path"), cli_overrides=overrides)
    except Exception as exc:
        click.echo(f"Configuration error: {exc}", err=True)
        sys.exit(1)

    results = generate_report(
        file_paths=list(files),
        config=cfg,
        output_dir=output_dir,
        generate_plots=not no_plots,
    )

    if results.get("status") == "error":
        click.echo(f"Analysis error: {results.get('message')}", err=True)
        sys.exit(1)

    # Summary output
    summary = results.get("summary", {})
    click.echo("=== RF Spectrum Analysis Report ===")
    click.echo(f"Files analyzed: {results.get('num_files', 0)}")
    click.echo(f"Total readings: {summary.get('total_readings', 0)}")
    click.echo(f"Time steps: {summary.get('time_steps', 0)}")
    click.echo(f"Frequency bins: {summary.get('freq_bins', 0)}")
    click.echo(f"Noise floor: {summary.get('noise_floor_dbm', 'N/A'):.1f} dBm")
    click.echo(f"Max power: {summary.get('max_power_dbm', 'N/A'):.1f} dBm")
    click.echo(f"Mean power: {summary.get('mean_power_dbm', 'N/A'):.1f} dBm")
    click.echo(f"Anomaly count: {summary.get('anomaly_count', 0)} ({summary.get('anomaly_fraction', 0):.1%})")

    # Baseline comparison
    baseline_cmp = results.get("baseline_comparison")
    if baseline_cmp:
        click.echo(f"\nBaseline comparison:")
        click.echo(f"  Baseline noise floor: {baseline_cmp['baseline_noise_floor_dbm']:.1f} dBm")
        click.echo(f"  Current noise floor:  {baseline_cmp['current_noise_floor_dbm']:.1f} dBm")
        click.echo(f"  Elevation:            {baseline_cmp['elevation_db']:.1f} dB")
        if baseline_cmp["is_elevated"]:
            click.echo("  STATUS: ELEVATED - possible broadband interference")

    # Jamming indicators
    indicators = results.get("jamming_indicators", [])
    if indicators:
        click.echo(f"\n*** JAMMING INDICATORS DETECTED ***")
        for ind in indicators:
            click.echo(f"  - {ind}")
    else:
        click.echo(f"\nNo jamming indicators detected.")

    # Top anomalies
    freq_anomalies = results.get("freq_anomalies", [])
    if freq_anomalies:
        click.echo(f"\nTop anomalous frequencies:")
        for a in freq_anomalies[:10]:
            click.echo(
                f"  {a['freq_mhz']:.3f} MHz: max {a['max_power_dbm']:.1f} dBm, "
                f"mean {a['mean_power_dbm']:.1f} dBm, persistence {a['persistence']:.0%}"
            )

    # Plot paths
    plots = results.get("plots", {})
    if plots:
        click.echo(f"\nGenerated plots:")
        for name, path in plots.items():
            click.echo(f"  {name}: {path}")

    report_path = results.get("report_path")
    if report_path:
        click.echo(f"\nFull report: {report_path}")

    if json_output:
        click.echo("\n--- JSON Report ---")
        click.echo(json.dumps(results, indent=2, default=str))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for console_scripts."""
    cli(obj={})


if __name__ == "__main__":
    main()
