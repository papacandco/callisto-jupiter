"""Docker container metrics, read from the Docker Engine API over its unix socket.

Per running container, labelled {"container": "<name>"}:
  container_cpu     percent of the whole host's CPU capacity (0–100) — the same
                    scale as `cpu`, NOT `docker stats`' per-core scale (where two
                    busy cores read 200%). Ingest clamps percentages to 0–100.
  container_ram     percent of the container's memory limit (the host's RAM when
                    it has none), page cache excluded — what `docker stats` shows
                    as MEM %.
  container_net_rx  bytes/sec received, summed over the container's interfaces
  container_net_tx  bytes/sec sent (absent under network_mode host/none)
Across all containers, labelled {"state": "<state>"}:
  container_count   containers in that state (running/exited/paused/…)

CPU and network are deltas between scrapes, so a container's first scrape emits
only container_ram. Stats are read with `one-shot=true`, which returns at once
instead of blocking ~1s per container to sample the previous CPU reading itself.

Standard library only (no docker SDK). Hosts without Docker — no socket — emit
nothing, silently. Any other failure (the agent's account isn't in the `docker`
group, the daemon is down) is logged once, not on every cycle.
"""

from __future__ import annotations

import http.client
import json
import logging
import socket

from .collectors import UNIT_BYTES_PER_SEC, UNIT_COUNT, UNIT_PERCENT, _sample

log = logging.getLogger("callisto_jupiter.containers")

CONTAINER_CPU = "container_cpu"
CONTAINER_RAM = "container_ram"
CONTAINER_NET_RX = "container_net_rx"
CONTAINER_NET_TX = "container_net_tx"
CONTAINER_COUNT = "container_count"

DEFAULT_SOCKET = "/var/run/docker.sock"

# Per Docker API request. Kept well under the collect interval: a daemon that hangs
# on one container ends the scrape (see collect_container_samples), not the agent.
REQUEST_TIMEOUT = 5.0

# Running containers read per scrape; bounds both the scrape time and the payload.
# container_count still reports every container.
MAX_CONTAINERS = 100


class DockerError(Exception):
    """The Docker API answered with something other than HTTP 200."""


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._socket_path)
        except BaseException:
            sock.close()
            raise
        self.sock = sock


class DockerClient:
    """Read-only Docker Engine API client: GET + JSON, one connection per call."""

    def __init__(self, socket_path: str, timeout: float = REQUEST_TIMEOUT) -> None:
        self.socket_path = socket_path
        self.timeout = timeout

    def _get(self, path: str):
        conn = _UnixHTTPConnection(self.socket_path, self.timeout)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            body = response.read()
            if response.status != 200:
                raise DockerError(f"GET {path} returned HTTP {response.status}")
            return json.loads(body)
        finally:
            conn.close()

    def containers(self) -> list[dict]:
        return self._get("/containers/json?all=1")

    def stats(self, container_id: str) -> dict:
        return self._get(f"/containers/{container_id}/stats?stream=false&one-shot=true")


class ContainerState:
    """Per-container counters from the previous scrape, keyed by container id:
    (cpu_total_ns, host_cpu_total_ns, rx_bytes, tx_bytes, monotonic_t). One
    instance lives for the agent's lifetime. An empty socket path disables it."""

    def __init__(self, socket_path: str, client=None) -> None:
        self.socket_path = socket_path
        if client is None and socket_path and hasattr(socket, "AF_UNIX"):
            client = DockerClient(socket_path)
        self.client = client
        self.prev: dict[str, tuple] = {}
        self._reported_error: str | None = None

    def prime(self, now: float) -> None:
        """Take a baseline so the first real scrape already has CPU/network
        deltas (mirrors NetworkRateState.prime)."""
        collect_container_samples(self, "", now)

    def _warn_once(self, message: str) -> None:
        if message != self._reported_error:
            log.warning(message)
            self._reported_error = message


def memory_percent(memory_stats: dict) -> float | None:
    """Used memory as a percent of the limit, page cache excluded, the way the
    docker CLI computes MEM %: cgroup v1 reports the cache as
    `total_inactive_file`, cgroup v2 as `inactive_file`."""
    usage = memory_stats.get("usage")
    limit = memory_stats.get("limit")
    if usage is None or not limit:
        return None
    stats = memory_stats.get("stats") or {}
    cache = stats.get("total_inactive_file", stats.get("inactive_file", 0)) or 0
    used = usage - cache if cache < usage else usage
    return used / limit * 100


def _network_totals(stats: dict) -> tuple[int | None, int | None]:
    networks = stats.get("networks")
    if not networks:
        return None, None
    rx = sum(int(n.get("rx_bytes", 0)) for n in networks.values())
    tx = sum(int(n.get("tx_bytes", 0)) for n in networks.values())
    return rx, tx


def container_samples(
    name: str, stats: dict, prev: tuple | None, now: float, collected_at: str
) -> tuple[list[dict], tuple]:
    """Samples for one container from its stats document, plus the snapshot to
    diff against next scrape. `prev` is the previous snapshot or None on first
    sighting; `now` a monotonic timestamp (injected so the math is testable).
    Negative deltas — the container restarted and its counters reset — are
    skipped rather than reported."""
    labels = {"container": name}
    samples: list[dict] = []

    cpu_stats = stats.get("cpu_stats") or {}
    cpu_total = (cpu_stats.get("cpu_usage") or {}).get("total_usage")
    host_total = cpu_stats.get("system_cpu_usage")
    rx, tx = _network_totals(stats)

    if prev is not None:
        prev_cpu, prev_host, prev_rx, prev_tx, prev_t = prev
        if None not in (cpu_total, host_total, prev_cpu, prev_host):
            cpu_delta = cpu_total - prev_cpu
            host_delta = host_total - prev_host
            # system_cpu_usage sums every core, so this is a share of the whole host.
            if host_delta > 0 and cpu_delta >= 0:
                samples.append(_sample(CONTAINER_CPU, cpu_delta / host_delta * 100,
                                       collected_at, unit=UNIT_PERCENT, labels=labels))
        elapsed = now - prev_t
        if elapsed > 0:
            for metric, current, previous in ((CONTAINER_NET_RX, rx, prev_rx),
                                              (CONTAINER_NET_TX, tx, prev_tx)):
                if current is None or previous is None:
                    continue
                rate = (current - previous) / elapsed
                if rate >= 0:
                    samples.append(_sample(metric, rate, collected_at,
                                           unit=UNIT_BYTES_PER_SEC, labels=labels))

    ram = memory_percent(stats.get("memory_stats") or {})
    if ram is not None:
        samples.append(_sample(CONTAINER_RAM, ram, collected_at, unit=UNIT_PERCENT, labels=labels))

    return samples, (cpu_total, host_total, rx, tx, now)


def _container_name(container: dict) -> str:
    names = container.get("Names") or []
    if names:
        return str(names[0]).lstrip("/")
    return str(container.get("Id", ""))[:12]


def collect_container_samples(state: ContainerState, collected_at: str, now: float) -> list[dict]:
    """One scrape: container_count per state, then per-container samples for up
    to MAX_CONTAINERS running containers. Never raises; updates `state`."""
    if state.client is None:
        return []

    try:
        listing = state.client.containers()
    except FileNotFoundError:
        state.prev = {}  # no Docker on this host (or not yet) — nothing to report
        return []
    except Exception as exc:
        state._warn_once(f"docker metrics unavailable via {state.socket_path}: {exc}")
        return []
    state._reported_error = None

    samples: list[dict] = []
    counts: dict[str, int] = {}
    running: list[dict] = []
    for container in listing:
        container_state = str(container.get("State") or "unknown")
        counts[container_state] = counts.get(container_state, 0) + 1
        if container_state == "running":
            running.append(container)
    for container_state, count in counts.items():
        samples.append(_sample(CONTAINER_COUNT, count, collected_at, unit=UNIT_COUNT,
                               labels={"state": container_state}))

    running = running[:MAX_CONTAINERS]
    running_ids = {str(c.get("Id")) for c in running}
    snapshots: dict[str, tuple] = {}
    for container in running:
        container_id = str(container.get("Id"))
        name = _container_name(container)
        try:
            stats = state.client.stats(container_id)
        except (socket.timeout, TimeoutError):
            log.warning("docker stats for %s timed out; skipping the remaining containers this cycle", name)
            break
        except Exception as exc:
            log.warning("docker stats for %s failed: %s", name, exc)
            continue
        container_out, snapshot = container_samples(
            name, stats, state.prev.get(container_id), now, collected_at
        )
        samples.extend(container_out)
        snapshots[container_id] = snapshot

    # Keep baselines for running containers this scrape could not reach (timeout);
    # forget containers that stopped or vanished.
    state.prev = {
        cid: snap for cid, snap in state.prev.items() if cid in running_ids and cid not in snapshots
    }
    state.prev.update(snapshots)
    return samples
