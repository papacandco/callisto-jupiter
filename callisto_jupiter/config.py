"""Configuration loading: /etc/callisto-jupiter/config.toml + env overrides.

Precedence: environment variable > config file > built-in default. The DSN is
required; everything else has a sane default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:  # Python 3.11+
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # Python 3.9 / 3.10
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG_PATH = "/etc/callisto-jupiter/config.toml"
DEFAULT_INTERVAL = 60
DEFAULT_DISK_PATH = "/"
DEFAULT_TIMEOUT = 10


class ConfigError(Exception):
    """Raised when the configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    dsn: str
    interval_seconds: int = DEFAULT_INTERVAL
    disk_path: str = DEFAULT_DISK_PATH
    timeout_seconds: int = DEFAULT_TIMEOUT


def _read_file(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _as_int(value, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{field} must be an integer, got {value!r}")


def load_config(env: dict | None = None) -> Config:
    """Build a Config from the config file and environment overrides.

    `env` defaults to os.environ; injectable for testing.
    """
    env = os.environ if env is None else env

    path = env.get("CALLISTO_CONFIG", DEFAULT_CONFIG_PATH)
    file_cfg = _read_file(path)

    dsn = env.get("CALLISTO_DSN") or file_cfg.get("dsn") or ""
    if not dsn:
        raise ConfigError(
            "No DSN configured. Set CALLISTO_DSN or `dsn` in "
            f"{path} (https://ingest.callistosignal.com/<id>?token=<token>)."
        )

    interval = env.get("CALLISTO_INTERVAL", file_cfg.get("interval_seconds", DEFAULT_INTERVAL))
    disk_path = env.get("CALLISTO_DISK_PATH") or file_cfg.get("disk_path") or DEFAULT_DISK_PATH
    timeout = env.get("CALLISTO_TIMEOUT", file_cfg.get("timeout_seconds", DEFAULT_TIMEOUT))

    interval_seconds = _as_int(interval, "interval_seconds")
    if interval_seconds < 1:
        raise ConfigError("interval_seconds must be >= 1")

    return Config(
        dsn=dsn,
        interval_seconds=interval_seconds,
        disk_path=disk_path,
        timeout_seconds=_as_int(timeout, "timeout_seconds"),
    )
