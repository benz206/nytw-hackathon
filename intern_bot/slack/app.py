"""Slack runtime integration for the Intern."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import asyncio
import hmac
import hashlib
import inspect
import json
import os
import time
from typing import Any, Awaitable, Callable, Iterator, Protocol
from urllib import error, request

from intern_bot.agent import TurnResult, run_turn
from intern_bot.config import InternConfig
from intern_bot.env import load_env_file
from intern_bot.memory import InternMemory


Runner = Callable[[str], Awaitable[TurnResult]]


class Poster(Protocol):
    async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
        """Post a message to Slack."""


@dataclass(frozen=True)
class SlackConfig:
    app_id: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    signing_secret: str | None = None
    bot_token: str | None = None
    app_token: str | None = None
    default_channel: str | None = None

    @classmethod
    def from_env(cls, *, env_file: str | None = ".env.local") -> "SlackConfig":
        if env_file:
            load_env_file(env_file)
        return cls(
            app_id=os.getenv("SLACK_APP_ID"),
            client_id=os.getenv("SLACK_CLIENT_ID"),
            client_secret=os.getenv("SLACK_CLIENT_SECRET"),
            signing_secret=os.getenv("SLACK_SIGNING_SECRET"),
            bot_token=os.getenv("SLACK_BOT_TOKEN"),
            app_token=os.getenv("SLACK_APP_TOKEN"),
            default_channel=os.getenv("SLACK_DEFAULT_CHANNEL"),
        )

    def missing_for_events_api(self) -> list[str]:
        missing = []
        if not self.signing_secret:
            missing.append("SLACK_SIGNING_SECRET")
        if not self.bot_token:
            missing.append("SLACK_BOT_TOKEN")
        return missing

    def missing_for_socket_mode(self) -> list[str]:
        missing = self.missing_for_events_api()
        if not self.app_token:
            missing.append("SLACK_APP_TOKEN")
        return missing


@dataclass(frozen=True)
class SlackEnvCheck:
    config: SlackConfig

    def lines(self) -> list[str]:
        configured = {
            "SLACK_APP_ID": self.config.app_id,
            "SLACK_CLIENT_ID": self.config.client_id,
            "SLACK_CLIENT_SECRET": self.config.client_secret,
            "SLACK_SIGNING_SECRET": self.config.signing_secret,
            "SLACK_BOT_TOKEN": self.config.bot_token,
            "SLACK_APP_TOKEN": self.config.app_token,
            "SLACK_DEFAULT_CHANNEL": self.config.default_channel,
        }
        lines = ["Slack env check"]
        for key, value in configured.items():
            lines.append(f"{key}: {'set' if value else 'missing'}")

        events_missing = self.config.missing_for_events_api()
        socket_missing = self.config.missing_for_socket_mode()
        lines.append(
            "Events API: ready" if not events_missing else f"Events API: missing {', '.join(events_missing)}"
        )
        lines.append(
            "Socket Mode: ready" if not socket_missing else f"Socket Mode: missing {', '.join(socket_missing)}"
        )
        return lines


class SlackWebPoster:
    """Minimal Slack Web API poster using stdlib urllib."""

    def __init__(self, bot_token: str) -> None:
        self.bot_token = bot_token

    async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
        payload = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        await asyncio.to_thread(self._post_json, "https://slack.com/api/chat.postMessage", payload)

    def _post_json(self, url: str, payload: dict[str, str]) -> None:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except error.URLError as exc:
            raise RuntimeError(f"Slack post failed: {exc}") from exc

        if not data.get("ok"):
            raise RuntimeError(f"Slack post failed: {data.get('error', 'unknown_error')}")


class PrintPoster:
    """Poster for local dry runs."""

    async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
        target = channel if thread_ts is None else f"{channel} thread {thread_ts}"
        print(f"[slack dry-run -> {target}] {text}")


async def handle_slack_text(
    text: str,
    *,
    channel: str,
    user: str | None = None,
    thread_ts: str | None = None,
    poster: Poster,
    runner: Runner | None = None,
    memory: InternMemory | None = None,
) -> TurnResult:
    """Run one Intern turn for a Slack message and post the reply."""
    prompt = format_slack_prompt(text, channel=channel, user=user, thread_ts=thread_ts)
    try:
        result = await (runner or run_turn)(prompt)
    except Exception as exc:
        reply = f"Intern runtime error: {_one_line(str(exc), limit=300)}"
        await poster.post_message(reply, channel=channel, thread_ts=thread_ts)
        if memory is not None:
            memory.append_event("slack_turn_error", _one_line(text))
        return TurnResult(text=reply)

    reply = result.text.strip()
    if reply:
        await poster.post_message(reply, channel=channel, thread_ts=thread_ts)
    if memory is not None:
        memory.append_event("slack_turn", _one_line(text), cost_usd=result.total_cost_usd)
    return result


def format_slack_prompt(
    text: str,
    *,
    channel: str,
    user: str | None = None,
    thread_ts: str | None = None,
) -> str:
    parts = [f"Slack channel: {channel}"]
    if user:
        parts.append(f"Slack user: {user}")
    if thread_ts:
        parts.append(f"Slack thread_ts: {thread_ts}")
    parts.append("")
    parts.append(text)
    return "\n".join(parts)


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: int | None = None,
) -> bool:
    """Verify Slack's v0 request signature."""
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    current_time = int(time.time()) if now is None else now
    if abs(current_time - ts) > 60 * 5:
        return False

    base = b"v0:" + timestamp.encode("utf-8") + b":" + body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def run_socket_mode(config: SlackConfig) -> None:
    """Start a Slack Socket Mode app for app mentions."""
    missing = config.missing_for_socket_mode()
    if missing:
        raise RuntimeError(f"Missing required Slack env vars for Socket Mode: {', '.join(missing)}")

    try:
        from slack_bolt.adapter.socket_mode import SocketModeHandler
    except ImportError as exc:
        raise RuntimeError(
            "Slack Socket Mode requires slack-bolt. Run `pip install -r requirements.txt`."
        ) from exc

    app = _create_single_workspace_bolt_app(config)
    runtime_config = InternConfig.from_env()
    memory = InternMemory(runtime_config.memory_path)

    @app.event("app_mention")
    def handle_app_mention(event, say):  # type: ignore[no-untyped-def]
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event.get("ts")
        user = event.get("user")
        text = event.get("text", "")

        async def post(text: str, *, channel: str, thread_ts: str | None = None) -> None:
            say(text=text, channel=channel, thread_ts=thread_ts)

        asyncio.run(
            handle_slack_text(
                text,
                channel=channel,
                user=user,
                thread_ts=thread_ts,
                poster=_FunctionPoster(post),
                runner=lambda prompt: run_turn(prompt, model=runtime_config.claude_model),
                memory=memory or None,
            )
        )

    SocketModeHandler(app, config.app_token).start()


def _create_single_workspace_bolt_app(
    config: SlackConfig,
    *,
    token_verification_enabled: bool = True,
) -> Any:
    """Create a Bolt app that always uses the single-workspace bot token.

    Bolt auto-enables OAuth mode when SLACK_CLIENT_ID and SLACK_CLIENT_SECRET
    exist in the environment. This project stores those values for operator
    reference, but Socket Mode should use SLACK_BOT_TOKEN directly.
    """
    from slack_bolt import App

    with _without_slack_oauth_env():
        return App(
            token=config.bot_token,
            signing_secret=config.signing_secret,
            token_verification_enabled=token_verification_enabled,
        )


@contextmanager
def _without_slack_oauth_env() -> Iterator[None]:
    keys = ("SLACK_CLIENT_ID", "SLACK_CLIENT_SECRET")
    saved = {key: os.environ.pop(key) for key in keys if key in os.environ}
    try:
        yield
    finally:
        os.environ.update(saved)


class _FunctionPoster:
    def __init__(self, post: Callable[..., Awaitable[None]]) -> None:
        self._post = post

    async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
        result = self._post(text, channel=channel, thread_ts=thread_ts)
        if inspect.isawaitable(result):
            await result


def _one_line(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
