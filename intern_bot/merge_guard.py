"""Claude Agent SDK hook that blocks merges and other high-risk git actions."""

from __future__ import annotations

import os
import re
import shlex
from typing import Any

MERGE_PATTERNS = (
    re.compile(r"(^|\s)gh\s+pr\s+merge(\s|$)"),
    re.compile(r"(^|\s)git\s+merge(\s|$)"),
    re.compile(r"(^|\s)git\s+push\s+--force(?:-with-lease)?(\s|$)"),
    re.compile(r"(^|\s)git\s+push\s+.*\s--force(?:-with-lease)?(\s|$)"),
)

DENIAL_REASON = (
    "Merging, force-pushing, and equivalent PR finalization require an explicit "
    "human go-ahead in Slack. Ask for approval and set merge_authorized=True first."
)


def _extract_command(input_data: dict[str, Any] | None) -> str:
    if not input_data:
        return ""

    tool_input = input_data.get("tool_input") or input_data.get("toolInput") or input_data
    if not isinstance(tool_input, dict):
        return ""

    for key in ("command", "cmd"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value

    return ""


def _normalized_command(command: str) -> str:
    """Best-effort normalization that keeps shell snippets readable to regexes."""
    try:
        return " ".join(shlex.split(command))
    except ValueError:
        return command


def command_requires_merge_authorization(command: str) -> bool:
    normalized = _normalized_command(command)
    return any(pattern.search(normalized) for pattern in MERGE_PATTERNS)


def _context_authorized(context: Any) -> bool:
    session = getattr(context, "session", None)
    if isinstance(session, dict) and session.get("merge_authorized"):
        return True

    # Useful for local/manual runs where no SDK session state has been set.
    return os.getenv("INTERN_MERGE_AUTHORIZED") == "1"


async def block_merges(input_data: dict[str, Any], tool_use_id: str, context: Any) -> dict[str, Any]:
    """Block dangerous git/GitHub commands before the Bash tool can execute them."""
    command = _extract_command(input_data)
    if not command_requires_merge_authorization(command) or _context_authorized(context):
        return {}

    hook_event_name = input_data.get("hook_event_name", "PreToolUse")
    return {
        "hookSpecificOutput": {
            "hookEventName": hook_event_name,
            "permissionDecision": "deny",
            "permissionDecisionReason": DENIAL_REASON,
        }
    }

