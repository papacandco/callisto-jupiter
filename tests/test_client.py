import types

import pytest
import requests

import callisto_jupiter.client as client_mod
from callisto_jupiter.client import IngestClient


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)


def _resp(status, text="", headers=None):
    return types.SimpleNamespace(status_code=status, text=text, headers=headers or {})


def test_empty_samples_is_noop(monkeypatch):
    called = False

    def post(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s", "t").push([]) is True
    assert called is False


def test_success_posts_to_dsn_with_samples_and_token_header(monkeypatch):
    captured = {}

    def post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _resp(204)

    monkeypatch.setattr(client_mod.requests, "post", post)
    ok = IngestClient("https://ingest/srv-1", "abc", timeout=7).push([{"metric_name": "cpu", "value": 5}])

    assert ok is True
    assert captured["url"] == "https://ingest/srv-1"
    assert captured["json"] == {"samples": [{"metric_name": "cpu", "value": 5}]}
    assert captured["headers"] == {
        "X-Callisto-Jupiter-Token": "abc",
        "User-Agent": client_mod.USER_AGENT,
    }
    assert client_mod.USER_AGENT.startswith("callisto-jupiter/")
    assert captured["timeout"] == 7


def test_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def post(url, json, headers, timeout):
        calls["n"] += 1
        return _resp(500) if calls["n"] < 3 else _resp(200)

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s", "t", max_attempts=3).push([{"a": 1}]) is True
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def post(url, json, headers, timeout):
        calls["n"] += 1
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s", "t", max_attempts=3).push([{"a": 1}]) is False
    assert calls["n"] == 3


def test_a_hopeless_4xx_is_not_retried_and_says_who_refused(monkeypatch, caplog):
    calls = {"n": 0}
    page = "<!DOCTYPE html><html><title>Access denied | Cloudflare</title> error code: 1010"

    def post(url, json, headers, timeout):
        calls["n"] += 1
        return _resp(403, page, {"server": "cloudflare", "cf-ray": "8f1-JNB"})

    monkeypatch.setattr(client_mod.requests, "post", post)
    with caplog.at_level("WARNING", logger="callisto_jupiter.client"):
        assert IngestClient("https://x/s", "t", max_attempts=3).push([{"a": 1}]) is False

    assert calls["n"] == 1, "a 403 cannot change on retry"
    assert "refused_by=cloudflare" in caplog.text
    assert "cf_ray=8f1-JNB" in caplog.text
    assert "1010" in caplog.text


def test_ingest_own_refusal_is_reported_as_ingest():
    resp = _resp(403, '{"error":"inactive"}', {"server": "cloudflare", "cf-ray": "x"})
    assert client_mod.describe_refusal(resp).startswith("refused_by=ingest")
    assert 'body={"error":"inactive"}' in client_mod.describe_refusal(resp)


def test_rate_limited_pushes_are_retried(monkeypatch):
    calls = {"n": 0}

    def post(url, json, headers, timeout):
        calls["n"] += 1
        return _resp(429) if calls["n"] < 2 else _resp(200)

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s", "t", max_attempts=3).push([{"a": 1}]) is True
    assert calls["n"] == 2
