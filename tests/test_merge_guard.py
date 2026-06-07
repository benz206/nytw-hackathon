import asyncio
from types import SimpleNamespace

import pytest

from intern_bot.merge_guard import block_merges, command_requires_merge_authorization


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 123 --squash",
        "git merge feature-branch",
        "git push --force origin main",
        "git push origin main --force-with-lease",
    ],
)
def test_command_requires_merge_authorization_for_blocked_patterns(command):
    assert command_requires_merge_authorization(command)


@pytest.mark.parametrize(
    "command",
    [
        "gh pr create --draft",
        "git push origin intern/ENG-123-fix",
        "pytest",
    ],
)
def test_command_allows_safe_patterns(command):
    assert not command_requires_merge_authorization(command)


def test_block_merges_denies_without_authorization():
    result = asyncio.run(
        block_merges(
            {"hook_event_name": "PreToolUse", "tool_input": {"command": "gh pr merge 123"}},
            "toolu_1",
            SimpleNamespace(session={}),
        )
    )

    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "human go-ahead" in output["permissionDecisionReason"]


def test_block_merges_allows_with_authorization():
    result = asyncio.run(
        block_merges(
            {"hook_event_name": "PreToolUse", "tool_input": {"command": "gh pr merge 123"}},
            "toolu_1",
            SimpleNamespace(session={"merge_authorized": True}),
        )
    )

    assert result == {}
