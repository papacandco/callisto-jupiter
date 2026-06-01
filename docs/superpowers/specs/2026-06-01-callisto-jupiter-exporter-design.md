# callisto-jupiter metrics exporter — design

**Date:** 2026-06-01
**Scope:** A pip-installable Python daemon that collects CPU/RAM/DISK/GPU
utilization and pushes them to a Callisto server-monitoring ingest DSN.

## Goal

callisto-jupiter is the agent that runs on a monitored server, collects resource
utilization as percentages, and `POST`s them to the per-server ingest DSN minted
by callisto-app. Those samples drive the client-defined alert rules
(`CPU > 10%`, …) evaluated by callisto-scheduler.

## Ingest contract (fixed by callisto-app)

- DSN: `https://ingest.callistosignal.com/{serverId}?token={token}`
- `POST` body: `{"samples": [{"metric_name": "cpu", "value": 23.4, "unit": "percent", "collected_at": "2026-06-01T23:00:00Z"}, ...]}`
- Resource metric names: `cpu`, `ram`, `disk`, `gpu` (percent, 0–100+). Only these
  drive alerts. `gpu` is omitted when no NVIDIA GPU is present.

## Decisions (brainstorming)

- **Run model:** long-running daemon (collect → push → sleep), managed by systemd.
- **GPU:** NVIDIA via `pynvml`; auto-skip the gpu sample if no GPU/driver.
- **Config:** `/etc/callisto-jupiter/config.toml` with env overrides.
- **Deps:** `psutil`, `requests`; optional `pynvml` (GPU); `tomli` on Python <3.11.

## Layout

```
callisto_jupiter/
  config.py       # load config.toml + env overrides → Config
  collectors.py   # cpu/ram/disk (psutil), gpu (pynvml, optional)
  client.py       # parse DSN, POST {samples} with retry/backoff
  agent.py        # loop: collect → push → sleep; SIGTERM-clean
  __main__.py     # CLI entry (`callisto-jupiter`)
pyproject.toml
deploy/callisto-jupiter.service     # systemd unit
deploy/config.example.toml
tests/
README.md
```

## Components

- **config.py** — `Config` dataclass: `dsn` (required), `interval_seconds` (60),
  `disk_path` (`/`), `timeout_seconds` (10). Precedence: env > file > default.
  Env keys: `CALLISTO_DSN`, `CALLISTO_INTERVAL`, `CALLISTO_DISK_PATH`,
  `CALLISTO_TIMEOUT`. Config file path from `CALLISTO_CONFIG` (default
  `/etc/callisto-jupiter/config.toml`). Missing DSN is fatal.
- **collectors.py** — `prime_cpu()` (first `psutil.cpu_percent` is 0, so prime
  once at startup); `collect_samples(disk_path)` returns a list of
  `{metric_name, value, unit:"percent", collected_at}` (UTC ISO-8601 `Z`).
  cpu=`cpu_percent()`, ram=`virtual_memory().percent`, disk=`disk_usage(path).percent`,
  gpu=max `pynvml` utilization across devices or omitted. Per-metric errors are
  logged and that sample dropped.
- **client.py** — `IngestClient(dsn, timeout)`; `push(samples)` POSTs
  `{"samples": [...]}` to the DSN as-is (it already carries `?token=`). Retries
  with backoff; returns bool; never raises to the loop.
- **agent.py** — `run(config)`: prime cpu, then `while running: collect; push;
  sleep`. SIGTERM/SIGINT set a stop flag for clean systemd shutdown. Logs to
  stdout (journald).

## Error handling

- Missing/empty DSN → fail fast at startup (exit 1).
- Collection error (incl. GPU absent) → log + skip that sample; others still sent.
- Push failure → retried, then logged and skipped; the daemon stays up.

## Testing (pytest)

- config precedence (env over file), missing-DSN raises, defaults.
- collectors shape/units with `psutil` monkeypatched; GPU-absent path omits gpu.
- DSN handling + payload build; client retry on mocked HTTP failure; success path.

## Out of scope

- AMD/Intel GPUs, per-core/per-disk breakdowns, TLS client certs, packaging to
  PyPI/apt. Single overall percent per resource for v1.
