from callisto_jupiter.agent import Agent
from callisto_jupiter.config import Config


def _agent():
    return Agent(Config(dsn="https://x/s", token="t", interval_seconds=1))


def test_request_stop_sets_flag():
    agent = _agent()
    assert agent._stop.is_set() is False
    agent.request_stop()
    assert agent._stop.is_set() is True


def test_install_signal_handlers_does_not_raise():
    # On Unix SIGBREAK is absent and on Windows SIGTERM isn't delivered; the
    # guarded registration must tolerate missing signals on any platform.
    _agent().install_signal_handlers()


def test_run_once_uses_injected_client(monkeypatch):
    import callisto_jupiter.agent as agent_mod

    pushed = {}

    class FakeClient:
        def push(self, samples):
            pushed["samples"] = samples
            return True

    monkeypatch.setattr(agent_mod, "collect_samples", lambda disk_path, net_state=None: [{"metric_name": "cpu", "value": 1}])
    agent = Agent(Config(dsn="https://x/s", token="t"), client=FakeClient())

    assert agent.run_once() is True
    assert pushed["samples"] == [{"metric_name": "cpu", "value": 1}]


def test_failed_push_keeps_samples_buffered(monkeypatch):
    import callisto_jupiter.agent as agent_mod

    class FailingClient:
        def push(self, samples):
            return False

    monkeypatch.setattr(agent_mod, "collect_samples",
                        lambda disk_path, net_state=None: [{"metric_name": "cpu", "value": 1}])
    agent = Agent(Config(dsn="https://x/s", token="t"), client=FailingClient())

    assert agent.run_once() is False
    assert agent._buffer.count() == 1


def test_recovered_push_drains_backlog(monkeypatch):
    import callisto_jupiter.agent as agent_mod

    calls = {"n": 0}

    class FlakyClient:
        def push(self, samples):
            calls["n"] += 1
            return calls["n"] > 1  # first push fails, later ones succeed

    monkeypatch.setattr(agent_mod, "collect_samples",
                        lambda disk_path, net_state=None: [{"metric_name": "cpu", "value": 1}])
    agent = Agent(Config(dsn="https://x/s", token="t"), client=FlakyClient())

    assert agent.run_once() is False     # outage: buffered
    assert agent._buffer.count() == 1
    assert agent.run_once() is True      # recovery: collects 1 more, drains both
    assert agent._buffer.count() == 0


def test_backlog_flushes_in_chunks(monkeypatch):
    import callisto_jupiter.agent as agent_mod

    pushes = []

    class CountingClient:
        def push(self, samples):
            pushes.append(len(samples))
            return True

    monkeypatch.setattr(agent_mod, "collect_samples",
                        lambda disk_path, net_state=None: [{"metric_name": "cpu", "value": i} for i in range(5)])
    cfg = Config(dsn="https://x/s", token="t", flush_batch_size=2)
    agent = Agent(cfg, client=CountingClient())

    assert agent.run_once() is True
    assert pushes == [2, 2, 1]
    assert agent._buffer.count() == 0


def test_run_once_persists_buffer_to_disk_on_failure(tmp_path, monkeypatch):
    import json
    import callisto_jupiter.agent as agent_mod

    buffer_file = tmp_path / "buffer.json"

    state = {"up": False}

    class TogglingClient:
        def push(self, samples):
            return state["up"]

    monkeypatch.setattr(agent_mod, "collect_samples",
                        lambda disk_path, net_state=None: [{"metric_name": "cpu", "value": 1}])
    cfg = Config(dsn="https://x/s", token="t", buffer_path=str(buffer_file))
    agent = Agent(cfg, client=TogglingClient())

    # Outage: push fails, sample must be persisted to disk.
    assert agent.run_once() is False
    assert buffer_file.exists()
    assert json.loads(buffer_file.read_text()) == [{"metric_name": "cpu", "value": 1}]

    # Recovery: server accepts; buffer drains and the persisted file is emptied.
    state["up"] = True
    assert agent.run_once() is True
    assert json.loads(buffer_file.read_text()) == []
