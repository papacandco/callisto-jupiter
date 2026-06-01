# callisto-jupiter

Server-monitoring metrics exporter for the Callisto platform. A small Python
daemon that collects **CPU / RAM / DISK / GPU** utilization (as percentages) and
pushes them to a per-server **ingest DSN** minted by callisto-app. Those samples
feed the client-defined alert rules (`CPU > 10%`, …) evaluated by
callisto-scheduler.

## How it fits together

```
callisto-jupiter (this agent)  ──POST samples──▶  callisto-app ingest endpoint
        on each monitored host                    https://ingest.callistosignal.com/<id>?token=<token>
                                                          │ stores server_monitoring_data
                                                          ▼
                                                   callisto-scheduler cron
                                                   evaluates alert rules → notify
```

Each push is `POST {"samples": [{"metric_name": "cpu", "value": 23.4, "unit": "percent", "collected_at": "..."}, ...]}`.
Resource names are `cpu`, `ram`, `disk`, `gpu`. The `gpu` sample is omitted on
hosts without an NVIDIA GPU/driver.

## Install

```bash
pip install .            # core (CPU/RAM/DISK)
pip install '.[gpu]'     # add NVIDIA GPU support (pynvml)
```

Requires Python 3.9+.

## Configure

Copy the example config and fill in the DSN from the server's page in Callisto
(server-monitoring → show):

```bash
sudo mkdir -p /etc/callisto-jupiter
sudo cp deploy/config.example.toml /etc/callisto-jupiter/config.toml
sudo $EDITOR /etc/callisto-jupiter/config.toml   # set dsn = "https://ingest.callistosignal.com/<id>?token=<token>"
```

Settings (file keys / env overrides):

| Key | Env | Default | Meaning |
|---|---|---|---|
| `dsn` | `CALLISTO_DSN` | — (required) | per-server ingest DSN |
| `interval_seconds` | `CALLISTO_INTERVAL` | `60` | collect/push cadence |
| `disk_path` | `CALLISTO_DISK_PATH` | `/` | filesystem reported as `disk` |
| `timeout_seconds` | `CALLISTO_TIMEOUT` | `10` | per-request HTTP timeout |

## Run

```bash
callisto-jupiter            # daemon loop
callisto-jupiter --once     # one collect+push cycle (handy for testing)
```

### As a systemd service

```bash
sudo cp deploy/callisto-jupiter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now callisto-jupiter
journalctl -u callisto-jupiter -f
```

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,gpu]'
pytest
```
