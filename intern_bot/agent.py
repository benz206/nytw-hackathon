"""Claude Agent SDK wiring for the Intern orchestrator and specialists."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .codebase import CODER_PROMPT, DEFAULT_CODER_TOOLS
from .github.app_auth import ensure_github_app_token_from_env
from .github import DEFAULT_SHIPPER_TOOLS, SHIPPER_PROMPT
from .linear import DEFAULT_PLANNER_TOOLS, LinearConfig, PLANNER_PROMPT
from .merge_guard import block_merges
from .slack import ORCHESTRATOR_PROMPT, ORCHESTRATOR_TOOLS

Logger = Callable[[str], None]


@dataclass
class TurnResult:
    text: str = ""
    total_cost_usd: float = 0.0
    raw_messages: list[Any] = field(default_factory=list)


def create_options(
    *,
    cwd: str | None = None,
    model: str | None = None,
    mcp_servers: dict[str, Any] | None = None,
    planner_tools: list[str] | None = None,
    coder_tools: list[str] | None = None,
    shipper_tools: list[str] | None = None,
    stderr: Logger | None = None,
    permission_mode: str | None = "bypassPermissions",
) -> Any:
    """Create Claude Agent SDK options lazily so tests don't require the SDK."""
    try:
        from claude_agent_sdk import AgentDefinition, ClaudeAgentOptions, HookMatcher
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .` or install the "
            "project dependencies before starting the Intern."
        ) from exc

    linear_config = LinearConfig.from_env()
    effective_mcp_servers = mcp_servers if mcp_servers is not None else _default_mcp_servers(linear_config)
    effective_planner_tools = (
        planner_tools
        if planner_tools is not None
        else list(linear_config.planner_tools or DEFAULT_PLANNER_TOOLS)
    )
    planner_mcp_servers = [linear_config.mcp_server_name] if effective_mcp_servers else None
    allowed_tools = _dedupe([*ORCHESTRATOR_TOOLS, *effective_planner_tools])

    kwargs: dict[str, Any] = {
        "system_prompt": ORCHESTRATOR_PROMPT,
        "allowed_tools": allowed_tools,
        "agents": {
            "planner": AgentDefinition(
                description="Reads/writes/triages Linear tickets and plans intern-safe work.",
                prompt=PLANNER_PROMPT + _linear_policy_prompt(linear_config),
                tools=effective_planner_tools,
                mcpServers=planner_mcp_servers,
            ),
            "coder": AgentDefinition(
                description="Writes and tests code on a feature branch after orienting with Perseus.",
                prompt=CODER_PROMPT,
                tools=coder_tools if coder_tools is not None else DEFAULT_CODER_TOOLS,
            ),
            "shipper": AgentDefinition(
                description="Pushes branches and opens or updates GitHub PRs; never merges.",
                prompt=SHIPPER_PROMPT,
                tools=shipper_tools if shipper_tools is not None else DEFAULT_SHIPPER_TOOLS,
            ),
        },
        "hooks": {
            "PreToolUse": [HookMatcher(matcher="Bash", hooks=[block_merges])],
        },
    }
    if cwd:
        kwargs["cwd"] = cwd
    if model:
        kwargs["model"] = model
    if permission_mode:
        kwargs["permission_mode"] = permission_mode
    if effective_mcp_servers:
        kwargs["mcp_servers"] = effective_mcp_servers
    if stderr:
        kwargs["stderr"] = stderr

    return ClaudeAgentOptions(**kwargs)


def _default_mcp_servers(linear_config: LinearConfig) -> dict[str, Any]:
    if not linear_config.has_team_allowlist:
        return {}
    return {linear_config.mcp_server_name: linear_config.mcp_server_config()}


def _linear_policy_prompt(linear_config: LinearConfig) -> str:
    if not linear_config.has_team_allowlist:
        return ""
    return (
        "\n\n## Runtime Linear policy\n"
        f"- Allowed Linear team keys: {', '.join(linear_config.team_keys)}.\n"
        f"- Allowed starting statuses: {', '.join(linear_config.allowed_statuses)}.\n"
        f"- Move started work to: {linear_config.in_progress_status}.\n"
        f"- Use blocked status for blockers: {linear_config.blocked_status}.\n"
        f"- Maximum self-start estimate: {linear_config.max_estimate}.\n"
        "- When creating, searching, or updating issues, stay inside the allowed team keys. "
        "If a user asks for a ticket in a different key or the tool default would create one elsewhere, stop and ask.\n"
    )


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


async def run_turn(
    prompt: str,
    *,
    options: Any | None = None,
    cwd: str | None = None,
    model: str | None = None,
    logger: Logger | None = None,
    permission_mode: str | None = "bypassPermissions",
) -> TurnResult:
    """Run one orchestrator turn and collect a human-postable result."""
    try:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .` before running turns."
        ) from exc

    if logger is not None:
        logger(f"[agent] turn setup cwd={cwd or '-'} model={model or '-'} permission_mode={permission_mode or '-'}")
    ensure_github_app_token_from_env()
    if logger is not None:
        logger("[agent] github app token ready")
    sdk_options = options or create_options(
        cwd=cwd,
        model=model,
        stderr=logger,
        permission_mode=permission_mode,
    )
    result = TurnResult()

    try:
        if logger is not None:
            logger("[agent] query stream open")
        async for message in query(prompt=prompt, options=sdk_options):
            result.raw_messages.append(message)
            if logger is not None:
                logger(f"[agent] {_sdk_message_summary(message, TextBlock)}")
            if isinstance(message, AssistantMessage):
                result.text = _append_distinct(result.text, _assistant_text(message, TextBlock))
            elif isinstance(message, ResultMessage):
                if message.result:
                    result.text = _append_distinct(result.text, message.result)
                if message.total_cost_usd:
                    result.total_cost_usd += message.total_cost_usd
    except Exception as exc:
        if logger is not None:
            logger(f"[agent] query error {_one_line(str(exc), limit=500)!r}")
        if result.text.strip():
            return result
        raise

    if logger is not None:
        logger(
            "[agent] turn done "
            f"messages={len(result.raw_messages)} text_chars={len(result.text.strip())} "
            f"cost_usd={result.total_cost_usd:.6f}"
        )
    return result


def _assistant_text(message: Any, text_block_type: Any) -> str:
    parts: list[str] = []
    for block in getattr(message, "content", []):
        if isinstance(block, text_block_type):
            parts.append(block.text)
        elif getattr(block, "type", None) == "text" and hasattr(block, "text"):
            parts.append(block.text)
    return "".join(parts)


def _append_distinct(existing: str, addition: str | None) -> str:
    if not addition:
        return existing
    if addition in existing:
        return existing
    return existing + addition


def _sdk_message_summary(message: Any, text_block_type: Any) -> str:
    name = type(message).__name__
    if name == "AssistantMessage":
        blocks = []
        for block in getattr(message, "content", []):
            blocks.append(_content_block_summary(block, text_block_type))
        return f"sdk message AssistantMessage blocks=[{'; '.join(blocks)}]"
    if name == "ResultMessage":
        parts = [
            "sdk message ResultMessage",
            f"subtype={getattr(message, 'subtype', None)}",
            f"is_error={getattr(message, 'is_error', None)}",
            f"stop_reason={getattr(message, 'stop_reason', None)}",
            f"cost_usd={getattr(message, 'total_cost_usd', None)}",
            f"result_chars={len((getattr(message, 'result', None) or '').strip())}",
        ]
        errors = getattr(message, "errors", None)
        if errors:
            parts.append(f"errors={_one_line(str(errors), limit=240)!r}")
        denials = getattr(message, "permission_denials", None)
        if denials:
            parts.append(f"permission_denials={_one_line(str(denials), limit=240)!r}")
        return " ".join(parts)
    if name in {"TaskStartedMessage", "TaskProgressMessage"}:
        return (
            f"sdk message {name} "
            f"task_id={getattr(message, 'task_id', None)} "
            f"type={getattr(message, 'task_type', None)} "
            f"last_tool={getattr(message, 'last_tool_name', None)} "
            f"description={_one_line(str(getattr(message, 'description', '') or ''), limit=180)!r}"
        )
    if name == "SystemMessage":
        return f"sdk message SystemMessage subtype={getattr(message, 'subtype', None)}"
    return f"sdk message {name}"


def _content_block_summary(block: Any, text_block_type: Any) -> str:
    name = type(block).__name__
    if isinstance(block, text_block_type) or (getattr(block, "type", None) == "text" and hasattr(block, "text")):
        return f"text chars={len(getattr(block, 'text', '') or '')} preview={_one_line(getattr(block, 'text', '') or '', limit=140)!r}"
    if name in {"ToolUseBlock", "ServerToolUseBlock"} or hasattr(block, "input"):
        tool_name = getattr(block, "name", None)
        tool_input = getattr(block, "input", None)
        input_keys = sorted(tool_input.keys()) if isinstance(tool_input, dict) else []
        return f"tool_use name={tool_name} input_keys={input_keys}"
    if name in {"ToolResultBlock", "ServerToolResultBlock"} or hasattr(block, "tool_use_id"):
        content = getattr(block, "content", None)
        return (
            f"tool_result id={getattr(block, 'tool_use_id', None)} "
            f"is_error={getattr(block, 'is_error', None)} "
            f"content_chars={len(str(content or ''))}"
        )
    return name


def _one_line(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
