import http.server
import json
import logging
import os
import shutil
import socket
import socketserver
import tempfile
import threading

import pytest

import callisto_jupiter.containers as containers
from callisto_jupiter.containers import (
    ContainerState,
    DockerClient,
    collect_container_samples,
    container_samples,
    memory_percent,
)

T = "2026-09-27T20:00:00Z"


def _stats(cpu=0, system=0, usage=0, limit=0, stats=None, networks=None):
    body = {
        "cpu_stats": {"cpu_usage": {"total_usage": cpu}, "system_cpu_usage": system},
        "memory_stats": {"usage": usage, "limit": limit, "stats": stats or {}},
    }
    if networks is not None:
        body["networks"] = networks
    return body


def _by_name(samples):
    return {s["metric_name"]: s for s in samples}


# --- memory_percent ---------------------------------------------------------------


def test_memory_percent_excludes_cgroup_v2_page_cache():
    mem = {"usage": 600, "limit": 1000, "stats": {"inactive_file": 100}}
    assert memory_percent(mem) == 50.0


def test_memory_percent_excludes_cgroup_v1_page_cache():
    mem = {"usage": 600, "limit": 1000, "stats": {"total_inactive_file": 200}}
    assert memory_percent(mem) == 40.0


def test_memory_percent_ignores_cache_larger_than_usage():
    mem = {"usage": 300, "limit": 1000, "stats": {"inactive_file": 900}}
    assert memory_percent(mem) == 30.0


def test_memory_percent_none_without_limit():
    assert memory_percent({"usage": 300, "limit": 0}) is None
    assert memory_percent({}) is None


# --- container_samples: per-container math -----------------------------------------


def test_first_sighting_emits_only_memory():
    stats = _stats(cpu=1_000, system=10_000, usage=500, limit=1000,
                   networks={"eth0": {"rx_bytes": 100, "tx_bytes": 50}})

    samples, snapshot = container_samples("web", stats, None, now=100.0, collected_at=T)

    assert [s["metric_name"] for s in samples] == ["container_ram"]
    assert samples[0]["value"] == 50.0
    assert samples[0]["labels"] == {"container": "web"}
    assert samples[0]["unit"] == "percent"
    assert snapshot == (1_000, 10_000, 100, 50, 100.0)


def test_cpu_is_share_of_whole_host_and_net_is_a_rate():
    prev = (1_000, 10_000, 100, 50, 100.0)
    # 500 of the host's 2_000 ns of CPU time went to this container → 25%.
    stats = _stats(cpu=1_500, system=12_000, usage=500, limit=1000,
                   networks={"eth0": {"rx_bytes": 700, "tx_bytes": 150},
                             "eth1": {"rx_bytes": 0, "tx_bytes": 0}})

    samples, _ = container_samples("web", stats, prev, now=110.0, collected_at=T)
    by = _by_name(samples)

    assert by["container_cpu"]["value"] == 25.0
    assert by["container_cpu"]["unit"] == "percent"
    assert by["container_net_rx"]["value"] == 60.0   # (700 - 100) / 10s
    assert by["container_net_tx"]["value"] == 10.0   # (150 - 50) / 10s
    assert by["container_net_rx"]["unit"] == "bytes_per_sec"
    for s in samples:
        assert s["labels"] == {"container": "web"}
        assert s["collected_at"] == T


def test_counter_reset_after_restart_emits_no_negative_rates():
    prev = (9_000, 10_000, 5_000, 5_000, 100.0)
    stats = _stats(cpu=100, system=12_000, usage=500, limit=1000,
                   networks={"eth0": {"rx_bytes": 10, "tx_bytes": 10}})

    samples, snapshot = container_samples("web", stats, prev, now=110.0, collected_at=T)

    assert {s["metric_name"] for s in samples} == {"container_ram"}
    assert snapshot[:4] == (100, 12_000, 10, 10)


def test_host_network_mode_has_no_net_samples():
    prev = (1_000, 10_000, None, None, 100.0)
    stats = _stats(cpu=1_500, system=12_000, usage=500, limit=1000)  # no "networks" key

    samples, _ = container_samples("web", stats, prev, now=110.0, collected_at=T)

    assert {s["metric_name"] for s in samples} == {"container_cpu", "container_ram"}


# --- collect_container_samples: orchestration --------------------------------------


class FakeDocker:
    def __init__(self, listing, stats=None, list_error=None, stats_errors=None):
        self.listing = listing
        self.stats_by_id = stats or {}
        self.list_error = list_error
        self.stats_errors = stats_errors or {}
        self.stats_calls = []

    def containers(self):
        if self.list_error is not None:
            raise self.list_error
        return self.listing

    def stats(self, container_id):
        self.stats_calls.append(container_id)
        if container_id in self.stats_errors:
            raise self.stats_errors[container_id]
        return self.stats_by_id.get(container_id, _stats(usage=1, limit=100))


def _c(cid, name, state="running"):
    return {"Id": cid, "Names": ["/" + name], "State": state}


def test_counts_every_state_but_reads_stats_only_for_running():
    docker = FakeDocker([_c("a", "web"), _c("b", "db"), _c("c", "job", "exited"), _c("d", "old", "exited")])
    state = ContainerState("/unused", client=docker)

    samples = collect_container_samples(state, T, now=1.0)

    counts = {s["labels"]["state"]: s["value"] for s in samples if s["metric_name"] == "container_count"}
    assert counts == {"running": 2.0, "exited": 2.0}
    for s in samples:
        if s["metric_name"] == "container_count":
            assert s["unit"] == "count"
    assert docker.stats_calls == ["a", "b"]
    names = {s["labels"]["container"] for s in samples if s["metric_name"] == "container_ram"}
    assert names == {"web", "db"}


def test_second_cycle_emits_cpu_and_forgets_stopped_containers():
    docker = FakeDocker([_c("a", "web"), _c("b", "db")], stats={
        "a": _stats(cpu=100, system=1_000, usage=1, limit=100),
        "b": _stats(cpu=100, system=1_000, usage=1, limit=100),
    })
    state = ContainerState("/unused", client=docker)
    collect_container_samples(state, T, now=1.0)

    docker.listing = [_c("a", "web"), _c("b", "db", "exited")]
    docker.stats_by_id["a"] = _stats(cpu=200, system=2_000, usage=1, limit=100)
    samples = collect_container_samples(state, T, now=2.0)

    cpu = [s for s in samples if s["metric_name"] == "container_cpu"]
    assert [(s["labels"]["container"], s["value"]) for s in cpu] == [("web", 10.0)]
    assert set(state.prev) == {"a"}


def test_missing_socket_is_silent(caplog):
    state = ContainerState("/unused", client=FakeDocker([], list_error=FileNotFoundError(2, "No such file")))

    with caplog.at_level(logging.WARNING):
        assert collect_container_samples(state, T, now=1.0) == []

    assert caplog.records == []


def test_permission_error_is_logged_once_not_every_cycle(caplog):
    state = ContainerState("/var/run/docker.sock",
                           client=FakeDocker([], list_error=PermissionError(13, "Permission denied")))

    with caplog.at_level(logging.WARNING):
        for tick in range(3):
            assert collect_container_samples(state, T, now=float(tick)) == []

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "docker" in warnings[0].getMessage()


def test_recovery_after_an_error_logs_again_on_the_next_failure(caplog):
    docker = FakeDocker([], list_error=ConnectionRefusedError(61, "refused"))
    state = ContainerState("/var/run/docker.sock", client=docker)

    with caplog.at_level(logging.WARNING):
        collect_container_samples(state, T, now=1.0)
        docker.list_error = None
        collect_container_samples(state, T, now=2.0)
        docker.list_error = ConnectionRefusedError(61, "refused")
        collect_container_samples(state, T, now=3.0)

    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_stats_timeout_stops_the_cycle_but_keeps_earlier_containers():
    docker = FakeDocker([_c("a", "web"), _c("b", "db"), _c("c", "cache")],
                        stats_errors={"b": socket.timeout("timed out")})
    state = ContainerState("/unused", client=docker)

    samples = collect_container_samples(state, T, now=1.0)

    assert docker.stats_calls == ["a", "b"]  # "c" skipped: a hung daemon must not stall every call
    assert {s["labels"]["container"] for s in samples if s["metric_name"] == "container_ram"} == {"web"}


def test_one_failing_container_does_not_drop_the_others():
    docker = FakeDocker([_c("a", "web"), _c("b", "db")],
                        stats_errors={"a": ValueError("bad json")})
    state = ContainerState("/unused", client=docker)

    samples = collect_container_samples(state, T, now=1.0)

    assert {s["labels"]["container"] for s in samples if s["metric_name"] == "container_ram"} == {"db"}


def test_running_containers_are_capped(monkeypatch):
    monkeypatch.setattr(containers, "MAX_CONTAINERS", 2)
    docker = FakeDocker([_c(str(i), f"c{i}") for i in range(5)])
    state = ContainerState("/unused", client=docker)

    samples = collect_container_samples(state, T, now=1.0)

    assert len(docker.stats_calls) == 2
    running = [s for s in samples if s["metric_name"] == "container_count"]
    assert running[0]["value"] == 5.0  # the count still reports every container


def test_name_falls_back_to_short_id():
    docker = FakeDocker([{"Id": "0123456789abcdef", "Names": [], "State": "running"}])
    state = ContainerState("/unused", client=docker)

    samples = collect_container_samples(state, T, now=1.0)

    assert _by_name(samples)["container_ram"]["labels"] == {"container": "0123456789ab"}


def test_disabled_when_no_socket_configured():
    assert collect_container_samples(ContainerState(""), T, now=1.0) == []


# --- DockerClient over a real unix socket ------------------------------------------


@pytest.fixture
def docker_socket():
    """A throwaway HTTP server on a unix socket, standing in for dockerd. Lives in a
    short /tmp path: AF_UNIX paths are capped near 104 bytes on macOS."""
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unavailable")
    tmp = tempfile.mkdtemp(prefix="cj", dir="/tmp")
    path = os.path.join(tmp, "d.sock")
    seen = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.path)
            if self.path.startswith("/containers/json"):
                body, code = [_c("a", "web")], 200
            elif self.path.startswith("/containers/a/stats"):
                body, code = _stats(usage=5, limit=10), 200
            else:
                body, code = {"message": "no such container"}, 404
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            request, _ = super().get_request()
            return request, ("docker", 0)  # BaseHTTPRequestHandler wants a (host, port)

    server = Server(path, Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield path, seen
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_client_lists_and_reads_one_shot_stats(docker_socket):
    path, seen = docker_socket
    client = DockerClient(path, timeout=2)

    assert client.containers() == [_c("a", "web")]
    assert client.stats("a")["memory_stats"]["usage"] == 5
    assert seen[0] == "/containers/json?all=1"
    assert seen[1] == "/containers/a/stats?stream=false&one-shot=true"


def test_client_raises_on_non_200(docker_socket):
    path, _ = docker_socket

    with pytest.raises(containers.DockerError):
        DockerClient(path, timeout=2).stats("missing")


def test_collect_end_to_end_over_the_socket(docker_socket):
    path, _ = docker_socket

    samples = collect_container_samples(ContainerState(path), T, now=1.0)

    assert _by_name(samples)["container_ram"]["value"] == 50.0
    assert _by_name(samples)["container_count"]["labels"] == {"state": "running"}


def test_client_missing_socket_raises_file_not_found():
    with pytest.raises(FileNotFoundError):
        DockerClient("/tmp/definitely-not-a-docker.sock", timeout=1).containers()
