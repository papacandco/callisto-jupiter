import pytest

from callisto_jupiter.config import ConfigError, load_config


def _write(tmp_path, body: str) -> str:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return str(p)


def test_loads_from_file(tmp_path):
    path = _write(tmp_path, 'dsn = "https://ingest.example.com/srv-1?token=abc"\ninterval_seconds = 30\ndisk_path = "/data"\n')
    cfg = load_config(env={"CALLISTO_CONFIG": path})
    assert cfg.dsn == "https://ingest.example.com/srv-1?token=abc"
    assert cfg.interval_seconds == 30
    assert cfg.disk_path == "/data"
    assert cfg.timeout_seconds == 10  # default


def test_env_overrides_file(tmp_path):
    path = _write(tmp_path, 'dsn = "https://file/srv?token=x"\ninterval_seconds = 30\n')
    cfg = load_config(env={
        "CALLISTO_CONFIG": path,
        "CALLISTO_DSN": "https://env/srv?token=y",
        "CALLISTO_INTERVAL": "5",
        "CALLISTO_DISK_PATH": "/mnt",
    })
    assert cfg.dsn == "https://env/srv?token=y"
    assert cfg.interval_seconds == 5
    assert cfg.disk_path == "/mnt"


def test_defaults_when_only_dsn(tmp_path):
    cfg = load_config(env={"CALLISTO_CONFIG": str(tmp_path / "missing.toml"), "CALLISTO_DSN": "https://x/s?token=t"})
    assert cfg.interval_seconds == 60
    assert cfg.disk_path == "/"
    assert cfg.timeout_seconds == 10


def test_missing_dsn_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={"CALLISTO_CONFIG": str(tmp_path / "missing.toml")})


def test_non_integer_interval_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={"CALLISTO_DSN": "https://x/s?token=t", "CALLISTO_INTERVAL": "soon"})


def test_zero_interval_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(env={"CALLISTO_DSN": "https://x/s?token=t", "CALLISTO_INTERVAL": "0"})
