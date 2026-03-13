"""Configuration management with Pydantic validation and hierarchical overrides.

Supports loading from:
1. Default values (lowest priority)
2. Config file (~/.rf-monitor/config.json or --config-path)
3. Environment variables (RF_MONITOR_*)
4. Command-line flags (highest priority)
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

# Default config directory
DEFAULT_CONFIG_DIR = Path.home() / ".rf-monitor"
DEFAULT_CONFIG_PATH = DEFAULT_CONFIG_DIR / "config.json"

# Environment variable prefix
ENV_PREFIX = "RF_MONITOR_"

# Frequency suffix multipliers
FREQ_MULTIPLIERS = {
    "": 1,
    "k": 1_000,
    "K": 1_000,
    "M": 1_000_000,
    "G": 1_000_000_000,
}


def parse_frequency(value: str) -> int:
    """Parse a frequency string like '100M', '2.4G', '10k' into Hz (integer).

    Args:
        value: Frequency string with optional suffix (k, K, M, G).

    Returns:
        Frequency in Hz as an integer.

    Raises:
        ValueError: If the format is unrecognized.
    """
    value = value.strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([kKMG]?)$", value)
    if not match:
        raise ValueError(
            f"Invalid frequency format: '{value}'. "
            "Expected a number with optional suffix (k, K, M, G). Examples: '100M', '2.4G', '500k'."
        )
    number = float(match.group(1))
    suffix = match.group(2)
    return int(number * FREQ_MULTIPLIERS[suffix])


def format_frequency(hz: int) -> str:
    """Format a frequency in Hz to a human-readable string.

    Args:
        hz: Frequency in Hz.

    Returns:
        Human-readable string like '100M', '2.4G', '500k'.
    """
    if hz >= 1_000_000_000 and hz % 1_000_000_000 == 0:
        return f"{hz // 1_000_000_000}G"
    if hz >= 1_000_000 and hz % 1_000_000 == 0:
        return f"{hz // 1_000_000}M"
    if hz >= 1_000 and hz % 1_000 == 0:
        return f"{hz // 1_000}k"
    return str(hz)


def parse_duration(value: str) -> float:
    """Parse a duration string like '0.4s', '30m', '1h' into seconds.

    Args:
        value: Duration string with suffix (s, m, h).

    Returns:
        Duration in seconds as a float.

    Raises:
        ValueError: If the format is unrecognized.
    """
    value = value.strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([smh]?)$", value)
    if not match:
        raise ValueError(
            f"Invalid duration format: '{value}'. "
            "Expected a number with optional suffix (s, m, h). Examples: '0.4s', '30m', '1h'."
        )
    number = float(match.group(1))
    suffix = match.group(2)
    multipliers = {"": 1, "s": 1, "m": 60, "h": 3600}
    return number * multipliers[suffix]


class RFMonitorConfig(BaseModel):
    """Main configuration model for rf-monitor.

    All fields have sensible defaults for general VHF/UHF monitoring
    with an RTL-SDR on Raspberry Pi hardware.
    """

    model_config = ConfigDict(validate_assignment=True)

    # Frequency settings
    freq_start: str = Field(
        default="100M",
        description="Start frequency for scanning (e.g., '100M', '2.4G').",
    )
    freq_end: str = Field(
        default="900M",
        description="End frequency for scanning (e.g., '900M', '1.7G').",
    )
    bin_size: str = Field(
        default="10k",
        description="Frequency bin size / resolution (e.g., '10k', '1M').",
    )

    # Scan parameters
    integration_time: str = Field(
        default="0.4s",
        description="Integration time per hop (e.g., '0.4s', '1s').",
    )
    gain: int = Field(
        default=50,
        ge=0,
        le=50,
        description="RTL-SDR tuner gain in dB (0-50). Use 0 for auto-gain.",
    )

    # Output settings
    output_dir: str = Field(
        default="./logs",
        description="Directory to store scan output CSV files.",
    )

    # Monitoring settings
    interval: int = Field(
        default=30,
        ge=5,
        description="Monitoring interval in seconds between scans (minimum 5).",
    )
    max_log_age: int = Field(
        default=7,
        ge=0,
        description="Delete log files older than this many days (0 to disable).",
    )

    # Anomaly detection
    alert_threshold: float = Field(
        default=-50.0,
        description="Power threshold in dBm for anomaly alerts.",
    )

    # Hardware tuning
    hop_bandwidth: str = Field(
        default="2.4M",
        description="Estimated bandwidth per hop for execution time estimation.",
    )
    duty_cycle_limit: float = Field(
        default=0.5,
        gt=0,
        le=1.0,
        description="Maximum duty cycle (scan_time / interval) before warning.",
    )

    # Advanced settings
    upconverter_offset: str = Field(
        default="0",
        description="Upconverter LO offset frequency (e.g., '125M' for Ham-It-Up).",
    )
    baseline_file: str = Field(
        default="",
        description="Path to a baseline CSV file for comparison analysis.",
    )

    # Duration for monitor mode
    duration: str = Field(
        default="",
        description="Maximum monitoring duration (e.g., '1h', '30m'). Empty for indefinite.",
    )

    @field_validator("freq_start", "bin_size", "hop_bandwidth", "upconverter_offset")
    @classmethod
    def validate_frequency(cls, v: str) -> str:
        """Ensure frequency strings are parseable."""
        if v == "0":
            return v
        parse_frequency(v)
        return v

    @field_validator("freq_end")
    @classmethod
    def validate_freq_end(cls, v: str) -> str:
        """Ensure freq_end is a valid frequency."""
        parse_frequency(v)
        return v

    @field_validator("integration_time")
    @classmethod
    def validate_integration_time(cls, v: str) -> str:
        """Ensure integration time is parseable and positive."""
        seconds = parse_duration(v)
        if seconds <= 0:
            raise ValueError("Integration time must be positive.")
        return v

    @field_validator("duration")
    @classmethod
    def validate_duration(cls, v: str) -> str:
        """Ensure duration is parseable if non-empty."""
        if v:
            parse_duration(v)
        return v

    @model_validator(mode="after")
    def validate_freq_range(self) -> "RFMonitorConfig":
        """Ensure freq_end > freq_start."""
        start_hz = parse_frequency(self.freq_start)
        end_hz = parse_frequency(self.freq_end)
        if end_hz <= start_hz:
            raise ValueError(
                f"freq_end ({self.freq_end}) must be greater than freq_start ({self.freq_start})."
            )
        return self

    def get_freq_start_hz(self) -> int:
        return parse_frequency(self.freq_start)

    def get_freq_end_hz(self) -> int:
        return parse_frequency(self.freq_end)

    def get_bin_size_hz(self) -> int:
        return parse_frequency(self.bin_size)

    def get_integration_seconds(self) -> float:
        return parse_duration(self.integration_time)

    def get_hop_bandwidth_hz(self) -> int:
        return parse_frequency(self.hop_bandwidth)

    def get_upconverter_offset_hz(self) -> int:
        return parse_frequency(self.upconverter_offset)

    def get_duration_seconds(self) -> Optional[float]:
        if self.duration:
            return parse_duration(self.duration)
        return None

    def get_rtl_power_freq_range(self) -> str:
        """Build the rtl_power -f argument string: 'start:end:bin_size'."""
        return f"{self.freq_start}:{self.freq_end}:{self.bin_size}"


# Type annotation map for env var coercion
_FIELD_TYPES: Dict[str, type] = {
    "gain": int,
    "interval": int,
    "max_log_age": int,
    "alert_threshold": float,
    "duty_cycle_limit": float,
}


def load_config(
    config_path: Optional[str] = None,
    cli_overrides: Optional[Dict[str, Any]] = None,
) -> RFMonitorConfig:
    """Load configuration with hierarchical priority.

    Priority (highest to lowest):
    1. CLI overrides (from command-line flags)
    2. Environment variables (RF_MONITOR_*)
    3. Config file
    4. Defaults

    Args:
        config_path: Path to a JSON config file. Defaults to ~/.rf-monitor/config.json.
        cli_overrides: Dict of overrides from CLI flags (only non-None values are applied).

    Returns:
        Validated RFMonitorConfig instance.
    """
    config_data: Dict[str, Any] = {}

    # 1. Load from config file
    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    if path.exists():
        logger.info("Loading config from %s", path)
        try:
            with open(path, "r") as f:
                config_data = json.load(f)
            # Strip comment keys (keys starting with '_' or '//')
            config_data = {
                k: v for k, v in config_data.items() if not k.startswith(("_", "//"))
            }
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load config file %s: %s", path, exc)
    else:
        logger.debug("No config file found at %s, using defaults.", path)

    # 2. Apply environment variable overrides
    env_map = {
        "FREQ_START": "freq_start",
        "FREQ_END": "freq_end",
        "BIN_SIZE": "bin_size",
        "INTEGRATION_TIME": "integration_time",
        "GAIN": "gain",
        "OUTPUT_DIR": "output_dir",
        "INTERVAL": "interval",
        "MAX_LOG_AGE": "max_log_age",
        "ALERT_THRESHOLD": "alert_threshold",
        "HOP_BANDWIDTH": "hop_bandwidth",
        "DUTY_CYCLE_LIMIT": "duty_cycle_limit",
        "UPCONVERTER_OFFSET": "upconverter_offset",
        "BASELINE_FILE": "baseline_file",
        "DURATION": "duration",
    }
    for env_suffix, field_name in env_map.items():
        env_var = f"{ENV_PREFIX}{env_suffix}"
        env_val = os.environ.get(env_var)
        if env_val is not None:
            logger.debug("Override from env %s=%s", env_var, env_val)
            # Type-coerce numeric fields
            target_type = _FIELD_TYPES.get(field_name)
            if target_type is int:
                config_data[field_name] = int(env_val)
            elif target_type is float:
                config_data[field_name] = float(env_val)
            else:
                config_data[field_name] = env_val

    # 3. Apply CLI overrides (highest priority)
    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None:
                config_data[key] = value

    return RFMonitorConfig(**config_data)


def save_config(config: RFMonitorConfig, path: Optional[str] = None) -> Path:
    """Save configuration to a JSON file.

    Args:
        config: RFMonitorConfig to serialize.
        path: Destination path. Defaults to ~/.rf-monitor/config.json.

    Returns:
        Path to the saved file.
    """
    dest = Path(path) if path else DEFAULT_CONFIG_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w") as f:
        json.dump(config.model_dump(), f, indent=2)
    logger.info("Configuration saved to %s", dest)
    return dest


def generate_default_config() -> Dict[str, Any]:
    """Generate a default config dict with annotation comments.

    Returns:
        Dict suitable for writing as an annotated example config.
    """
    defaults = RFMonitorConfig()
    config = defaults.model_dump()
    # Add comment-style annotations (will be stripped on load)
    annotated: Dict[str, Any] = {
        "// NOTICE": (
            "RF COMPLIANCE: This tool is for authorized educational and diagnostic use only. "
            "Comply with all local RF regulations (e.g., FCC Part 15 in the US). "
            "Receiving signals you are not authorized to monitor may violate laws in your jurisdiction."
        ),
        "// freq_start": "Start frequency. RTL-SDR native range: 500kHz - 1.75GHz. Use upconverter for higher bands.",
        "// freq_end": "End frequency. Keep range narrow for faster scans on constrained hardware.",
        "// bin_size": "Frequency resolution per bin. Smaller = more detail but slower scans.",
        "// integration_time": "Time to dwell per hop. Longer = better sensitivity, slower scan.",
        "// gain": "Tuner gain 0-50 dB. 0 = auto gain. 40-50 typical for weak signal monitoring.",
        "// output_dir": "Directory for CSV scan output files.",
        "// interval": "Seconds between scans in monitor mode. Must exceed estimated scan time.",
        "// max_log_age": "Auto-delete logs older than N days. 0 to disable rotation.",
        "// alert_threshold": "Power in dBm above which an anomaly alert is raised.",
        "// hop_bandwidth": "Estimated RTL-SDR bandwidth per hop for timing calculations.",
        "// duty_cycle_limit": "Max scan_time/interval ratio before warning (0.0-1.0).",
        "// upconverter_offset": "LO offset if using an upconverter (e.g., '125M' for Ham-It-Up v1.3).",
        "// baseline_file": "Path to baseline CSV for comparative anomaly detection.",
        "// duration": "Max monitor duration (e.g., '1h'). Empty string for indefinite.",
    }
    annotated.update(config)
    return annotated
