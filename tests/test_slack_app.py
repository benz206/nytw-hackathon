import hashlib
import hmac
import asyncio

from intern_bot.agent import TurnResult
from intern_bot.slack.app import (
    PrintPoster,
    SlackConfig,
    SlackEnvCheck,
    format_slack_prompt,
    handle_slack_text,
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
    assert config.missing_for_events_api() == ["SLACK_BOT_TOKEN"]
    assert config.missing_for_socket_mode() == ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"]


def test_format_slack_prompt_includes_context():
    prompt = format_slack_prompt("hello intern", channel="C123", user="U123", thread_ts="123.456")

    assert "Slack channel: C123" in prompt
    assert "Slack user: U123" in prompt
    assert "Slack thread_ts: 123.456" in prompt
    assert prompt.endswith("hello intern")


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
            "hi",
            channel="C123",
            user="U123",
            poster=PrintPoster(),
            runner=runner,
        )
    )

    captured = capsys.readouterr()
    assert "hello from intern" in captured.out
