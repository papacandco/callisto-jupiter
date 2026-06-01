"""Resource collectors. Each produces a normalized percent sample matching the
Callisto ingest contract: {metric_name, value, unit:"percent", collected_at}.

CPU/RAM/DISK come from psutil. GPU comes from NVIDIA's pynvml and is omitted
entirely when no GPU/driver is present. A failure collecting one metric is
logged and that sample is dropped — the others are still returned.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import psutil

log = logging.getLogger("callisto_jupiter.collectors")

UNIT_PERCENT = "percent"

RESOURCE_CPU = "cpu"
RESOURCE_RAM = "ram"
RESOURCE_DISK = "disk"
RESOURCE_GPU = "gpu"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample(metric_name: str, value: float, collected_at: str) -> dict:
    return {
        "metric_name": metric_name,
        "value": round(float(value), 2),
        "unit": UNIT_PERCENT,
        "collected_at": collected_at,
    }


def prime_cpu() -> None:
    """Prime psutil's CPU percent. The first call always returns 0.0 because it
    has no prior interval to diff against; call this once at startup so the
    first real tick is meaningful."""
    psutil.cpu_percent(interval=None)


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

    collectors = [
        (RESOURCE_CPU, lambda: psutil.cpu_percent(interval=None)),
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
