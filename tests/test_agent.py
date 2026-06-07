import sys
import types
import asyncio

from intern_bot.agent import run_turn


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
