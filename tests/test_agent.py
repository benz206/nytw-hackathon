import sys
import types
import asyncio

from intern_bot.perseus import PerseusCommandResult, PerseusDoctorReport
from intern_bot.agent import create_options, run_turn


def test_run_turn_returns_result_text_before_sdk_error(monkeypatch):
    class AssistantMessage:
        pass

    class ResultMessage:
        result = "Not logged in - please run /login"
        total_cost_usd = 0

    class TextBlock:
        pass

    async def query(*, prompt, options):
        yield ResultMessage()
        raise Exception("Claude Code returned an error result: success")

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    result = asyncio.run(run_turn("hello", options=object()))

    assert result.text == "Not logged in - please run /login"


def test_run_turn_logs_sdk_progress(monkeypatch):
    class TextBlock:
        def __init__(self, text):
            self.text = text

    class ToolUseBlock:
        name = "Agent"
        input = {"description": "ship it", "prompt": "secret-ish large prompt", "subagent_type": "coder"}

    class AssistantMessage:
        def __init__(self):
            self.content = [TextBlock("working on it"), ToolUseBlock()]

    class ResultMessage:
        result = "opened PR"
        total_cost_usd = 0.25
        subtype = "success"
        is_error = False
        stop_reason = "stop_sequence"
        errors = None
        permission_denials = None

    async def query(*, prompt, options):
        yield AssistantMessage()
        yield ResultMessage()

    fake_sdk = types.SimpleNamespace(
        AssistantMessage=AssistantMessage,
        ResultMessage=ResultMessage,
        TextBlock=TextBlock,
        query=query,
    )
    logs = []
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr("intern_bot.agent.ensure_github_app_token_from_env", lambda: None)

    result = asyncio.run(
        run_turn(
            "hello",
            options=object(),
            cwd="/tmp/repo",
            model="sonnet",
            logger=logs.append,
            git_author_name="bob-the-intern[bot]",
            git_author_email="291564787+bob-the-intern[bot]@users.noreply.github.com",
        )
    )

    assert result.text == "working on itopened PR"
    rendered = "\n".join(logs)
    assert (
        "[agent] turn setup cwd=/tmp/repo model=sonnet permission_mode=bypassPermissions "
        "git_author=bob-the-intern[bot]"
    ) in rendered
    assert "[agent] github app token ready" in rendered
    assert "[agent] query stream open" in rendered
    assert "sdk message AssistantMessage" in rendered
    assert "tool_use name=Agent input_keys=['description', 'prompt', 'subagent_type']" in rendered
    assert "subagent_type=coder" in rendered
    assert "description='ship it'" in rendered
    assert "sdk message ResultMessage subtype=success is_error=False" in rendered
    assert "turn done messages=2 text_chars=22 cost_usd=0.250000" in rendered


def test_create_options_wires_linear_mcp_from_env(monkeypatch):
    captured = {}

    class AgentDefinition:
        def __init__(self, *, description, prompt, tools, mcpServers=None):
            self.description = description
            self.prompt = prompt
            self.tools = tools
            self.mcpServers = mcpServers

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    fake_sdk = types.SimpleNamespace(
        AgentDefinition=AgentDefinition,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setenv("INTERN_LINEAR_TEAM_KEYS", "TOT")
    monkeypatch.setenv("INTERN_LINEAR_PLANNER_TOOLS", "mcp__linear__list_issues")

    create_options(
        stderr=print,
        git_author_name="bob-the-intern[bot]",
        git_author_email="291564787+bob-the-intern[bot]@users.noreply.github.com",
    )

    assert captured["mcp_servers"] == {
        "linear": {
            "command": "npx",
            "args": ["-y", "mcp-remote", "https://mcp.linear.app/mcp"],
            "env": {},
        }
    }
    assert captured["agents"]["planner"].tools == ["mcp__linear__list_issues"]
    assert captured["agents"]["planner"].mcpServers == ["linear"]
    assert captured["allowed_tools"] == ["Agent", "mcp__linear__list_issues"]
    assert "Allowed Linear team keys: TOT" in captured["agents"]["planner"].prompt
    assert captured["stderr"] is print
    assert captured["permission_mode"] == "bypassPermissions"
    assert captured["env"] == {
        "GIT_AUTHOR_NAME": "bob-the-intern[bot]",
        "GIT_COMMITTER_NAME": "bob-the-intern[bot]",
        "GIT_AUTHOR_EMAIL": "291564787+bob-the-intern[bot]@users.noreply.github.com",
        "GIT_COMMITTER_EMAIL": "291564787+bob-the-intern[bot]@users.noreply.github.com",
        "EMAIL": "291564787+bob-the-intern[bot]@users.noreply.github.com",
    }


def test_create_options_injects_remembered_notes(monkeypatch, tmp_path):
    captured = {}

    class AgentDefinition:
        def __init__(self, *, description, prompt, tools, mcpServers=None):
            self.description = description
            self.prompt = prompt
            self.tools = tools
            self.mcpServers = mcpServers

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    fake_sdk = types.SimpleNamespace(
        AgentDefinition=AgentDefinition,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
    )
    memory_path = tmp_path / ".intern" / "memory.md"
    memory_path.parent.mkdir()
    memory_path.write_text(
        "# Intern Memory\n\n"
        "## Remembered Notes\n\n"
        "- The user likes concise updates.\n\n"
        "## Activity Log\n\n"
        "- 2026-06-07T09:00:00 slack_turn: old log [cost_usd=1.000000]\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)

    create_options(memory_path=memory_path)

    assert "Runtime memory" in captured["system_prompt"]
    assert str(memory_path.resolve()) in captured["system_prompt"]
    assert "- The user likes concise updates." in captured["system_prompt"]
    assert "old log" not in captured["system_prompt"]
    coder_prompt = captured["agents"]["coder"].prompt
    assert "Runtime memory editing" in coder_prompt
    assert str(memory_path.resolve()) in coder_prompt


def test_create_options_adds_perseus_runtime_status_for_coder(monkeypatch, tmp_path):
    captured = {}

    class AgentDefinition:
        def __init__(self, *, description, prompt, tools, mcpServers=None):
            self.description = description
            self.prompt = prompt
            self.tools = tools
            self.mcpServers = mcpServers

    class ClaudeAgentOptions:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class HookMatcher:
        def __init__(self, *, matcher, hooks):
            self.matcher = matcher
            self.hooks = hooks

    fake_sdk = types.SimpleNamespace(
        AgentDefinition=AgentDefinition,
        ClaudeAgentOptions=ClaudeAgentOptions,
        HookMatcher=HookMatcher,
    )
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_sdk)
    monkeypatch.setattr(
        "intern_bot.agent.check_perseus",
        lambda **kwargs: PerseusDoctorReport(
            executable="/bin/perseus",
            token_path=tmp_path / "token",
            token_exists=True,
            version=PerseusCommandResult(("/bin/perseus", "--version"), 0, stdout="perseus 0.1\n"),
            index_status=PerseusCommandResult(
                ("/bin/perseus", "index", "--status"),
                2,
                stderr="no ready index",
            ),
            query_probe=PerseusCommandResult(
                ("/bin/perseus", "query", "where is the main application code?"),
                0,
                stdout="src/app/page.tsx:1\n",
            ),
        ),
    )

    create_options(cwd=str(tmp_path))

    coder_prompt = captured["agents"]["coder"].prompt
    assert "Runtime Perseus status" in coder_prompt
    assert "Perseus query is available for this repo" in coder_prompt
    assert "query probe works" in coder_prompt
