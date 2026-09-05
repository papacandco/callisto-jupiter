"""CLI exit-code contract: a bad configuration must exit EX_CONFIG (78) so
systemd's RestartPreventExitStatus=78 stops the unit instead of crashlooping."""

import callisto_jupiter.__main__ as main_mod
from callisto_jupiter.__main__ import EX_CONFIG, main


def test_config_error_exits_ex_config(monkeypatch, capsys):
    def boom():
        raise main_mod.ConfigError("no DSN configured")

    monkeypatch.setattr(main_mod, "load_config", boom)
    assert main([]) == EX_CONFIG
    assert "no DSN configured" in capsys.readouterr().err


def test_ex_config_is_78():
    assert EX_CONFIG == 78
