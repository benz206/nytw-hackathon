import os

from intern_bot.env import load_env_file


def test_load_env_file_reads_simple_key_values(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        """
# comment
PLAIN=value
QUOTED="hello world"
        """,
        encoding="utf-8",
    )
    monkeypatch.delenv("PLAIN", raising=False)
    monkeypatch.delenv("QUOTED", raising=False)

    assert load_env_file(env_file)

    assert os.environ["PLAIN"] == "value"
    assert os.environ["QUOTED"] == "hello world"


def test_load_env_file_preserves_existing_values_without_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text("TOKEN=from-file\n", encoding="utf-8")
    monkeypatch.setenv("TOKEN", "already-set")

    load_env_file(env_file)

    assert os.environ["TOKEN"] == "already-set"
