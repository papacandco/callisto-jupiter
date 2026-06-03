import os

import pytest

import callisto_jupiter.config as config_mod
from callisto_jupiter.config import (
    ConfigError,
    default_config_path,
    default_disk_path,
    load_config,
)


def test_default_config_path_per_os(monkeypatch):
    monkeypatch.setattr(config_mod.sys, "platform", "linux")
    assert default_config_path() == "/etc/callisto-jupiter/config.toml"

    monkeypatch.setattr(config_mod.sys, "platform", "darwin")
    assert default_config_path() == "/Library/Application Support/callisto-jupiter/config.toml"

    # os.path.join uses the host separator, so build the expectation the same
    # way (on real Windows this yields backslashes).
    monkeypatch.setattr(config_mod.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")
    assert default_config_path() == os.path.join(r"C:\ProgramData", "callisto-jupiter", "config.toml")


def test_default_disk_path_per_os(monkeypatch):
    monkeypatch.setattr(config_mod.sys, "platform", "linux")
    assert default_disk_path() == "/"
    monkeypatch.setattr(config_mod.sys, "platform", "win32")
    assert default_disk_path() == "C:\\"


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return str(p)


def test_loads_from_file(tmp_path):
    path = _write(tmp_path, 'dsn = "https://ingest.example.com/srv-1"\ntoken = "abc"\ninterval_seconds = 30\ndisk_path = "/data"\n')
    cfg = load_config(env={"CALLISTO_CONFIG": path})
    assert cfg.dsn == "https://ingest.example.com/srv-1"
    assert cfg.token == "abc"
    assert cfg.interval_seconds == 30
    assert cfg.disk_path == "/data"
    assert cfg.timeout_seconds == 10  # default


def test_env_overrides_file(tmp_path):
    path = _write(tmp_path, 'dsn = "https://file/srv"\ntoken = "x"\ninterval_seconds = 30\n')
    cfg = load_config(env={
        "CALLISTO_CONFIG": path,
        "CALLISTO_DSN": "https://env/srv",
        "CALLISTO_TOKEN": "y",
        "CALLISTO_INTERVAL": "5",
        "CALLISTO_DISK_PATH": "/mnt",
    })
    assert cfg.dsn == "https://env/srv"
    assert cfg.token == "y"
    assert cfg.interval_seconds == 5
    assert cfg.disk_path == "/mnt"


def test_defaults_when_only_dsn_and_token(tmp_path):
    cfg = load_config(env={
        "CALLISTO_CONFIG": str(tmp_path / "missing.toml"),
        "CALLISTO_DSN": "https://x/s",
        "CALLISTO_TOKEN": "t",
    })
    assert cfg.interval_seconds == 60
    assert cfg.disk_path == "/"
    assert cfg.timeout_seconds == 10


def test_missing_dsn_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={"CALLISTO_CONFIG": str(tmp_path / "missing.toml"), "CALLISTO_TOKEN": "t"})


def test_missing_token_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={"CALLISTO_CONFIG": str(tmp_path / "missing.toml"), "CALLISTO_DSN": "https://x/s"})


def test_non_integer_interval_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={
            "CALLISTO_CONFIG": str(tmp_path / "missing.toml"),
            "CALLISTO_DSN": "https://x/s",
            "CALLISTO_TOKEN": "t",
            "CALLISTO_INTERVAL": "soon",
        })


def test_zero_interval_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={
            "CALLISTO_CONFIG": str(tmp_path / "missing.toml"),
            "CALLISTO_DSN": "https://x/s",
            "CALLISTO_TOKEN": "t",
            "CALLISTO_INTERVAL": "0",
        })
