"""Resource collectors. Each produces a sample matching the Callisto ingest
contract: {metric_name, value, unit, collected_at}. Unit is "percent" for
cpu/ram/disk/gpu, "bytes_per_sec" for network rx/tx, and "count" for process
tallies.

CPU/RAM/DISK come from psutil. GPU comes from NVIDIA's pynvml and is omitted
entirely when no GPU/driver is present. A failure collecting one metric is
logged and that sample is dropped — the others are still returned.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import psutil

log = logging.getLogger("callisto_jupiter.collectors")

UNIT_PERCENT = "percent"
UNIT_BYTES_PER_SEC = "bytes_per_sec"
UNIT_COUNT = "count"

RESOURCE_CPU = "cpu"
RESOURCE_RAM = "ram"
RESOURCE_DISK = "disk"
RESOURCE_GPU = "gpu"
RESOURCE_NET_RX = "net_rx"
RESOURCE_NET_TX = "net_tx"
RESOURCE_PROC = "proc"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample(
    metric_name: str,
    value: float,
    collected_at: str,
    unit: str = UNIT_PERCENT,
    labels: dict | None = None,
) -> dict:
    sample = {
        "metric_name": metric_name,
        "value": round(float(value), 2),
        "unit": unit,
        "collected_at": collected_at,
    }
    if labels is not None:
        sample["labels"] = labels
    return sample


# Interfaces whose throughput we never report (local traffic only).
_LOOPBACK_PREFIXES = ("lo", "Loopback")


def _is_loopback(iface: str) -> bool:
    return iface.startswith(_LOOPBACK_PREFIXES)


class NetworkRateState:
    """Holds the previous net_io_counters snapshot + a monotonic timestamp so
    per-interface byte rates can be derived as deltas between scrapes. One
    instance lives for the agent's lifetime."""

    def __init__(self) -> None:
        self.prev: dict | None = None
        self.prev_t: float | None = None

    def prime(self) -> None:
        """Take a baseline snapshot so the first real scrape already has a delta
        to diff against (mirrors prime_cpu)."""
        self.prev = psutil.net_io_counters(pernic=True)
        self.prev_t = time.monotonic()


def collect_network_rates(
    state: NetworkRateState, collected_at: str, current: dict, now: float
) -> list[dict]:
    """Per-interface RX/TX byte rates (bytes/sec) from counter deltas. `current`
    is psutil.net_io_counters(pernic=True); `now` a monotonic timestamp (both
    injected so the math is testable). Returns [] until a prior snapshot exists.
    Loopback interfaces and negative deltas (counter reset / vanished iface) are
    skipped. Updates `state` in place."""
    prev, prev_t = state.prev, state.prev_t
    state.prev, state.prev_t = current, now

    if prev is None or prev_t is None:
        return []
    elapsed = now - prev_t
    if elapsed <= 0:
        return []

    samples: list[dict] = []
    for iface, counters in current.items():
        if _is_loopback(iface):
            continue
        previous = prev.get(iface)
        if previous is None:
            continue
        rx = (counters.bytes_recv - previous.bytes_recv) / elapsed
        tx = (counters.bytes_sent - previous.bytes_sent) / elapsed
        if rx >= 0:
            samples.append(_sample(RESOURCE_NET_RX, rx, collected_at,
                                   unit=UNIT_BYTES_PER_SEC, labels={"iface": iface}))
        if tx >= 0:
            samples.append(_sample(RESOURCE_NET_TX, tx, collected_at,
                                   unit=UNIT_BYTES_PER_SEC, labels={"iface": iface}))
    return samples


# Normalized process states surfaced as `proc` series; any psutil status not
# listed folds into "other" so the dashboard has a bounded, stable label set.
STATUS_OTHER = "other"
_STATUS_MAP = {
    psutil.STATUS_RUNNING: "running",
    psutil.STATUS_SLEEPING: "sleeping",
    psutil.STATUS_IDLE: "idle",
    psutil.STATUS_STOPPED: "stopped",
    psutil.STATUS_ZOMBIE: "zombie",
}


def collect_process_status_counts() -> dict[str, int]:
    """Tally running processes by normalized status. psutil.process_iter handles
    processes that vanish mid-iteration internally; the guard below is just
    belt-and-suspenders. Statuses with zero processes simply don't appear."""
    counts: dict[str, int] = {}
    try:
        for proc in psutil.process_iter(["status"]):
            raw = proc.info.get("status")
            status = _STATUS_MAP.get(raw, STATUS_OTHER)
            counts[status] = counts.get(status, 0) + 1
    except psutil.NoSuchProcess:
        pass
    return counts


def prime_cpu() -> None:
    """Prime psutil's per-CPU percent counters. The first call returns 0.0 for
    each core because it has no prior interval to diff against; call this once
    at startup so the first real tick is meaningful."""
    psutil.cpu_percent(interval=None, percpu=True)


def collect_cpu_percents() -> list[float]:
    """Per-core CPU utilization (%), one entry per logical core."""
    return psutil.cpu_percent(interval=None, percpu=True)


def collect_gpu_percent() -> float | None:
    """Max GPU utilization (%) across NVIDIA devices, or None when unavailable
    (no pynvml, no driver, no GPU)."""
    try:
        import pynvml  # noqa: PLC0415 — optional dependency, imported lazily
    except Exception:
        return None

    try:
        pynvml.nvmlInit()
    except Exception:
        return None

    try:
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return None
        usages = []
        for i in range(count):
            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
            usages.append(pynvml.nvmlDeviceGetUtilizationRates(handle).gpu)
        return float(max(usages)) if usages else None
    except Exception as exc:
        log.warning("gpu collection failed: %s", exc)
        return None
    finally:
        try:
            pynvml.nvmlShutdown()
        except Exception:
            pass


def collect_samples(disk_path: str = "/") -> list[dict]:
    """Collect cpu/ram/disk/gpu samples. GPU is omitted when unavailable; any
    single collector error is logged and skipped."""
    collected_at = _now_iso()
    samples: list[dict] = []

    # CPU: one labeled sample per logical core (labels={"cpu": "<index>"}).
    # Consumers (dashboard chart, alert evaluation) average across cores for
    # the overall percentage. All cores in a scrape share `collected_at`.
    try:
        for index, percent in enumerate(collect_cpu_percents()):
            samples.append(_sample(RESOURCE_CPU, percent, collected_at, labels={"cpu": str(index)}))
    except Exception as exc:
        log.warning("%s collection failed: %s", RESOURCE_CPU, exc)

    collectors = [
        (RESOURCE_RAM, lambda: psutil.virtual_memory().percent),
        (RESOURCE_DISK, lambda: psutil.disk_usage(disk_path).percent),
    ]
    for name, fn in collectors:
        try:
            samples.append(_sample(name, fn(), collected_at))
        except Exception as exc:
            log.warning("%s collection failed: %s", name, exc)

    gpu = collect_gpu_percent()
    if gpu is not None:
        samples.append(_sample(RESOURCE_GPU, gpu, collected_at))

    return samples
