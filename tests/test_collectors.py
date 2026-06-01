import types

import callisto_jupiter.collectors as collectors


def _patch_psutil(monkeypatch, cpu=12.0, ram=34.5, disk=67.8):
    monkeypatch.setattr(collectors.psutil, "cpu_percent", lambda interval=None: cpu)
    monkeypatch.setattr(collectors.psutil, "virtual_memory", lambda: types.SimpleNamespace(percent=ram))
    monkeypatch.setattr(collectors.psutil, "disk_usage", lambda path: types.SimpleNamespace(percent=disk))


def test_collect_samples_without_gpu(monkeypatch):
    _patch_psutil(monkeypatch)
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)

    samples = collectors.collect_samples("/")
    by_name = {s["metric_name"]: s for s in samples}

    assert set(by_name) == {"cpu", "ram", "disk"}
    assert by_name["cpu"]["value"] == 12.0
    assert by_name["ram"]["value"] == 34.5
    assert by_name["disk"]["value"] == 67.8
    for s in samples:
        assert s["unit"] == "percent"
        assert s["collected_at"].endswith("Z")


def test_collect_samples_includes_gpu_when_present(monkeypatch):
    _patch_psutil(monkeypatch)
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: 91.0)

    by_name = {s["metric_name"]: s for s in collectors.collect_samples("/")}
    assert by_name["gpu"]["value"] == 91.0
    assert by_name["gpu"]["unit"] == "percent"


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
    _patch_psutil(monkeypatch, cpu=12.34567)
    monkeypatch.setattr(collectors, "collect_gpu_percent", lambda: None)
    by_name = {s["metric_name"]: s for s in collectors.collect_samples("/")}
    assert by_name["cpu"]["value"] == 12.35
