"""Agent loop: collect → push → sleep, until a stop signal arrives."""

from __future__ import annotations

import logging
import signal
import threading

from .client import IngestClient
from .collectors import collect_samples, prime_cpu
from .config import Config

log = logging.getLogger("callisto_jupiter.agent")


class Agent:
    def __init__(self, config: Config, client: IngestClient | None = None) -> None:
        self.config = config
        self.client = client or IngestClient(config.dsn, timeout=config.timeout_seconds)
        self._stop = threading.Event()

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
        """Collect and push one cycle. Returns the push result."""
        samples = collect_samples(self.config.disk_path)
        ok = self.client.push(samples)
        log.info("pushed %s samples ok=%s", len(samples), ok)
        return ok

    def run(self) -> None:
        prime_cpu()
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
