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

from callisto_jupiter import __version__

log = logging.getLogger("callisto_jupiter.client")

TOKEN_HEADER = "X-Callisto-Jupiter-Token"

# An explicit agent name instead of requests' default "python-requests/x":
# proxies in front of ingest (Cloudflare) treat generic library user agents as
# bots, and the operator can allow this one by name.
USER_AGENT = f"callisto-jupiter/{__version__}"

# 4xx answers a retry cannot change (bad token, unpaid, inactive, unknown
# server, blocked by a proxy). 408 and 429 are worth another attempt.
_RETRYABLE_4XX = {408, 429}


def describe_refusal(response) -> str:
    """Who refused a push and why, for the log: Cloudflare (its own page,
    `server: cloudflare` without our JSON body) or ingest itself (its JSON
    error, e.g. {"error":"inactive"}). Never raises; the body is truncated and
    holds no credential."""
    headers = getattr(response, "headers", None) or {}
    server = str(headers.get("server", headers.get("Server", ""))).lower()
    try:
        body = str(getattr(response, "text", "") or "")
    except Exception:  # noqa: BLE001 - logging must never fail the push loop
        body = ""
    body = " ".join(body.split())[:200]
    by = "cloudflare" if server == "cloudflare" and not body.startswith("{") else "ingest"
    ray = headers.get("cf-ray") or headers.get("CF-RAY") or ""

    return f"refused_by={by}" + (f" cf_ray={ray}" if ray else "") + (f" body={body}" if body else "")


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
        headers = {TOKEN_HEADER: self.token, "User-Agent": USER_AGENT}

        for attempt in range(1, self.max_attempts + 1):
            try:
                response = requests.post(
                    self.dsn, json=payload, headers=headers, timeout=self.timeout
                )
                if 200 <= response.status_code < 300:
                    return True
                log.warning(
                    "ingest returned HTTP %s (attempt %s/%s) %s",
                    response.status_code,
                    attempt,
                    self.max_attempts,
                    describe_refusal(response),
                )
                if 400 <= response.status_code < 500 and response.status_code not in _RETRYABLE_4XX:
                    break
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
