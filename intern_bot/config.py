"""Runtime configuration for the Intern."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class InternConfig:
    claude_model: str | None = "sonnet"
    permission_mode: str | None = "bypassPermissions"
    git_author_name: str = "bob-the-intern[bot]"
    git_author_email: str = "291564787+bob-the-intern[bot]@users.noreply.github.com"
    target_repo_path: Path | None = None
    preview_url: str | None = None
    heartbeat_seconds: int = 30 * 60
    random_banter_chance: float = 0.10
    max_concurrent_tasks: int = 1
    max_self_started_prs_per_day: int = 3
    daily_spend_cap_usd: float = 5.00
    memory_path: Path = Path(".intern/memory.md")
    quiet_hours_start: int | None = None
    quiet_hours_end: int | None = None

    @classmethod
    def from_env(cls) -> "InternConfig":
        quiet_start = os.getenv("INTERN_QUIET_HOURS_START")
        quiet_end = os.getenv("INTERN_QUIET_HOURS_END")
        target_repo = os.getenv("INTERN_TARGET_REPO")
        return cls(
            claude_model=os.getenv("INTERN_CLAUDE_MODEL", cls.claude_model) or None,
            permission_mode=os.getenv("INTERN_PERMISSION_MODE", cls.permission_mode) or None,
            git_author_name=os.getenv("INTERN_GIT_AUTHOR_NAME", cls.git_author_name),
            git_author_email=os.getenv("INTERN_GIT_AUTHOR_EMAIL", cls.git_author_email),
            target_repo_path=Path(target_repo).expanduser() if target_repo else None,
            preview_url=os.getenv("INTERN_PREVIEW_URL") or None,
            heartbeat_seconds=_int_env("INTERN_HEARTBEAT_SECONDS", cls.heartbeat_seconds),
            random_banter_chance=_float_env("INTERN_RANDOM_BANTER_CHANCE", cls.random_banter_chance),
            max_concurrent_tasks=_int_env("INTERN_MAX_CONCURRENT_TASKS", cls.max_concurrent_tasks),
            max_self_started_prs_per_day=_int_env(
                "INTERN_MAX_SELF_STARTED_PRS_PER_DAY",
                cls.max_self_started_prs_per_day,
            ),
            daily_spend_cap_usd=_float_env("INTERN_DAILY_SPEND_CAP_USD", cls.daily_spend_cap_usd),
            memory_path=Path(os.getenv("INTERN_MEMORY_PATH", str(cls.memory_path))),
            quiet_hours_start=int(quiet_start) if quiet_start else None,
            quiet_hours_end=int(quiet_end) if quiet_end else None,
        )
