import types

import pytest
import requests

import callisto_jupiter.client as client_mod
from callisto_jupiter.client import IngestClient


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(client_mod.time, "sleep", lambda _s: None)


def _resp(status):
    return types.SimpleNamespace(status_code=status)


def test_empty_samples_is_noop(monkeypatch):
    called = False

    def post(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s?token=t").push([]) is True
    assert called is False


def test_success_posts_to_dsn_with_samples(monkeypatch):
    captured = {}

    def post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return _resp(204)

    monkeypatch.setattr(client_mod.requests, "post", post)
    ok = IngestClient("https://ingest/srv-1?token=abc", timeout=7).push([{"metric_name": "cpu", "value": 5}])

    assert ok is True
    assert captured["url"] == "https://ingest/srv-1?token=abc"
    assert captured["json"] == {"samples": [{"metric_name": "cpu", "value": 5}]}
    assert captured["timeout"] == 7


def test_retries_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def post(url, json, timeout):
        calls["n"] += 1
        return _resp(500) if calls["n"] < 3 else _resp(200)

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s?token=t", max_attempts=3).push([{"a": 1}]) is True
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(monkeypatch):
    calls = {"n": 0}

    def post(url, json, timeout):
        calls["n"] += 1
        raise requests.ConnectionError("refused")

    monkeypatch.setattr(client_mod.requests, "post", post)
    assert IngestClient("https://x/s?token=t", max_attempts=3).push([{"a": 1}]) is False
    assert calls["n"] == 3
