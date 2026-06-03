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


def test_sample_carries_explicit_unit_and_labels():
    s = collectors._sample("net_rx", 1234.5, "2026-06-03T10:00:00Z",
                           unit=collectors.UNIT_BYTES_PER_SEC, labels={"iface": "eth0"})
    assert s == {
        "metric_name": "net_rx",
        "value": 1234.5,
        "unit": "bytes_per_sec",
        "collected_at": "2026-06-03T10:00:00Z",
        "labels": {"iface": "eth0"},
    }


def _nic(sent, recv):
    return types.SimpleNamespace(bytes_sent=sent, bytes_recv=recv)


def test_network_rates_empty_on_first_call():
    state = collectors.NetworkRateState()
    out = collectors.collect_network_rates(
        state, "2026-06-03T10:00:00Z", {"eth0": _nic(1000, 2000)}, now=100.0)
    assert out == []  # no prior snapshot yet


def test_network_rates_computes_per_interface_bytes_per_sec():
    state = collectors.NetworkRateState()
    collectors.collect_network_rates(state, "t0", {"eth0": _nic(1000, 2000)}, now=100.0)
    out = collectors.collect_network_rates(
        state, "2026-06-03T10:00:10Z", {"eth0": _nic(1500, 4000)}, now=110.0)

    by = {(s["metric_name"], s["labels"]["iface"]): s for s in out}
    # 10s elapsed: rx delta 2000->4000 = 200/s, tx delta 1000->1500 = 50/s.
    assert by[("net_rx", "eth0")]["value"] == 200.0
    assert by[("net_rx", "eth0")]["unit"] == "bytes_per_sec"
    assert by[("net_rx", "eth0")]["labels"] == {"iface": "eth0"}
    assert by[("net_tx", "eth0")]["value"] == 50.0


def test_network_rates_skip_loopback_and_counter_reset():
    state = collectors.NetworkRateState()
    collectors.collect_network_rates(
        state, "t0", {"eth0": _nic(5000, 5000), "lo": _nic(1, 1)}, now=100.0)
    out = collectors.collect_network_rates(
        state, "t1", {"eth0": _nic(10, 10), "lo": _nic(9999, 9999)}, now=110.0)
    # eth0 counter went backwards (reboot) -> negative delta dropped; lo skipped.
    assert out == []


def _proc(status):
    return types.SimpleNamespace(info={"status": status})


def test_process_status_counts_normalizes_and_tallies(monkeypatch):
    procs = [_proc("running"), _proc("running"), _proc("sleeping"),
             _proc("zombie"), _proc("disk-sleep")]  # disk-sleep -> "other"
    monkeypatch.setattr(collectors.psutil, "process_iter", lambda attrs=None: iter(procs))

    counts = collectors.collect_process_status_counts()
    assert counts == {"running": 2, "sleeping": 1, "zombie": 1, "other": 1}


def test_process_status_counts_skips_vanished(monkeypatch):
    def boom():
        yield _proc("running")
        raise collectors.psutil.NoSuchProcess(123)
    monkeypatch.setattr(collectors.psutil, "process_iter", lambda attrs=None: boom())

    counts = collectors.collect_process_status_counts()
    assert counts == {"running": 1}  # iteration stops cleanly on NoSuchProcess
