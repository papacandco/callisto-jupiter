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
