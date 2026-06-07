"""Always-on heartbeat driver for the Intern."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date, datetime
import os
import random

from .agent import TurnResult, run_turn
from .config import InternConfig
from .memory import InternMemory
from .slack import HEARTBEAT_CHECKLIST

PostMessage = Callable[[str], Awaitable[None]]
RunTurn = Callable[[str], Awaitable[TurnResult]]


def is_paused() -> bool:
    return os.getenv("INTERN_PAUSED") == "1"


def in_quiet_hours(now: datetime, config: InternConfig) -> bool:
    start = config.quiet_hours_start
    end = config.quiet_hours_end
    if start is None or end is None or start == end:
        return False
    hour = now.hour
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def under_daily_caps(config: InternConfig, memory: InternMemory, *, today: date | None = None) -> bool:
    usage = memory.usage_for(today or date.today())
    return (
        usage.active_tasks < config.max_concurrent_tasks
        and usage.self_started_prs < config.max_self_started_prs_per_day
        and usage.spend_usd < config.daily_spend_cap_usd
    )


async def heartbeat_once(
    *,
    config: InternConfig,
    memory: InternMemory,
    post_message: PostMessage,
    run_once: RunTurn | None = None,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> TurnResult | None:
    """Run one heartbeat if not paused, quiet, or capped."""
    current_time = now or datetime.now()
    if is_paused() or in_quiet_hours(current_time, config) or not under_daily_caps(config, memory):
        return None

    randomizer = rng or random.Random()
    prompt = HEARTBEAT_CHECKLIST
    if randomizer.random() < config.random_banter_chance:
        prompt += "\n(You may add one bit of light banter this tick.)"

    cwd = str(config.target_repo_path) if config.target_repo_path else None
    runner = run_once or (
        lambda heartbeat_prompt: run_turn(
            heartbeat_prompt,
            cwd=cwd,
            model=config.claude_model,
        )
    )
    result = await runner(prompt)
    memory.append_event(
        "heartbeat",
        _summarize(result.text),
        cost_usd=result.total_cost_usd,
        self_started_pr=_looks_like_pr_opened(result.text),
    )

    if result.text.strip() and result.text.strip() != "HEARTBEAT_OK":
        await post_message(result.text.strip())

    return result


async def heartbeat_loop(
    *,
    config: InternConfig,
    memory: InternMemory,
    post_message: PostMessage,
    run_once: RunTurn | None = None,
) -> None:
    while True:
        await heartbeat_once(
            config=config,
            memory=memory,
            post_message=post_message,
            run_once=run_once,
        )
        await asyncio.sleep(config.heartbeat_seconds)


def _looks_like_pr_opened(text: str) -> bool:
    lowered = text.lower()
    return "pull request" in lowered or "pr_url" in lowered or "/pull/" in lowered


def _summarize(text: str, limit: int = 180) -> str:
    one_line = " ".join(text.split()) or "HEARTBEAT_OK"
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 3] + "..."
