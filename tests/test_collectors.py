import types

import callisto_jupiter.collectors as collectors


def _patch_psutil(monkeypatch, cpu=(12.0,), ram=34.5, disk=67.8):
    """Patch psutil. `cpu` is a tuple of per-core percentages; psutil.cpu_percent
    is called with percpu=True by the collector and returns that list."""
    monkeypatch.setattr(
        collectors.psutil,
        "cpu_percent",
        lambda interval=None, percpu=False: list(cpu) if percpu else (sum(cpu) / len(cpu)),
    )
    monkeypatch.setattr(collectors.psutil, "virtual_memory", lambda: types.SimpleNamespace(percent=ram))
    monkeypatch.setattr(collectors.psutil, "disk_usage", lambda path: types.SimpleNamespace(percent=disk))


def test_collect_samples_without_gpu(monkeypatch):
    _patch_psutil(monkeypatch)  # single core, 12.0
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)

    samples = collectors.collect_samples("/")
    by_name = {s["metric_name"]: s for s in samples}

    assert {s["metric_name"] for s in samples} == {"cpu", "ram", "disk"}

    cpu_samples = [s for s in samples if s["metric_name"] == "cpu"]
    assert len(cpu_samples) == 1
    assert cpu_samples[0]["value"] == 12.0
    assert cpu_samples[0]["labels"] == {"cpu": "0"}

    assert by_name["ram"]["value"] == 34.5
    assert by_name["disk"]["value"] == 67.8
    for s in samples:
        assert s["unit"] == "percent"
        assert s["collected_at"].endswith("Z")


def test_cpu_emits_one_labeled_sample_per_core(monkeypatch):
    _patch_psutil(monkeypatch, cpu=(10.0, 90.0, 50.0, 0.0))
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)

    cpu_samples = [s for s in collectors.collect_samples("/") if s["metric_name"] == "cpu"]

    assert len(cpu_samples) == 4
    assert [s["labels"] for s in cpu_samples] == [{"cpu": "0"}, {"cpu": "1"}, {"cpu": "2"}, {"cpu": "3"}]
    assert [s["value"] for s in cpu_samples] == [10.0, 90.0, 50.0, 0.0]
    # all cores in a scrape share one timestamp (so consumers can group/average)
    assert len({s["collected_at"] for s in cpu_samples}) == 1
    for s in cpu_samples:
        assert s["unit"] == "percent"


def test_ram_disk_gpu_carry_no_labels(monkeypatch):
    _patch_psutil(monkeypatch)
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: 91.0)

    by_name = {s["metric_name"]: s for s in collectors.collect_samples("/")}
    assert by_name["gpu"]["value"] == 91.0
    assert by_name["gpu"]["unit"] == "percent"
    assert "labels" not in by_name["ram"]
    assert "labels" not in by_name["disk"]
    assert "labels" not in by_name["gpu"]


def test_cpu_collection_error_is_skipped(monkeypatch):
    def boom(interval=None, percpu=False):
        raise OSError("cpu read failed")

    monkeypatch.setattr(collectors.psutil, "cpu_percent", boom)
    monkeypatch.setattr(collectors.psutil, "virtual_memory", lambda: types.SimpleNamespace(percent=34.5))
    monkeypatch.setattr(collectors.psutil, "disk_usage", lambda path: types.SimpleNamespace(percent=67.8))
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)

    names = {s["metric_name"] for s in collectors.collect_samples("/")}
    assert names == {"ram", "disk"}  # cpu dropped, others survive


def test_single_collector_error_is_skipped(monkeypatch):
    _patch_psutil(monkeypatch)
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)

    def boom(path):
        raise OSError("no such mount")

    monkeypatch.setattr(collectors.psutil, "disk_usage", boom)

    names = {s["metric_name"] for s in collectors.collect_samples("/")}
    assert names == {"cpu", "ram"}  # disk dropped, others survive


def test_gpu_absent_returns_none(monkeypatch):
    # No pynvml installed in the test env → import fails → None.
    assert collectors.collect_gpu_percent() is None


def test_values_are_rounded(monkeypatch):
    _patch_psutil(monkeypatch, cpu=(12.34567,))
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)
    cpu_samples = [s for s in collectors.collect_samples("/") if s["metric_name"] == "cpu"]
    assert cpu_samples[0]["value"] == 12.35
