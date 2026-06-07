"""Markdown memory, audit log, and daily cap accounting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

REMEMBERED_NOTES_HEADING = "## Remembered Notes"
ACTIVITY_LOG_HEADING = "## Activity Log"


@dataclass(frozen=True)
class DailyUsage:
    date: date
    self_started_prs: int = 0
    active_tasks: int = 0
    spend_usd: float = 0.0


class InternMemory:
    """Tiny persistent memory file inspired by OpenClaw-style agents."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure_exists(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            "# Intern Memory\n\n"
            f"{REMEMBERED_NOTES_HEADING}\n\n"
            "- Nothing yet.\n\n"
            f"{ACTIVITY_LOG_HEADING}\n\n",
            encoding="utf-8",
        )

    def remembered_notes(self, *, max_chars: int = 4000) -> str:
        """Return durable notes intended for agent context, excluding audit logs."""
        if not self.path.exists():
            return ""

        content = self.path.read_text(encoding="utf-8")
        section = _markdown_section(content, REMEMBERED_NOTES_HEADING).strip()
        if not section or section == "- Nothing yet.":
            return ""
        if len(section) <= max_chars:
            return section
        return section[-max_chars:].lstrip()

    def append_event(
        self,
        event_type: str,
        summary: str,
        *,
        cost_usd: float | None = None,
        pr_url: str | None = None,
        task_started: bool = False,
        task_finished: bool = False,
        self_started_pr: bool = False,
        happened_at: datetime | None = None,
    ) -> None:
        self.ensure_exists()
        timestamp = (happened_at or datetime.now()).isoformat(timespec="seconds")
        markers = []
        if task_started:
            markers.append("task_started")
        if task_finished:
            markers.append("task_finished")
        if self_started_pr:
            markers.append("self_started_pr")
        if cost_usd is not None:
            markers.append(f"cost_usd={cost_usd:.6f}")
        if pr_url:
            markers.append(f"pr_url={pr_url}")

        suffix = f" [{' '.join(markers)}]" if markers else ""
        self._ensure_activity_log_heading()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"- {timestamp} {event_type}: {summary}{suffix}\n")

    def usage_for(self, day: date) -> DailyUsage:
        if not self.path.exists():
            return DailyUsage(date=day)

        prs = 0
        active_tasks = 0
        spend = 0.0
        day_prefix = day.isoformat()

        for line in self._event_lines():
            if not line.startswith(f"- {day_prefix}"):
                continue
            if "self_started_pr" in line:
                prs += 1
            if "task_started" in line:
                active_tasks += 1
            if "task_finished" in line:
                active_tasks = max(0, active_tasks - 1)
            spend += _extract_cost(line)

        return DailyUsage(date=day, self_started_prs=prs, active_tasks=active_tasks, spend_usd=spend)

    def _event_lines(self) -> Iterable[str]:
        return self.path.read_text(encoding="utf-8").splitlines()

    def _ensure_activity_log_heading(self) -> None:
        content = self.path.read_text(encoding="utf-8")
        if ACTIVITY_LOG_HEADING in content:
            return
        with self.path.open("a", encoding="utf-8") as handle:
            if content and not content.endswith("\n"):
                handle.write("\n")
            handle.write(f"\n{ACTIVITY_LOG_HEADING}\n")


def _extract_cost(line: str) -> float:
    marker = "cost_usd="
    if marker not in line:
        return 0.0
    tail = line.split(marker, 1)[1].split(" ", 1)[0].rstrip("]")
    try:
        return float(tail)
    except ValueError:
        return 0.0


def _markdown_section(content: str, heading: str) -> str:
    lines = content.splitlines()
    try:
        start = lines.index(heading) + 1
    except ValueError:
        return ""

    end = len(lines)
    for index in range(start, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    return "\n".join(lines[start:end]).strip()
