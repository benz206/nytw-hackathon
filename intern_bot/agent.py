"""Claude Agent SDK wiring for the Intern orchestrator and specialists."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .merge_guard import block_merges
from .prompts import CODER_PROMPT, ORCHESTRATOR_PROMPT, PLANNER_PROMPT, SHIPPER_PROMPT


@dataclass
class TurnResult:
    text: str = ""
    total_cost_usd: float = 0.0
    raw_messages: list[Any] = field(default_factory=list)


def create_options(
    *,
    cwd: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    planner_tools: list[str] | None = None,
    coder_tools: list[str] | None = None,
    shipper_tools: list[str] | None = None,
) -> Any:
    """Create Claude Agent SDK options lazily so tests don't require the SDK."""
    try:
        from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .` or install the "
            "project dependencies before starting the Intern."
        ) from exc

    kwargs: dict[str, Any] = {
        "system_prompt": ORCHESTRATOR_PROMPT,
        "allowed_tools": ["Agent"],
        "agents": {
            "planner": AgentDefinition(
                description="Reads/writes/triages Linear tickets and plans intern-safe work.",
                prompt=PLANNER_PROMPT,
                tools=planner_tools or [],
            ),
            "coder": AgentDefinition(
                description="Writes and tests code on a feature branch after orienting with Perseus.",
                prompt=CODER_PROMPT,
                tools=coder_tools or ["Read", "Write", "Edit", "Grep", "Glob", "Bash"],
            ),
            "shipper": AgentDefinition(
                description="Pushes branches and opens or updates GitHub PRs; never merges.",
                prompt=SHIPPER_PROMPT,
                tools=shipper_tools or ["Bash"],
            ),
        },
        "hooks": {
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[block_merges])],
        },
    }
    if cwd:
        kwargs["cwd"] = cwd
    if mcp_servers:
        kwargs["mcp_servers"] = mcp_servers

    return ClaudeAgentOptions(**kwargs)


async def run_turn(prompt: str, *, options: Any | None = None, cwd: str | None = None) -> TurnResult:
    """Run one orchestrator turn and collect a human-postable result."""
    try:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .` before running turns."
        ) from exc

    sdk_options = options or create_options(cwd=cwd)
    result = TurnResult()

    async for message in query(prompt=prompt, options=sdk_options):
        result.raw_messages.append(message)
        if isinstance(message, AssistantMessage):
            result.text += _assistant_text(message, TextBlock)
        elif isinstance(message, ResultMessage):
            if message.result:
                result.text += message.result
            if message.total_cost_usd:
                result.total_cost_usd += message.total_cost_usd

    return result


def _assistant_text(message: Any, text_block_type: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []):
        if isinstance(block, text_block_type):
            parts.append(block.text)
        elif getattr(block, "type", None) == "text" and hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)
