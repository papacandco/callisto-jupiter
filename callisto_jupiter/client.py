"""Ingest client: POST {samples} to the Callisto server-monitoring DSN.

The DSN (https://ingest.callistosignal.com/servers/<id>) identifies the server; the push
credential travels separately in the `X-Callisto-Jupiter-Token` header so the
DSN can be logged without leaking it. Pushes never raise into the agent loop —
failures are retried with backoff and then reported as a False return.
"""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("callisto_jupiter.client")

TOKEN_HEADER = "X-Callisto-Jupiter-Token"


class IngestClient:
    def __init__(self, dsn: str, token: str, timeout: int = 10, max_attempts: int = 3) -> None:
        self.dsn = dsn
        self.token = token
        self.timeout = timeout
        self.max_attempts = max_attempts

    def push(self, samples: list[dict]) -> bool:
        """POST the samples. Returns True on a 2xx response, False otherwise.

        Empty sample lists are a no-op (treated as success)."""
        if not samples:
            return True

        payload = {"samples": samples}
        headers = {TOKEN_HEADER: self.token}

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(
                    self.dsn, json=payload, headers=headers, timeout=self.timeout
                )
                if 200 <= response.status_code < 300:
                    return True
                log.warning(
                    "ingest returned HTTP %s (attempt %s/%s)",
                    response.status_code,
                    attempt,
                    self.max_attempts,
                )
            except requests.RequestException as exc:
                log.warning(
                    "ingest request failed (attempt %s/%s): %s",
                    attempt,
                    self.max_attempts,
                    exc,
                )

            if attempt < self.max_attempts:
                time.sleep(min(2 ** (attempt - 1), 10))

        log.error("giving up on %s samples after %s attempts", len(samples), self.max_attempts)
        return False
