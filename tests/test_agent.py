import sys
import types
import asyncio

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

    create_options()

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
