from intern_bot.config import InternConfig


def test_config_defaults_to_haiku(monkeypatch):
    monkeypatch.delenv("INTERN_CLAUDE_MODEL", raising=False)

    assert InternConfig.from_env().claude_model == "haiku"


def test_config_allows_model_override(monkeypatch):
    monkeypatch.setenv("INTERN_CLAUDE_MODEL", "sonnet")

    assert InternConfig.from_env().claude_model == "sonnet"
