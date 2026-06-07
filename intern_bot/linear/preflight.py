"""Linear configuration and local preflight helpers."""

from __future__ import annotations

from dataclasses import dataclass
import asyncio
import os
from pathlib import Path
import shutil
from typing import Any


DEFAULT_LINEAR_MCP_URL = "https://mcp.linear.app/mcp"
RECOMMENDED_LINEAR_PLANNER_TOOLS = {
    "get_issue",
    "list_issues",
    "save_issue",
    "list_comments",
    "save_comment",
    "list_issue_statuses",
    "get_issue_status",
    "list_issue_labels",
    "list_projects",
    "get_project",
    "list_teams",
    "get_team",
    "list_users",
    "get_user",
    "search_documentation",
}
LINEAR_MCP_SERVER_NAME_ENV = "LINEAR_MCP_SERVER_NAME"
LINEAR_MCP_URL_ENV = "LINEAR_MCP_URL"
INTERN_LINEAR_TEAM_KEYS_ENV = "INTERN_LINEAR_TEAM_KEYS"
INTERN_LINEAR_ALLOWED_STATUSES_ENV = "INTERN_LINEAR_ALLOWED_STATUSES"
INTERN_LINEAR_IN_PROGRESS_STATUS_ENV = "INTERN_LINEAR_IN_PROGRESS_STATUS"
INTERN_LINEAR_BLOCKED_STATUS_ENV = "INTERN_LINEAR_BLOCKED_STATUS"
INTERN_LINEAR_DONE_STATUS_ENV = "INTERN_LINEAR_DONE_STATUS"
INTERN_LINEAR_MAX_ESTIMATE_ENV = "INTERN_LINEAR_MAX_ESTIMATE"
INTERN_LINEAR_CANDIDATE_LIMIT_ENV = "INTERN_LINEAR_CANDIDATE_LIMIT"
INTERN_LINEAR_RANDOM_TOP_N_ENV = "INTERN_LINEAR_RANDOM_TOP_N"
INTERN_LINEAR_COMMENT_ON_START_ENV = "INTERN_LINEAR_COMMENT_ON_START"
INTERN_LINEAR_COMMENT_ON_PR_ENV = "INTERN_LINEAR_COMMENT_ON_PR"
INTERN_LINEAR_PLANNER_TOOLS_ENV = "INTERN_LINEAR_PLANNER_TOOLS"


@dataclass(frozen=True)
class LinearConfig:
    mcp_server_name: str = "linear"
    mcp_url: str = DEFAULT_LINEAR_MCP_URL
    team_keys: tuple[str, ...] = ()
    allowed_statuses: tuple[str, ...] = ("Todo", "Backlog", "Triage")
    in_progress_status: str = "In Progress"
    blocked_status: str = "Blocked"
    done_status: str = "Done"
    max_estimate: int = 2
    candidate_limit: int = 20
    random_top_n: int = 3
    comment_on_start: bool = True
    comment_on_pr: bool = True
    planner_tools: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "LinearConfig":
        values = os.environ if env is None else env
        return cls(
            mcp_server_name=values.get(LINEAR_MCP_SERVER_NAME_ENV, cls.mcp_server_name),
            mcp_url=values.get(LINEAR_MCP_URL_ENV, cls.mcp_url),
            team_keys=_csv_env(values, INTERN_LINEAR_TEAM_KEYS_ENV),
            allowed_statuses=_csv_env(
                values,
                INTERN_LINEAR_ALLOWED_STATUSES_ENV,
                default=cls.allowed_statuses,
            ),
            in_progress_status=values.get(
                INTERN_LINEAR_IN_PROGRESS_STATUS_ENV,
                cls.in_progress_status,
            ),
            blocked_status=values.get(INTERN_LINEAR_BLOCKED_STATUS_ENV, cls.blocked_status),
            done_status=values.get(INTERN_LINEAR_DONE_STATUS_ENV, cls.done_status),
            max_estimate=_int_env(values, INTERN_LINEAR_MAX_ESTIMATE_ENV, cls.max_estimate),
            candidate_limit=_int_env(values, INTERN_LINEAR_CANDIDATE_LIMIT_ENV, cls.candidate_limit),
            random_top_n=_int_env(values, INTERN_LINEAR_RANDOM_TOP_N_ENV, cls.random_top_n),
            comment_on_start=_bool_env(
                values,
                INTERN_LINEAR_COMMENT_ON_START_ENV,
                cls.comment_on_start,
            ),
            comment_on_pr=_bool_env(values, INTERN_LINEAR_COMMENT_ON_PR_ENV, cls.comment_on_pr),
            planner_tools=_csv_env(values, INTERN_LINEAR_PLANNER_TOOLS_ENV),
        )

    @property
    def has_team_allowlist(self) -> bool:
        return bool(self.team_keys)

    def mcp_server_config(self) -> dict[str, object]:
        return {
            "command": "npx",
            "args": ["-y", "mcp-remote", self.mcp_url],
            "env": {},
        }


@dataclass(frozen=True)
class LinearPreflightReport:
    config: LinearConfig
    node_executable: str | None
    npx_executable: str | None

    @property
    def ok(self) -> bool:
        return (
            self.node_executable is not None
            and self.npx_executable is not None
            and self.config.has_team_allowlist
        )


def check_linear_setup(env: dict[str, str] | None = None) -> LinearPreflightReport:
    return LinearPreflightReport(
        config=LinearConfig.from_env(env),
        node_executable=shutil.which("node"),
        npx_executable=shutil.which("npx"),
    )


@dataclass(frozen=True)
class LinearMcpToolReport:
    server_name: str
    status: str
    tools: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "connected" and bool(self.tools)

    @property
    def recommended_planner_tools(self) -> tuple[str, ...]:
        return tuple(_sdk_tool_name(self.server_name, tool) for tool in self.tools if tool in RECOMMENDED_LINEAR_PLANNER_TOOLS)


async def discover_linear_mcp_tools(
    config: LinearConfig | None = None,
    *,
    timeout_seconds: float = 60.0,
    poll_interval_seconds: float = 2.0,
) -> LinearMcpToolReport:
    """Connect to the configured Linear MCP server and return exposed tool names."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    except ImportError as exc:
        raise RuntimeError("claude-agent-sdk is required to inspect Linear MCP tools.") from exc

    linear_config = config or LinearConfig.from_env()
    options = ClaudeAgentOptions(
        mcp_servers={linear_config.mcp_server_name: linear_config.mcp_server_config()},
        strict_mcp_config=True,
    )
    async with ClaudeSDKClient(options) as client:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        report = _linear_tool_report_from_status(
            await client.get_mcp_status(),
            linear_config.mcp_server_name,
        )
        while report.status == "pending" and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(poll_interval_seconds)
            report = _linear_tool_report_from_status(
                await client.get_mcp_status(),
                linear_config.mcp_server_name,
            )
        return report


def _linear_tool_report_from_status(status: dict[str, Any], server_name: str) -> LinearMcpToolReport:
    servers = status.get("mcpServers", [])
    for server in servers:
        if server.get("name") != server_name:
            continue
        tool_names = tuple(
            tool["name"]
            for tool in server.get("tools", [])
            if isinstance(tool, dict) and isinstance(tool.get("name"), str)
        )
        return LinearMcpToolReport(
            server_name=server_name,
            status=str(server.get("status", "unknown")),
            tools=tool_names,
            error=server.get("error") if isinstance(server.get("error"), str) else None,
        )

    return LinearMcpToolReport(
        server_name=server_name,
        status="missing",
        error="Configured Linear MCP server was not returned by Claude SDK status.",
    )


def format_linear_tools_report(report: LinearMcpToolReport) -> str:
    lines = ["Linear MCP tools"]
    lines.append(f"server: {report.server_name}")
    lines.append(f"status: {report.status}")
    if report.error:
        lines.append(f"error: {report.error}")
    if report.tools:
        lines.append("tools:")
        lines.extend(f"- {tool}" for tool in report.tools)
        if report.recommended_planner_tools:
            lines.append(
                f"recommended_env: {INTERN_LINEAR_PLANNER_TOOLS_ENV}="
                f"{','.join(report.recommended_planner_tools)}"
            )
        else:
            lines.append("recommended_env: unavailable; no known planner-safe tool names matched.")
    elif report.status == "needs-auth":
        lines.append("next: complete the Linear MCP OAuth flow, then rerun this command.")
    elif report.status != "connected":
        lines.append("next: fix the MCP server status, then rerun this command.")
    else:
        lines.append("next: Linear MCP connected but returned no tools.")
    return "\n".join(lines)


def write_linear_planner_tools_env(
    tools: tuple[str, ...],
    *,
    env_file: str | Path = ".env.local",
) -> None:
    if not tools:
        raise ValueError("No Linear MCP tools were discovered.")
    _write_env_value(Path(env_file), INTERN_LINEAR_PLANNER_TOOLS_ENV, ",".join(tools))


def format_linear_report(report: LinearPreflightReport) -> str:
    config = report.config
    lines = ["Linear preflight"]
    lines.append(f"node: {report.node_executable or 'missing'}")
    lines.append(f"npx: {report.npx_executable or 'missing'}")
    lines.append(f"{LINEAR_MCP_SERVER_NAME_ENV}: {config.mcp_server_name}")
    lines.append(f"{LINEAR_MCP_URL_ENV}: {config.mcp_url}")
    lines.append(
        f"{INTERN_LINEAR_TEAM_KEYS_ENV}: "
        f"{','.join(config.team_keys) if config.team_keys else 'missing'}"
    )
    lines.append(f"{INTERN_LINEAR_ALLOWED_STATUSES_ENV}: {','.join(config.allowed_statuses)}")
    lines.append(f"{INTERN_LINEAR_IN_PROGRESS_STATUS_ENV}: {config.in_progress_status}")
    lines.append(f"{INTERN_LINEAR_BLOCKED_STATUS_ENV}: {config.blocked_status}")
    lines.append(f"{INTERN_LINEAR_DONE_STATUS_ENV}: {config.done_status}")
    lines.append(f"{INTERN_LINEAR_MAX_ESTIMATE_ENV}: {config.max_estimate}")
    lines.append(f"{INTERN_LINEAR_CANDIDATE_LIMIT_ENV}: {config.candidate_limit}")
    lines.append(f"{INTERN_LINEAR_RANDOM_TOP_N_ENV}: {config.random_top_n}")
    lines.append(f"{INTERN_LINEAR_COMMENT_ON_START_ENV}: {_bool_text(config.comment_on_start)}")
    lines.append(f"{INTERN_LINEAR_COMMENT_ON_PR_ENV}: {_bool_text(config.comment_on_pr)}")
    lines.append(
        f"{INTERN_LINEAR_PLANNER_TOOLS_ENV}: "
        f"{','.join(config.planner_tools) if config.planner_tools else 'missing'}"
    )

    if not report.ok:
        lines.extend(_next_steps(report))
    return "\n".join(lines)


def _next_steps(report: LinearPreflightReport) -> list[str]:
    steps: list[str] = []
    if report.node_executable is None or report.npx_executable is None:
        steps.append("next: install Node.js so the Linear MCP server can run through npx.")
    if not report.config.has_team_allowlist:
        steps.append(f"next: set {INTERN_LINEAR_TEAM_KEYS_ENV} to the Linear team keys the Intern may touch.")
    return steps


def _csv_env(
    values: dict[str, str] | os._Environ[str],
    name: str,
    *,
    default: tuple[str, ...] = (),
) -> tuple[str, ...]:
    raw = values.get(name)
    if raw is None or raw.strip() == "":
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _int_env(values: dict[str, str] | os._Environ[str], name: str, default: int) -> int:
    raw = values.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _bool_env(values: dict[str, str] | os._Environ[str], name: str, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _bool_text(value: bool) -> str:
    return "1" if value else "0"


def _write_env_value(env_path: Path, key: str, value: str) -> None:
    line = f"{key}={value}"
    if not env_path.exists():
        env_path.write_text(line + "\n", encoding="utf-8")
        return

    lines = env_path.read_text(encoding="utf-8").splitlines()
    replaced = False
    output: list[str] = []
    for existing in lines:
        if existing.strip().startswith(f"{key}="):
            if not replaced:
                output.append(line)
                replaced = True
            continue
        output.append(existing)
    if not replaced:
        output.append(line)
    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def _sdk_tool_name(server_name: str, tool_name: str) -> str:
    if tool_name.startswith("mcp__"):
        return tool_name
    return f"mcp__{server_name}__{tool_name}"
