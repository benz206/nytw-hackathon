from intern_bot.linear import LinearConfig
from intern_bot.linear.preflight import (
    LinearMcpToolReport,
    check_linear_setup,
    format_linear_report,
    write_linear_planner_tools_env,
)


def test_linear_config_defaults_to_safe_policy():
    config = LinearConfig.from_env({})

    assert config.mcp_server_name == "linear"
    assert config.mcp_url == "https://mcp.linear.app/mcp"
    assert config.team_keys == ()
    assert config.allowed_statuses == ("Todo", "Backlog", "Triage")
    assert config.in_progress_status == "In Progress"
    assert config.max_estimate == 2
    assert config.comment_on_start is True
    assert config.comment_on_pr is True
    assert config.planner_tools == ()


def test_linear_config_reads_env_policy():
    config = LinearConfig.from_env(
        {
            "LINEAR_MCP_SERVER_NAME": "linear-prod",
            "LINEAR_MCP_URL": "https://example.com/mcp",
            "INTERN_LINEAR_TEAM_KEYS": "ENG, APP",
            "INTERN_LINEAR_ALLOWED_STATUSES": "Todo,Ready",
            "INTERN_LINEAR_IN_PROGRESS_STATUS": "Doing",
            "INTERN_LINEAR_BLOCKED_STATUS": "Needs Human",
            "INTERN_LINEAR_DONE_STATUS": "Done-ish",
            "INTERN_LINEAR_MAX_ESTIMATE": "3",
            "INTERN_LINEAR_CANDIDATE_LIMIT": "50",
            "INTERN_LINEAR_RANDOM_TOP_N": "5",
            "INTERN_LINEAR_COMMENT_ON_START": "0",
            "INTERN_LINEAR_COMMENT_ON_PR": "false",
            "INTERN_LINEAR_PLANNER_TOOLS": "mcp__linear__list_issues,mcp__linear__update_issue",
        }
    )

    assert config.mcp_server_name == "linear-prod"
    assert config.mcp_url == "https://example.com/mcp"
    assert config.team_keys == ("ENG", "APP")
    assert config.allowed_statuses == ("Todo", "Ready")
    assert config.in_progress_status == "Doing"
    assert config.blocked_status == "Needs Human"
    assert config.done_status == "Done-ish"
    assert config.max_estimate == 3
    assert config.candidate_limit == 50
    assert config.random_top_n == 5
    assert config.comment_on_start is False
    assert config.comment_on_pr is False
    assert config.planner_tools == ("mcp__linear__list_issues", "mcp__linear__update_issue")


def test_linear_config_builds_remote_mcp_config():
    config = LinearConfig.from_env({})

    assert config.mcp_server_config() == {
        "command": "npx",
        "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"],
        "env": {},
    }


def test_linear_report_requires_team_allowlist(monkeypatch):
    monkeypatch.setattr("intern_bot.linear.preflight.shutil.which", lambda name: f"/bin/{name}")
    report = check_linear_setup({})

    assert not report.ok
    rendered = format_linear_report(report)

    assert "INTERN_LINEAR_TEAM_KEYS: missing" in rendered
    assert "INTERN_LINEAR_PLANNER_TOOLS: missing" in rendered


def test_write_linear_planner_tools_env_appends_key(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text("INTERN_TARGET_REPO=/tmp/repo\n", encoding="utf-8")

    write_linear_planner_tools_env(("mcp__linear__list_issues",), env_file=env_file)

    assert env_file.read_text(encoding="utf-8") == (
        "INTERN_TARGET_REPO=/tmp/repo\n"
        "INTERN_LINEAR_PLANNER_TOOLS=mcp__linear__list_issues\n"
    )


def test_write_linear_planner_tools_env_replaces_existing_key(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "INTERN_LINEAR_PLANNER_TOOLS=old\nOTHER=value\n",
        encoding="utf-8",
    )

    write_linear_planner_tools_env(
        ("mcp__linear__list_issues", "mcp__linear__update_issue"),
        env_file=env_file,
    )

    assert env_file.read_text(encoding="utf-8") == (
        "INTERN_LINEAR_PLANNER_TOOLS=mcp__linear__list_issues,mcp__linear__update_issue\n"
        "OTHER=value\n"
    )


def test_linear_tool_report_recommends_sdk_tool_names():
    report = LinearMcpToolReport(
        server_name="linear",
        status="connected",
        tools=("get_issue", "delete_comment", "list_issues"),
    )

    assert report.recommended_planner_tools == (
        "mcp__linear__get_issue",
        "mcp__linear__list_issues",
    )
