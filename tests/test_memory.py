from datetime import date, datetime

from intern_bot.memory import InternMemory


def test_memory_tracks_daily_usage(tmp_path):
    memory = InternMemory(tmp_path / "memory.md")
    memory.append_event(
        "heartbeat",
        "started ENG-123",
        cost_usd=0.25,
        task_started=True,
        happened_at=datetime(2026, 6, 7, 9, 0),
    )
    memory.append_event(
        "heartbeat",
        "opened PR",
        cost_usd=0.75,
        self_started_pr=True,
        task_finished=True,
        happened_at=datetime(2026, 6, 7, 10, 0),
    )

    usage = memory.usage_for(date(2026, 6, 7))

    assert usage.self_started_prs == 1
    assert usage.active_tasks == 0
    assert usage.spend_usd == 1.0

