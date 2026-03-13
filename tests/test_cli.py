"""Tests for rf-monitor CLI, config, core, and analysis modules.

Uses pytest with Click's testing utilities and mocked subprocess calls
to avoid requiring actual RTL-SDR hardware.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from click.testing import CliRunner

from rf_monitor.cli import cli
from rf_monitor.config import (
    RFMonitorConfig,
    generate_default_config,
    load_config,
    parse_duration,
    parse_frequency,
    save_config,
)
from rf_monitor.utils import (
    compute_noise_floor,
    detect_noise_floor_elevation,
    detect_power_anomalies,
    estimate_scan_time,
    rotate_logs,
    timestamped_filename,
    validate_timing,
)


# ============================================================================
# Config Tests
# ============================================================================


class TestParseFrequency:
    def test_plain_number(self):
        assert parse_frequency("1000") == 1000

    def test_kilohertz(self):
        assert parse_frequency("10k") == 10_000
        assert parse_frequency("10K") == 10_000

    def test_megahertz(self):
        assert parse_frequency("100M") == 100_000_000

    def test_gigahertz(self):
        assert parse_frequency("2G") == 2_000_000_000
        assert parse_frequency("2.4G") == 2_400_000_000

    def test_decimal(self):
        assert parse_frequency("1.5M") == 1_500_000

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_frequency("abc")
        with pytest.raises(ValueError):
            parse_frequency("")
        with pytest.raises(ValueError):
            parse_frequency("100X")


class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("0.4s") == 0.4
        assert parse_duration("1s") == 1.0

    def test_minutes(self):
        assert parse_duration("30m") == 1800.0

    def test_hours(self):
        assert parse_duration("1h") == 3600.0

    def test_bare_number(self):
        assert parse_duration("10") == 10.0

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            parse_duration("abc")


class TestRFMonitorConfig:
    def test_defaults(self):
        cfg = RFMonitorConfig()
        assert cfg.freq_start == "100M"
        assert cfg.freq_end == "900M"
        assert cfg.gain == 50
        assert cfg.interval == 30

    def test_invalid_freq_range(self):
        with pytest.raises(Exception):
            RFMonitorConfig(freq_start="900M", freq_end="100M")

    def test_invalid_gain(self):
        with pytest.raises(Exception):
            RFMonitorConfig(gain=100)

    def test_get_freq_range_str(self):
        cfg = RFMonitorConfig(freq_start="100M", freq_end="200M", bin_size="10k")
        assert cfg.get_rtl_power_freq_range() == "100M:200M:10k"

    def test_helpers(self):
        cfg = RFMonitorConfig()
        assert cfg.get_freq_start_hz() == 100_000_000
        assert cfg.get_freq_end_hz() == 900_000_000
        assert cfg.get_integration_seconds() == 0.4
        assert cfg.get_hop_bandwidth_hz() == 2_400_000

    def test_duration_parsing(self):
        cfg = RFMonitorConfig(duration="1h")
        assert cfg.get_duration_seconds() == 3600.0
        cfg2 = RFMonitorConfig(duration="")
        assert cfg2.get_duration_seconds() is None


class TestConfigLoadSave:
    def test_save_and_load(self, tmp_path):
        cfg = RFMonitorConfig(freq_start="200M", freq_end="400M")
        path = tmp_path / "test_config.json"
        save_config(cfg, str(path))
        loaded = load_config(str(path))
        assert loaded.freq_start == "200M"
        assert loaded.freq_end == "400M"

    def test_load_with_overrides(self, tmp_path):
        cfg = RFMonitorConfig()
        path = tmp_path / "test_config.json"
        save_config(cfg, str(path))
        loaded = load_config(str(path), cli_overrides={"gain": 30})
        assert loaded.gain == 30

    def test_load_nonexistent_uses_defaults(self):
        cfg = load_config("/nonexistent/path/config.json")
        assert cfg.freq_start == "100M"

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("RF_MONITOR_GAIN", "25")
        cfg = load_config("/nonexistent/config.json")
        assert cfg.gain == 25

    def test_generate_default_config(self):
        data = generate_default_config()
        assert "freq_start" in data
        assert "// NOTICE" in data


# ============================================================================
# Utils Tests
# ============================================================================


class TestTimestampedFilename:
    def test_format(self):
        name = timestamped_filename()
        assert name.startswith("spectrum_")
        assert name.endswith(".csv")

    def test_custom_prefix(self):
        name = timestamped_filename(prefix="scan", ext="txt")
        assert name.startswith("scan_")
        assert name.endswith(".txt")


class TestLogRotation:
    def test_deletes_old_files(self, tmp_path):
        old_file = tmp_path / "old.csv"
        old_file.write_text("data")
        # Set mtime to 10 days ago
        old_mtime = os.path.getmtime(str(old_file)) - (10 * 86400)
        os.utime(str(old_file), (old_mtime, old_mtime))

        new_file = tmp_path / "new.csv"
        new_file.write_text("data")

        deleted = rotate_logs(str(tmp_path), max_age_days=7)
        assert len(deleted) == 1
        assert not old_file.exists()
        assert new_file.exists()

    def test_disabled_when_zero(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("data")
        deleted = rotate_logs(str(tmp_path), max_age_days=0)
        assert deleted == []


class TestScanTimeEstimation:
    def test_basic_estimate(self):
        # 100M span, 2.4M hop BW = ~42 hops
        est = estimate_scan_time(
            freq_start_hz=100_000_000,
            freq_end_hz=200_000_000,
            integration_seconds=0.4,
            hop_bandwidth_hz=2_400_000,
        )
        # ~42 hops * (0.4 + 0.1) = ~21s
        assert 15 < est < 30

    def test_zero_span(self):
        assert estimate_scan_time(100, 100, 0.5, 2_400_000) == 0.0


class TestTimingValidation:
    def test_ok(self):
        ok, msg = validate_timing(10.0, 30, 0.5)
        assert ok

    def test_exceeds_interval(self):
        ok, msg = validate_timing(35.0, 30, 0.5)
        assert not ok
        assert "exceeds" in msg.lower() or "overlap" in msg.lower()

    def test_exceeds_duty_cycle(self):
        ok, msg = validate_timing(20.0, 30, 0.5)
        assert not ok
        assert "duty" in msg.lower()


class TestAnomalyDetection:
    def test_detect_power_anomalies(self):
        values = np.array([-70, -60, -50, -40, -30])
        mask = detect_power_anomalies(values, -50.0)
        assert list(mask) == [False, False, True, True, True]

    def test_compute_noise_floor(self):
        values = np.array([-80, -75, -70, -60, -50, -40])
        floor = compute_noise_floor(values, percentile=10)
        assert floor < -70

    def test_empty_noise_floor(self):
        assert np.isnan(compute_noise_floor(np.array([]), 10))

    def test_noise_floor_elevation(self):
        assert detect_noise_floor_elevation(-60, -75, 10.0)
        assert not detect_noise_floor_elevation(-72, -75, 10.0)


# ============================================================================
# Core Tests
# ============================================================================


class TestBuildCommand:
    def test_command_structure(self):
        from rf_monitor.core import build_rtl_power_command

        cfg = RFMonitorConfig(freq_start="100M", freq_end="200M", gain=40)
        with patch("rf_monitor.core.find_rtl_power", return_value="/usr/bin/rtl_power"):
            cmd = build_rtl_power_command(cfg, "/tmp/out.csv")
        assert cmd[0] == "/usr/bin/rtl_power"
        assert "-f" in cmd
        assert "100M:200M:10k" in cmd
        assert "-g" in cmd
        assert "40" in cmd
        assert "-1" in cmd
        assert "/tmp/out.csv" in cmd

    def test_missing_binary_raises(self):
        from rf_monitor.core import build_rtl_power_command

        cfg = RFMonitorConfig()
        with patch("rf_monitor.core.find_rtl_power", return_value=None):
            with pytest.raises(FileNotFoundError):
                build_rtl_power_command(cfg, "/tmp/out.csv")


class TestRunSingleScan:
    def test_successful_scan(self, tmp_path):
        from rf_monitor.core import run_single_scan

        output = str(tmp_path / "scan.csv")
        cfg = RFMonitorConfig(output_dir=str(tmp_path))

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("rf_monitor.core.find_rtl_power", return_value="/usr/bin/rtl_power"), \
             patch("rf_monitor.core.check_rtl_power_available", return_value=(True, "ok")), \
             patch("subprocess.run", return_value=mock_result):
            result = run_single_scan(cfg, output_file=output)
        assert result == output

    def test_rtl_power_not_found(self, tmp_path):
        from rf_monitor.core import run_single_scan

        cfg = RFMonitorConfig(output_dir=str(tmp_path))
        with patch("rf_monitor.core.check_rtl_power_available", return_value=(False, "not found")):
            with pytest.raises(FileNotFoundError):
                run_single_scan(cfg)

    def test_rtl_power_failure(self, tmp_path):
        from rf_monitor.core import run_single_scan

        cfg = RFMonitorConfig(output_dir=str(tmp_path))
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "device not found"

        with patch("rf_monitor.core.find_rtl_power", return_value="/usr/bin/rtl_power"), \
             patch("rf_monitor.core.check_rtl_power_available", return_value=(True, "ok")), \
             patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="device not found"):
                run_single_scan(cfg)


# ============================================================================
# Analyze Tests
# ============================================================================


class TestAnalyze:
    @pytest.fixture
    def sample_csv(self, tmp_path):
        """Create a minimal rtl_power CSV file."""
        csv_path = tmp_path / "sample.csv"
        lines = []
        for i in range(5):
            # date, time, freq_low, freq_high, bin_size, num_samples, power...
            powers = ", ".join([f"{-70 + i * 5 + j}" for j in range(10)])
            lines.append(f"2025-01-01, 12:00:0{i}, 100000000, 102400000, 240000, 10, {powers}")
        csv_path.write_text("\n".join(lines))
        return str(csv_path)

    def test_load_csv(self, sample_csv):
        from rf_monitor.analyze import load_rtl_power_csv

        df = load_rtl_power_csv(sample_csv)
        assert len(df) == 5
        assert "freq_low" in df.columns
        assert "power_0" in df.columns

    def test_load_missing_file(self):
        from rf_monitor.analyze import load_rtl_power_csv

        with pytest.raises(FileNotFoundError):
            load_rtl_power_csv("/nonexistent/file.csv")

    def test_analyze_scan(self, sample_csv):
        from rf_monitor.analyze import analyze_scan, load_rtl_power_csv

        df = load_rtl_power_csv(sample_csv)
        results = analyze_scan(df, threshold_dbm=-50.0)
        assert results["status"] == "ok"
        assert "summary" in results
        assert results["summary"]["total_readings"] > 0

    def test_generate_report(self, sample_csv, tmp_path):
        from rf_monitor.analyze import generate_report

        cfg = RFMonitorConfig(alert_threshold=-50.0)
        report = generate_report(
            [sample_csv],
            config=cfg,
            output_dir=str(tmp_path / "report"),
            generate_plots=True,
        )
        assert report["status"] == "ok"
        assert report["num_files"] == 1

    def test_resolve_file_paths(self, tmp_path):
        from rf_monitor.analyze import resolve_file_paths

        f1 = tmp_path / "a.csv"
        f2 = tmp_path / "b.csv"
        f1.write_text("data")
        f2.write_text("data")
        resolved = resolve_file_paths([str(tmp_path / "*.csv")])
        assert len(resolved) == 2


# ============================================================================
# CLI Tests
# ============================================================================


class TestCLI:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "rf-monitor" in result.output.lower() or "spectrum" in result.output.lower()

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output

    def test_scan_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["scan", "--help"])
        assert result.exit_code == 0
        assert "--freq-start" in result.output

    def test_monitor_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["monitor", "--help"])
        assert result.exit_code == 0
        assert "--interval" in result.output

    def test_config_init(self, tmp_path):
        runner = CliRunner()
        output = str(tmp_path / "test_config.json")
        result = runner.invoke(cli, ["config", "init", "-o", output])
        assert result.exit_code == 0
        assert Path(output).exists()
        data = json.loads(Path(output).read_text())
        assert "freq_start" in data

    def test_config_show(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "freq_start" in result.output

    def test_config_validate(self, tmp_path):
        runner = CliRunner()
        cfg = RFMonitorConfig()
        path = tmp_path / "valid.json"
        save_config(cfg, str(path))
        result = runner.invoke(cli, ["config", "validate", str(path)])
        assert result.exit_code == 0
        assert "valid" in result.output.lower()

    def test_scan_without_rtl_power(self):
        runner = CliRunner()
        with patch("rf_monitor.core.check_rtl_power_available", return_value=(False, "not found")):
            result = runner.invoke(cli, ["scan"])
        assert result.exit_code != 0

    def test_analyze_missing_files(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["analyze", "/nonexistent/*.csv"])
        assert result.exit_code != 0

    def test_analyze_with_csv(self, tmp_path):
        runner = CliRunner()
        csv_path = tmp_path / "test.csv"
        lines = []
        for i in range(3):
            powers = ", ".join([f"{-70 + j}" for j in range(10)])
            lines.append(f"2025-01-01, 12:00:0{i}, 100000000, 102400000, 240000, 10, {powers}")
        csv_path.write_text("\n".join(lines))

        result = runner.invoke(cli, ["analyze", str(csv_path), "--no-plots"])
        assert result.exit_code == 0
        assert "Analysis Report" in result.output
