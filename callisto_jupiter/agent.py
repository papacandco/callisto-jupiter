"""Agent loop: collect → push → sleep, until a stop signal arrives."""

from __future__ import annotations

import logging
import signal
import threading

from .buffer import SampleBuffer
from .client import IngestClient
from .collectors import NetworkRateState, collect_samples, prime_cpu
from .config import Config

log = logging.getLogger("callisto_jupiter.agent")


class Agent:
    def __init__(self, config: Config, client: IngestClient | None = None) -> None:
        self.config = config
        self.client = client or IngestClient(
            config.dsn, config.token, timeout=config.timeout_seconds
        )
        self._stop = threading.Event()
        self._net_state = NetworkRateState()
        self._buffer = SampleBuffer(
            config.buffer_path or None,
            config.buffer_max_age_seconds,
            config.buffer_max_samples,
        )

    def request_stop(self, *_args) -> None:
        log.info("stop requested; shutting down after current cycle")
        self._stop.set()

    def install_signal_handlers(self) -> None:
        # SIGINT exists on every platform; SIGTERM/SIGBREAK vary (Windows lacks
        # a delivered SIGTERM, but defines SIGBREAK). Register what's available;
        # the service manager (systemd/launchd/NSSM) terminates us regardless,
        # and the interruptible sleep keeps stops responsive where signals land.
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, self.request_stop)
            except (ValueError, OSError, RuntimeError):
                pass

    def run_once(self) -> bool:
        """Collect a cycle, buffer it durably, then flush the buffer to the
        ingest endpoint in front-first chunks. Returns True only when the buffer
        is empty afterwards (everything has been accepted by the server)."""
        samples = collect_samples(self.config.disk_path, self._net_state)
        self._buffer.add(samples)
        self._buffer.prune()
        self._buffer.persist()  # durable before any network attempt

        flushed = 0
        while self._buffer.count() > 0:
            chunk = self._buffer.pending()[: self.config.flush_batch_size]
            if not self.client.push(chunk):
                break  # server still down — keep the remainder buffered
            self._buffer.drop_first(len(chunk))
            self._buffer.persist()
            flushed += len(chunk)

        ok = self._buffer.count() == 0
        log.info(
            "collected %s; flushed %s; buffered %s; ok=%s",
            len(samples), flushed, self._buffer.count(), ok,
        )
        return ok

    def run(self) -> None:
        prime_cpu()
        self._net_state.prime()
        log.info(
            "callisto-jupiter started: interval=%ss disk_path=%s",
            self.config.interval_seconds,
            self.config.disk_path,
        )
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # never let the loop die
                log.exception("unexpected error during cycle: %s", exc)
            # Interruptible sleep: wakes immediately on stop.
            self._stop.wait(self.config.interval_seconds)
        log.info("callisto-jupiter stopped")


def run(config: Config) -> None:
    agent = Agent(config)
    agent.install_signal_handlers()
    agent.run()
