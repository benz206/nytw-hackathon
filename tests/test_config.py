from intern_bot.config import InternConfig


def test_config_defaults_to_sonnet(monkeypatch):
    monkeypatch.delenv("INTERN_CLAUDE_MODEL", raising=False)
    monkeypatch.delenv("INTERN_PERMISSION_MODE", raising=False)

    assert InternConfig.from_env().claude_model == "sonnet"
    assert InternConfig.from_env().permission_mode == "bypassPermissions"


def test_config_allows_model_override(monkeypatch):
    monkeypatch.setenv("INTERN_CLAUDE_MODEL", "sonnet")
    monkeypatch.setenv("INTERN_PERMISSION_MODE", "acceptEdits")

    assert InternConfig.from_env().claude_model == "sonnet"
    assert InternConfig.from_env().permission_mode == "acceptEdits"


def test_config_reads_target_repo(monkeypatch):
    monkeypatch.setenv("INTERN_TARGET_REPO", "/Users/benz/Documents/task-manager")

    assert str(InternConfig.from_env().target_repo_path) == "/Users/benz/Documents/task-manager"
