"""Small .env file loader for local runs."""

from __future__ import annotations

from pathlib import Path
import os


def load_env_file(path: str | Path = ".env.local", *, override: bool = False) -> bool:
    """Load KEY=VALUE lines from a local env file.

    This intentionally covers the simple `.env.local` shape we use here without
    adding a runtime dependency on python-dotenv.
    """
    env_path = Path(path)
    if not env_path.exists():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = _clean_value(value.strip())
        if not key or (not override and key in os.environ):
            continue
        os.environ[key] = value

    return True


def _clean_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
