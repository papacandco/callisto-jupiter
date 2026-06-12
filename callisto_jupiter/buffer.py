"""Disk-persisted, bounded store-and-forward buffer for metric samples.

When a push to the ingest endpoint fails, the agent keeps the samples here and
retries them on later cycles, so a transient outage never loses metrics. The
buffer survives agent restarts (an atomically written JSON file) and is bounded
by both age and count so it can't grow without limit or replay very stale data.

Every disk failure degrades to in-memory-only behaviour and is logged — the
buffer never raises into the agent loop.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone

log = logging.getLogger("callisto_jupiter.buffer")


def _parse_iso(value) -> datetime | None:
    """Parse a `collected_at` string (e.g. '2026-06-12T12:00:00Z'). Returns
    None when missing, unparseable, or lacking timezone info (so the age check
    can safely subtract from an aware 'now')."""
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


class SampleBuffer:
    """Ordered (oldest-first), bounded, optionally disk-backed list of samples.

    Samples are only appended at the end and removed from the front, so the list
    stays oldest-first without per-sample IDs. The agent is single-threaded, so
    no locking is needed.
    """

    def __init__(self, path: str | None, max_age_seconds: int, max_samples: int) -> None:
        self.path = path or None
        self.max_age_seconds = max_age_seconds
        self.max_samples = max_samples
        self._samples: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path or not os.path.isfile(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning("could not read buffer %s (%s); starting empty", self.path, exc)
            return
        if isinstance(data, list):
            self._samples = [s for s in data if isinstance(s, dict)]
        else:
            log.warning("buffer %s is not a JSON list; starting empty", self.path)

    def add(self, samples: list[dict]) -> None:
        self._samples.extend(samples)

    def pending(self) -> list[dict]:
        """A copy of the buffered samples, oldest first."""
        return list(self._samples)

    def count(self) -> int:
        return len(self._samples)

    def drop_first(self, n: int) -> None:
        """Remove the oldest `n` samples (called after a chunk is accepted)."""
        if n > 0:
            del self._samples[:n]

    def prune(self, now: datetime | None = None) -> None:
        """Drop samples older than `max_age_seconds`, then cap the total at
        `max_samples`, dropping oldest first. Samples with an unparseable
        `collected_at` are not age-dropped but are subject to the count cap."""
        if now is None:
            now = datetime.now(timezone.utc)
        kept = []
        for sample in self._samples:
            ts = _parse_iso(sample.get("collected_at"))
            if ts is not None and (now - ts).total_seconds() > self.max_age_seconds:
                continue
            kept.append(sample)
        if self.max_samples <= 0:
            kept = []
        elif len(kept) > self.max_samples:
            kept = kept[-self.max_samples:]
        self._samples = kept

    def persist(self) -> None:
        """Atomically write the buffer to disk (temp file + os.replace). A no-op
        in in-memory mode. Any OSError is logged and swallowed so the agent loop
        keeps running with the samples still held in memory."""
        if not self.path:
            return
        try:
            parent = os.path.dirname(self.path) or "."
            os.makedirs(parent, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=parent, prefix=".buffer-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(self._samples, fh)
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
        except OSError as exc:
            log.warning("could not persist buffer to %s (%s); keeping in memory", self.path, exc)
