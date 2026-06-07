import hashlib
import hmac
import asyncio
import os

from intern_bot.agent import TurnResult
from intern_bot.slack.prompts import ORCHESTRATOR_PROMPT
from intern_bot.slack.app import (
    CAT_PHOTO_URL,
    PrintPoster,
    SlackConfig,
    SlackEnvCheck,
    _create_single_workspace_bolt_app,
    casual_intern_reply,
    format_slack_prompt,
    handle_slack_text,
    message_event_name,
    reply_thread_ts_for_message_event,
    should_reply_to_message_event,
    verify_slack_signature,
)


def test_slack_config_reports_missing_runtime_tokens():
    config = SlackConfig(
        app_id="A123",
        client_id="client",
        client_secret="secret",
        signing_secret="signing",
    )

    rendered = "\n".join(SlackEnvCheck(config).lines())

    assert "SLACK_SIGNING_SECRET: set" in rendered
    assert "SLACK_BOT_TOKEN: missing" in rendered
    assert "Always Show My Bot as Online" in rendered
    assert config.missing_for_events_api() == ["SLACK_BOT_TOKEN"]
    assert config.missing_for_socket_mode() == ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]


def test_format_slack_prompt_includes_context():
    prompt = format_slack_prompt(
        "hello intern",
        channel="C123",
        user="U123",
        thread_ts="123.456",
        thread_messages=[
            {"user": "U123", "text": "<@B123> open a pr for a simple readme change"},
            {"bot_id": "B123", "text": "what change do you want in the readme?"},
        ],
    )

    assert "Slack channel: C123" in prompt
    assert "Slack user: U123" in prompt
    assert "Slack thread_ts: 123.456" in prompt
    assert "Recent Slack thread context (oldest to newest):" in prompt
    assert "- U123: <@B123> open a pr for a simple readme change" in prompt
    assert "- Intern: what change do you want in the readme?" in prompt
    assert "Answer the latest user message directly." in prompt
    assert "barely-trained intern in Slack" in prompt
    assert "One Slack bubble" in prompt
    assert "No markdown headings" in prompt
    assert "dumb-funny" in prompt
    assert "pick a concrete answer" in prompt
    assert "joking shrug" in prompt
    assert "one short paragraph with at most one tiny question" in prompt
    assert "treat that as permission to proceed" in prompt
    assert "open a draft PR" in prompt
    assert "where the PR is" in prompt
    assert "1-2 short sentences" in prompt
    assert "long capability list" in prompt
    assert prompt.endswith("hello intern")


def test_orchestrator_prompt_discourages_generic_flattery_for_casual_questions():
    assert "concrete answer" in ORCHESTRATOR_PROMPT
    assert "generic flattery" in ORCHESTRATOR_PROMPT


def test_orchestrator_prompt_uses_intern_coded_slack_voice():
    assert "intern-coded" in ORCHESTRATOR_PROMPT
    assert "actual intern in Slack" in ORCHESTRATOR_PROMPT
    assert "assistant-y phrases" in ORCHESTRATOR_PROMPT
    assert "not a project brief" in ORCHESTRATOR_PROMPT
    assert "No markdown headings" in ORCHESTRATOR_PROMPT
    assert '"hi" -> "hi" plus one cat photo/link' in ORCHESTRATOR_PROMPT
    assert "joke about coffee" in ORCHESTRATOR_PROMPT
    assert "uhhh mb guys" in ORCHESTRATOR_PROMPT
    assert "Ask at most one tiny question" in ORCHESTRATOR_PROMPT
    assert "no three-question questionnaire" in ORCHESTRATOR_PROMPT
    assert "1-2 short sentences" in ORCHESTRATOR_PROMPT
    assert "Do not dump a long feature list" in ORCHESTRATOR_PROMPT
    assert "Corny jokes" not in ORCHESTRATOR_PROMPT
    assert "well-timed GIF" not in ORCHESTRATOR_PROMPT
    assert "goofball" not in ORCHESTRATOR_PROMPT


def test_casual_intern_reply_handles_obvious_banter():
    assert casual_intern_reply("<@U123> hi") == f"hi\n{CAT_PHOTO_URL}"
    assert "make u some coffee" in casual_intern_reply("what can you actually do for our company")
    assert casual_intern_reply("why is prod down") == (
        "uhhh mb guys\nI can look, but I am not touching prod without on-call"
    )
    assert casual_intern_reply("what should we do about the deploy") is None


def test_verify_slack_signature_accepts_valid_signature():
    secret = "secret"
    timestamp = "1000"
    body = b'{"type":"event_callback"}'
    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    signature = "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()

    assert verify_slack_signature(
        signing_secret=secret,
        timestamp=timestamp,
        body=body,
        signature=signature,
        now=1000,
    )


def test_handle_slack_text_runs_runner_and_posts(capsys):
    async def runner(prompt: str) -> TurnResult:
        assert "Slack channel: C123" in prompt
        return TurnResult(text="hello from intern", total_cost_usd=0.01)

    asyncio.run(
        handle_slack_text(
            "please check the repo status",
            channel="C123",
            user="U123",
            poster=PrintPoster(),
            runner=runner,
        )
    )

    captured = capsys.readouterr()
    assert "hello from intern" in captured.out


def test_handle_slack_text_posts_work_ack_before_runner():
    class RecordingPoster:
        def __init__(self) -> None:
            self.messages = []

        async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
            self.messages.append((text, channel, thread_ts))

    async def runner(prompt: str) -> TurnResult:
        assert "open a pr for a simple readme change" in prompt
        return TurnResult(text="opened it: https://github.com/example/repo/pull/1")

    poster = RecordingPoster()

    result = asyncio.run(
        handle_slack_text(
            "<@U123> open a pr for a simple readme change please",
            channel="C123",
            user="U123",
            thread_ts="111.222",
            poster=poster,
            runner=runner,
        )
    )

    assert result.text == "opened it: https://github.com/example/repo/pull/1"
    assert poster.messages == [
        ("on it, I'll make the change and open a draft PR", "C123", "111.222"),
        ("opened it: https://github.com/example/repo/pull/1", "C123", "111.222"),
    ]


def test_handle_slack_text_uses_thread_context_for_affirmation():
    class RecordingPoster:
        def __init__(self) -> None:
            self.messages = []

        async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
            self.messages.append(text)

    class ThreadHistory:
        async def fetch_thread_messages(self, *, channel: str, thread_ts: str, limit: int = 20):
            return (
                {"user": "U123", "text": "<@B123> open a pr for a simple readme change please"},
                {"bot_id": "B123", "text": "what change do you want in the readme?"},
                {"user": "U123", "text": "just like add ur name or msth"},
                {"bot_id": "B123", "text": "add my name where lol? like a file, a README, contributors list?"},
            )

    async def runner(prompt: str) -> TurnResult:
        assert "Recent Slack thread context" in prompt
        assert "just like add ur name or msth" in prompt
        assert "User message:\nya bro" in prompt
        return TurnResult(text="done, opened https://github.com/example/repo/pull/2")

    poster = RecordingPoster()

    result = asyncio.run(
        handle_slack_text(
            "ya bro",
            channel="C123",
            user="U123",
            thread_ts="111.222",
            poster=poster,
            thread_history=ThreadHistory(),
            runner=runner,
        )
    )

    assert result.text == "done, opened https://github.com/example/repo/pull/2"
    assert poster.messages == [
        "on it, I'll make the change and open a draft PR",
        "done, opened https://github.com/example/repo/pull/2",
    ]


def test_handle_slack_text_posts_fast_banter_without_runner(capsys):
    async def runner(prompt: str) -> TurnResult:
        raise AssertionError("casual banter should not call the model runner")

    result = asyncio.run(
        handle_slack_text(
            "<@U123> hi",
            channel="C123",
            user="U123",
            poster=PrintPoster(),
            runner=runner,
        )
    )

    captured = capsys.readouterr()
    assert result.text == f"hi\n{CAT_PHOTO_URL}"
    assert CAT_PHOTO_URL in captured.out


def test_handle_slack_text_posts_runner_errors(capsys):
    async def runner(prompt: str) -> TurnResult:
        raise RuntimeError("Claude Code is not logged in")

    result = asyncio.run(
        handle_slack_text(
            "please hit the runtime error path",
            channel="C123",
            user="U123",
            poster=PrintPoster(),
            runner=runner,
        )
    )

    captured = capsys.readouterr()
    assert "Intern runtime error: Claude Code is not logged in" in captured.out
    assert "Claude Code is not logged in" in result.text


def test_handle_slack_text_marks_activity_and_logs_received_message(capsys):
    class RecordingActivity:
        def __init__(self) -> None:
            self.calls = []

        async def mark_online(self) -> None:
            self.calls.append(("online",))

        async def start_typing(self, *, channel: str, thread_ts: str) -> None:
            self.calls.append(("typing", channel, thread_ts))

        async def stop_typing(self, *, channel: str, thread_ts: str) -> None:
            self.calls.append(("clear", channel, thread_ts))

    async def runner(prompt: str) -> TurnResult:
        return TurnResult(text="reply")

    activity = RecordingActivity()

    asyncio.run(
        handle_slack_text(
            "please send a normal reply",
            channel="C123",
            user="U123",
            thread_ts="111.222",
            event_type="message.im",
            event_ts="333.444",
            poster=PrintPoster(),
            activity=activity,
            runner=runner,
            logger=print,
        )
    )

    captured = capsys.readouterr()
    assert (
        "[slack] received type=message.im channel=C123 user=U123 "
        "thread_ts=111.222 event_ts=333.444 text='please send a normal reply'"
    ) in captured.out
    assert activity.calls == [
        ("online",),
        ("typing", "C123", "111.222"),
        ("clear", "C123", "111.222"),
    ]


def test_handle_slack_text_always_posts_fallback_for_empty_result(capsys):
    async def runner(prompt: str) -> TurnResult:
        return TurnResult(text="")

    result = asyncio.run(
        handle_slack_text(
            "please send an empty reply",
            channel="C123",
            user="U123",
            poster=PrintPoster(),
            runner=runner,
        )
    )

    captured = capsys.readouterr()
    assert "Intern runtime did not return a reply" in captured.out
    assert "Intern runtime did not return a reply" in result.text


def test_should_reply_to_message_event_for_dms_and_threads_only():
    assert should_reply_to_message_event(
        {
            "type": "message",
            "channel": "D123",
            "channel_type": "im",
            "user": "U123",
            "ts": "1.0",
        }
    )
    assert should_reply_to_message_event(
        {
            "type": "message",
            "channel": "G123",
            "channel_type": "mpim",
            "user": "U123",
            "ts": "1.0",
        }
    )
    assert should_reply_to_message_event(
        {
            "type": "message",
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "thread_ts": "1.0",
            "ts": "2.0",
        }
    )
    assert not should_reply_to_message_event(
        {
            "type": "message",
            "channel": "C123",
            "channel_type": "channel",
            "user": "U123",
            "ts": "1.0",
        }
    )
    assert not should_reply_to_message_event(
        {
            "type": "message",
            "channel": "D123",
            "channel_type": "im",
            "user": "U123",
            "bot_id": "B123",
            "ts": "1.0",
        }
    )
    assert not should_reply_to_message_event(
        {
            "type": "message",
            "channel": "D123",
            "channel_type": "im",
            "user": "U123",
            "subtype": "message_changed",
            "ts": "1.0",
        }
    )


def test_message_event_helpers_return_reply_target_and_event_name():
    event = {
        "type": "message",
        "channel": "C123",
        "channel_type": "group",
        "user": "U123",
        "thread_ts": "1.0",
        "ts": "2.0",
    }

    assert reply_thread_ts_for_message_event(event) == "1.0"
    assert message_event_name(event) == "message.groups"
    assert message_event_name({"channel_type": "im"}) == "message.im"
    assert message_event_name({"channel_type": "mpim"}) == "message.mpim"
    assert message_event_name({"channel_type": "channel"}) == "message.channels"


def test_bolt_app_uses_bot_token_when_oauth_env_is_present(monkeypatch):
    monkeypatch.setenv("SLACK_CLIENT_ID", "client-id")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "client-secret")
    config = SlackConfig(signing_secret="signing", bot_token="xoxb-test")

    app = _create_single_workspace_bolt_app(config, token_verification_enabled=False)

    assert app._token == "xoxb-test"
    assert app._oauth_flow is None
    assert app._authorize is None
    assert app.client.token == "xoxb-test"
    assert "SLACK_CLIENT_ID" in os.environ
    assert "SLACK_CLIENT_SECRET" in os.environ
