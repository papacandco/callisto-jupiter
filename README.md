# callisto-jupiter

Server-monitoring metrics exporter for the Callisto platform. A small Python
daemon that collects **CPU / RAM / DISK / GPU** utilization (as percentages) and
pushes them to a per-server **ingest DSN** minted by callisto-app. Those samples
feed the client-defined alert rules (`CPU > 10%`, …) evaluated by
callisto-scheduler.

## How it fits together

```
callisto-jupiter (this agent)  ──POST samples──▶  callisto-app ingest endpoint
        on each monitored host                    https://ingest.callistosignal.com/servers/<id>
        (token in X-Callisto-Jupiter-Token)               │ stores server_monitoring_data
                                                          ▼
                                                   callisto-scheduler cron
                                                   evaluates alert rules → notify
```

Each push is `POST {"samples": [{"metric_name": "cpu", "value": 23.4, "unit": "percent", "labels": {"cpu": "0"}, "collected_at": "..."}, ...]}`.
Resource names are `cpu`, `ram`, `disk`, `gpu`. CPU is reported **per logical
core** — one `cpu` sample per core, tagged `labels: {"cpu": "<index>"}`, all
sharing one `collected_at`; consumers average across cores for the overall
percentage. `ram`/`disk`/`gpu` are single, unlabelled samples. The `gpu` sample
is omitted on hosts without an NVIDIA GPU/driver.

The agent also emits per-interface network throughput (`net_rx`/`net_tx`,
`unit: "bytes_per_sec"`, `labels: {"iface": "<name>"}`, loopback excluded) and
process-status counts (`proc`, `unit: "count"`, `labels: {"status": "<state>"}`,
states `running`/`sleeping`/`idle`/`stopped`/`zombie`/`other`). Network rates are
deltas between scrapes, so the very first scrape after start emits no `net_*`
samples.

## Install

### Quick (one command)

From the cloned repo, the bundled installer does everything below — venv, config,
service, and a verification cycle — in one shot. Pass the per-server DSN and
token from the server's page in Callisto (it also accepts `CALLISTO_DSN` /
`CALLISTO_TOKEN`, or prompts):

```bash
git clone https://github.com/papacandco/callisto-jupiter
cd callisto-jupiter
sudo ./setup.sh "https://ingest.callistosignal.com/servers/<id>" "<token>"   # Linux / macOS
```

On Windows, run the PowerShell companion from an elevated prompt:

```powershell
.\setup.ps1 "https://ingest.callistosignal.com/servers/<id>" "<token>"
```

`setup.sh` auto-detects Linux vs macOS, adds NVIDIA GPU support when `nvidia-smi`
is present, writes the config to the OS-conventional path, installs + starts the
service, and runs one collect+push cycle to confirm it works. Re-running is safe.

### Manual

Prefer the steps yourself? Install into a dedicated virtualenv at a fixed path
(robust across distros — avoids PEP 668 "externally-managed-environment" errors
and gives the service a known binary path). Requires Python 3.9+.

```bash
git clone https://github.com/papacandco/callisto-jupiter
cd callisto-jupiter
sudo python3 -m venv /opt/callisto-jupiter
sudo /opt/callisto-jupiter/bin/pip install .          # core (CPU/RAM/DISK)
sudo /opt/callisto-jupiter/bin/pip install '.[gpu]'   # add NVIDIA GPU support (pynvml)
```

`pip` installs the `callisto_jupiter` package into the venv and creates the
`callisto-jupiter` executable at `/opt/callisto-jupiter/bin/callisto-jupiter`
— that's the path the systemd unit / launchd plist run. You do not copy the
Python code anywhere by hand.

## Configure

Copy the example config and fill in the DSN and token from the server's page in
Callisto (server-monitoring → show). Put it at the OS-conventional path (or
override with `CALLISTO_CONFIG` / env vars):

| OS | Default config path |
|---|---|
| Linux | `/etc/callisto-jupiter/config.toml` |
| macOS | `/Library/Application Support/callisto-jupiter/config.toml` |
| Windows | `%PROGRAMDATA%\callisto-jupiter\config.toml` |

```bash
# Linux example
sudo mkdir -p /etc/callisto-jupiter
sudo cp deploy/config.example.toml /etc/callisto-jupiter/config.toml
sudo nano /etc/callisto-jupiter/config.toml      # set dsn = "https://ingest.callistosignal.com/servers/<id>" and token = "<token>"
```

Settings (file keys / env overrides):

| Key | Env | Default | Meaning |
|---|---|---|---|
| `dsn` | `CALLISTO_DSN` | — (required) | per-server ingest DSN |
| `token` | `CALLISTO_TOKEN` | — (required) | push credential, sent in `X-Callisto-Jupiter-Token` |
| `interval_seconds` | `CALLISTO_INTERVAL` | `60` | collect/push cadence |
| `disk_path` | `CALLISTO_DISK_PATH` | `/` (Unix), `C:\` (Windows) | filesystem reported as `disk` |
| `timeout_seconds` | `CALLISTO_TIMEOUT` | `10` | per-request HTTP timeout |
| _config file_ | `CALLISTO_CONFIG` | OS path above | full path to the config file |

## Run

Use the venv's executable (`/opt/callisto-jupiter/bin/callisto-jupiter`), or
activate the venv / put it on PATH:

```bash
/opt/callisto-jupiter/bin/callisto-jupiter            # daemon loop
/opt/callisto-jupiter/bin/callisto-jupiter --once     # one collect+push cycle (handy for testing)
```

The agent itself is the same plain process on every OS. Run it under your
platform's service manager:

### Linux — systemd

```bash
sudo cp deploy/callisto-jupiter.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now callisto-jupiter
journalctl -u callisto-jupiter -f
```

### macOS — launchd

```bash
sudo cp deploy/com.callistosignal.jupiter.plist /Library/LaunchDaemons/
sudo chown root:wheel /Library/LaunchDaemons/com.callistosignal.jupiter.plist
# Modern API (macOS 10.11+). `launchctl load` is deprecated and fails with a
# "try launchctl bootstrap" hint.
sudo launchctl bootout system/com.callistosignal.jupiter 2>/dev/null  # if already loaded (avoids "Bootstrap failed: 5")
sudo launchctl bootstrap system /Library/LaunchDaemons/com.callistosignal.jupiter.plist
sudo launchctl kickstart -k system/com.callistosignal.jupiter   # start now
# logs: /var/log/callisto-jupiter.log
# stop/remove: sudo launchctl bootout system/com.callistosignal.jupiter
```

(Adjust the binary path in the plist if the venv lives elsewhere.)

### Windows — NSSM

See [deploy/windows-nssm.md](deploy/windows-nssm.md) — installs the agent as an
auto-start Windows service via NSSM.

## Develop

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev,gpu]'
pytest
```
