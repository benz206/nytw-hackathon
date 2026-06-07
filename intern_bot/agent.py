"""Claude Agent SDK wiring for the Intern orchestrator and specialists."""

from __future__ import annotations

from dataclasses import dataclass, field
import inspect
from pathlib import Path
import shlex
from typing import Any, Awaitable, Callable

from .codebase import CODER_PROMPT, DEFAULT_CODER_TOOLS
from .github.app_auth import ensure_github_app_token_from_env
from .github import DEFAULT_SHIPPER_TOOLS, SHIPPER_PROMPT
from .linear import DEFAULT_PLANNER_TOOLS, LinearConfig, PLANNER_PROMPT
from .memory import InternMemory
from .merge_guard import block_merges
from .perseus import check_perseus
from .slack import ORCHESTRATOR_PROMPT, ORCHESTRATOR_TOOLS

Logger = Callable[[str], None]
ProgressCallback = Callable[[str], Awaitable[None] | None]


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
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    memory_path: str | Path | None = None,
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
        "system_prompt": ORCHESTRATOR_PROMPT + _memory_runtime_prompt(memory_path),
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
                prompt=CODER_PROMPT + _perseus_runtime_prompt(cwd) + _memory_editing_prompt(memory_path),
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
    git_env = _git_identity_env(name=git_author_name, email=git_author_email)
    if git_env:
        kwargs["env"] = git_env
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


def _perseus_runtime_prompt(cwd: str | None) -> str:
    if not cwd:
        return ""

    report = check_perseus(
        cwd=cwd,
        run_doctor=False,
        run_query_probe=True,
        timeout_seconds=15,
    )
    lines = ["\n\n## Runtime Perseus status"]
    if report.executable is None:
        lines.append(
            "- Perseus CLI is not installed on PATH. Mark "
            "`perseus: unavailable (missing CLI)` and use normal repo tools."
        )
        return "\n".join(lines) + "\n"

    lines.append(f"- Perseus executable: {report.executable}.")
    lines.append(f"- Perseus token: {'found' if report.token_exists else 'missing'} at {report.token_path}.")
    if report.query_available:
        lines.append(
            "- Perseus query is available for this repo. Use `perseus query \"...\"` "
            "before broad Read/Grep/Glob orientation."
        )
        if report.index_status is not None and not report.index_status.ok:
            lines.append(
                "- `perseus index --status` reported not ready, but the query probe "
                "works; trust query availability for orientation."
            )
    else:
        reason = "unknown"
        for result in (report.query_probe, report.index_status, report.version):
            if result is not None and not result.ok and result.output:
                reason = result.output.splitlines()[0]
                break
        lines.append(
            f"- Perseus query was not confirmed before this turn: {reason}. Try one "
            "`perseus query` for non-trivial work, then fall back if it fails."
        )
    return "\n".join(lines) + "\n"


def _memory_runtime_prompt(memory_path: str | Path | None) -> str:
    if memory_path is None:
        return ""

    path = _memory_path(memory_path)
    memory = InternMemory(path)
    try:
        memory.ensure_exists()
        notes = memory.remembered_notes()
    except OSError as exc:
        return (
            "\n\n## Runtime memory\n"
            f"- Local memory path: {path}.\n"
            f"- Could not read memory this turn: {_one_line(str(exc), limit=240)}.\n"
        )

    lines = [
        "\n\n## Runtime memory",
        f"- Local memory path: {path}. This file is local and gitignored.",
        "- Use remembered notes as durable context, but treat the latest human message as higher priority.",
        (
            "- If the human explicitly asks you to remember or forget something, or gives a stable "
            "preference/fact that will help future turns, delegate CODER to update only the "
            "`## Remembered Notes` section of the memory file."
        ),
        "- Keep memory concise. Do not store secrets or sensitive personal data unless the human explicitly asks.",
    ]
    if notes:
        lines.append("")
        lines.append("Remembered notes:")
        lines.append(notes)
    else:
        lines.append("- No remembered notes yet.")
    return "\n".join(lines) + "\n"


def _memory_editing_prompt(memory_path: str | Path | None) -> str:
    if memory_path is None:
        return ""
    path = _memory_path(memory_path)
    return (
        "\n\n## Runtime memory editing\n"
        f"- Memory file: {path}.\n"
        "- When the orchestrator asks you to remember or forget something, edit only the "
        "`## Remembered Notes` section unless it explicitly asks for audit-log work.\n"
        "- Keep notes short, stable, and human-inspectable. Do not commit this file.\n"
    )


def _memory_path(memory_path: str | Path) -> Path:
    return Path(memory_path).expanduser().resolve()


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _git_identity_env(*, name: str | None, email: str | None) -> dict[str, str]:
    if not name and not email:
        return {}
    env: dict[str, str] = {}
    if name:
        env["GIT_AUTHOR_NAME"] = name
        env["GIT_COMMITTER_NAME"] = name
    if email:
        env["GIT_AUTHOR_EMAIL"] = email
        env["GIT_COMMITTER_EMAIL"] = email
        env["EMAIL"] = email
    return env


async def run_turn(
    prompt: str,
    *,
    options: Any | None = None,
    cwd: str | None = None,
    model: str | None = None,
    logger: Logger | None = None,
    permission_mode: str | None = "bypassPermissions",
    git_author_name: str | None = None,
    git_author_email: str | None = None,
    memory_path: str | Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> TurnResult:
    """Run one orchestrator turn and collect a human-postable result."""
    try:
        from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, query
    except ImportError as exc:
        raise RuntimeError(
            "claude-agent-sdk is not installed. Run `pip install -e .` before running turns."
        ) from exc

    if logger is not None:
        logger(
            f"[agent] turn setup cwd={cwd or '-'} model={model or '-'} "
            f"permission_mode={permission_mode or '-'} git_author={git_author_name or '-'}"
        )
    ensure_github_app_token_from_env()
    if logger is not None:
        logger("[agent] github app token ready")
    sdk_options = options or create_options(
        cwd=cwd,
        model=model,
        stderr=logger,
        permission_mode=permission_mode,
        git_author_name=git_author_name,
        git_author_email=git_author_email,
        memory_path=memory_path,
    )
    result = TurnResult()
    progress_seen: set[str] = set()

    async def emit_progress_once(text: str | None) -> None:
        if not text or text in progress_seen:
            return
        progress_seen.add(text)
        await _emit_progress(progress_callback, text)

    try:
        if logger is not None:
            logger("[agent] query stream open")
        async for message in query(prompt=prompt, options=sdk_options):
            result.raw_messages.append(message)
            if logger is not None:
                logger(f"[agent] {_sdk_message_summary(message, TextBlock)}")
            if isinstance(message, AssistantMessage):
                assistant_text = _assistant_text(message, TextBlock)
                if _assistant_message_has_tool_use(message, TextBlock):
                    progress_text = _one_line(assistant_text, limit=240) or _sdk_progress_update(
                        message,
                        TextBlock,
                    )
                    await emit_progress_once(progress_text)
                else:
                    result.text = _append_distinct(result.text, assistant_text)
            elif isinstance(message, ResultMessage):
                if message.result:
                    result.text = _append_distinct(result.text, message.result)
                if message.total_cost_usd:
                    result.total_cost_usd += message.total_cost_usd
            else:
                await emit_progress_once(_sdk_progress_update(message, TextBlock))
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


async def _emit_progress(callback: ProgressCallback | None, text: str) -> None:
    if callback is None:
        return
    maybe_awaitable = callback(text)
    if inspect.isawaitable(maybe_awaitable):
        await maybe_awaitable


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


def _assistant_message_has_tool_use(message: Any, text_block_type: Any) -> bool:
    for block in getattr(message, "content", []) or []:
        if isinstance(block, text_block_type) or (
            getattr(block, "type", None) == "text" and hasattr(block, "text")
        ):
            continue
        name = type(block).__name__
        if name in {"ToolUseBlock", "ServerToolUseBlock"} or hasattr(block, "input"):
            return True
    return False


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


def _sdk_progress_update(message: Any, text_block_type: Any) -> str | None:
    name = type(message).__name__
    if name == "AssistantMessage":
        for block in getattr(message, "content", []) or []:
            progress = _content_block_progress(block, text_block_type)
            if progress:
                return progress
    if name in {"TaskStartedMessage", "TaskProgressMessage"}:
        description = _one_line(str(getattr(message, "description", "") or ""), limit=120)
        if not description:
            return None
        return f"quick update: {description}"
    return None


def _content_block_summary(block: Any, text_block_type: Any) -> str:
    name = type(block).__name__
    if isinstance(block, text_block_type) or (getattr(block, "type", None) == "text" and hasattr(block, "text")):
        return f"text chars={len(getattr(block, 'text', '') or '')} preview={_one_line(getattr(block, 'text', '') or '', limit=140)!r}"
    if name in {"ToolUseBlock", "ServerToolUseBlock"} or hasattr(block, "input"):
        tool_name = getattr(block, "name", None)
        tool_input = getattr(block, "input", None)
        input_keys = sorted(tool_input.keys()) if isinstance(tool_input, dict) else []
        detail = ""
        if tool_name == "Agent" and isinstance(tool_input, dict):
            bits = []
            subagent_type = tool_input.get("subagent_type")
            description = tool_input.get("description")
            if isinstance(subagent_type, str):
                bits.append(f"subagent_type={subagent_type}")
            if isinstance(description, str):
                bits.append(f"description={_one_line(description, limit=120)!r}")
            if bits:
                detail = " " + " ".join(bits)
        return f"tool_use name={tool_name} input_keys={input_keys}{detail}"
    if name in {"ToolResultBlock", "ServerToolResultBlock"} or hasattr(block, "tool_use_id"):
        content = getattr(block, "content", None)
        return (
            f"tool_result id={getattr(block, 'tool_use_id', None)} "
            f"is_error={getattr(block, 'is_error', None)} "
            f"content_chars={len(str(content or ''))}"
        )
    return name


def _content_block_progress(block: Any, text_block_type: Any) -> str | None:
    name = type(block).__name__
    if isinstance(block, text_block_type) or (getattr(block, "type", None) == "text" and hasattr(block, "text")):
        return None
    if name in {"ToolUseBlock", "ServerToolUseBlock"} or hasattr(block, "input"):
        tool_name = str(getattr(block, "name", "") or "")
        tool_input = getattr(block, "input", None)
        if tool_name == "Agent" and isinstance(tool_input, dict):
            description = _one_line(str(tool_input.get("description") or ""), limit=120)
            if description:
                return f"quick update: {description}"
            subagent_type = _one_line(str(tool_input.get("subagent_type") or ""), limit=80)
            if subagent_type:
                return f"quick update: working with {subagent_type}"
        if tool_name.lower() in {"bash", "shell"} and isinstance(tool_input, dict):
            command = tool_input.get("command") or tool_input.get("cmd")
            if isinstance(command, str):
                description = _shell_progress_description(command)
                if description:
                    return f"quick update: {description}"
    return None


def _shell_progress_description(command: str) -> str | None:
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    visible_parts = []
    for part in parts:
        if not part or part.startswith("-"):
            break
        if any(char in part for char in "\n\r\t=:/\\"):
            break
        visible_parts.append(part)
        if len(visible_parts) >= 3:
            break
    if not visible_parts:
        return None
    return f"running {' '.join(visible_parts)}"


def _one_line(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
