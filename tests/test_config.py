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


from callisto_jupiter.config import default_buffer_path


def test_default_buffer_path_per_os(monkeypatch):
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    monkeypatch.setattr(config_mod.sys, "platform", "linux")
    assert default_buffer_path() == "/var/lib/callisto-jupiter/buffer.json"

    monkeypatch.setattr(config_mod.sys, "platform", "darwin")
    assert default_buffer_path() == "/Library/Application Support/callisto-jupiter/buffer.json"

    monkeypatch.setattr(config_mod.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMDATA", r"C:\ProgramData")
    assert default_buffer_path() == os.path.join(r"C:\ProgramData", "callisto-jupiter", "buffer.json")


def test_default_buffer_path_honors_state_directory(monkeypatch):
    # Use a path that ISN'T the hardcoded default so the test actually proves
    # $STATE_DIRECTORY is read (a no-op implementation would fail here).
    monkeypatch.setattr(config_mod.sys, "platform", "linux")
    monkeypatch.setenv("STATE_DIRECTORY", "/run/callisto-state")
    assert default_buffer_path() == "/run/callisto-state/buffer.json"
    # systemd may pass a colon-separated list; the first entry is ours.
    monkeypatch.setenv("STATE_DIRECTORY", "/run/callisto-state:/run/other")
    assert default_buffer_path() == "/run/callisto-state/buffer.json"


def test_buffer_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    monkeypatch.setattr(config_mod.sys, "platform", "linux")
    cfg = load_config(env={
        "CALLISTO_CONFIG": str(tmp_path / "missing.toml"),
        "CALLISTO_DSN": "https://x/s",
        "CALLISTO_TOKEN": "t",
    })
    assert cfg.buffer_path == "/var/lib/callisto-jupiter/buffer.json"
    assert cfg.buffer_max_age_seconds == 3600
    assert cfg.buffer_max_samples == 10000
    assert cfg.flush_batch_size == 500


def test_buffer_settings_from_file(tmp_path):
    path = _write(tmp_path,
        'dsn = "https://x/s"\ntoken = "t"\n'
        'buffer_path = "/tmp/buf.json"\nbuffer_max_age_seconds = 120\n'
        'buffer_max_samples = 50\nflush_batch_size = 10\n')
    cfg = load_config(env={"CALLISTO_CONFIG": path})
    assert cfg.buffer_path == "/tmp/buf.json"
    assert cfg.buffer_max_age_seconds == 120
    assert cfg.buffer_max_samples == 50
    assert cfg.flush_batch_size == 10


def test_buffer_env_overrides(tmp_path):
    path = _write(tmp_path, 'dsn = "https://x/s"\ntoken = "t"\nbuffer_path = "/tmp/file.json"\n')
    cfg = load_config(env={
        "CALLISTO_CONFIG": path,
        "CALLISTO_BUFFER_PATH": "/tmp/env.json",
        "CALLISTO_BUFFER_MAX_AGE": "200",
        "CALLISTO_BUFFER_MAX_SAMPLES": "7",
        "CALLISTO_FLUSH_BATCH": "3",
    })
    assert cfg.buffer_path == "/tmp/env.json"
    assert cfg.buffer_max_age_seconds == 200
    assert cfg.buffer_max_samples == 7
    assert cfg.flush_batch_size == 3


def test_empty_buffer_path_disables_persistence(tmp_path):
    cfg = load_config(env={
        "CALLISTO_CONFIG": str(tmp_path / "missing.toml"),
        "CALLISTO_DSN": "https://x/s",
        "CALLISTO_TOKEN": "t",
        "CALLISTO_BUFFER_PATH": "",
    })
    assert cfg.buffer_path == ""


def test_invalid_buffer_max_samples_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={
            "CALLISTO_CONFIG": str(tmp_path / "missing.toml"),
            "CALLISTO_DSN": "https://x/s",
            "CALLISTO_TOKEN": "t",
            "CALLISTO_BUFFER_MAX_SAMPLES": "0",
        })
