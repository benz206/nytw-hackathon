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


class ActivityIndicator(Protocol):
    async def mark_online(self) -> None:
        """Mark the Intern active/online in Slack."""

    async def start_typing(self, *, channel: str, thread_ts: str) -> None:
        """Show a Slack assistant typing/status indicator."""

    async def stop_typing(self, *, channel: str, thread_ts: str) -> None:
        """Clear a Slack assistant typing/status indicator."""


Logger = Callable[[str], None]


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
        lines.append(
            "Bot online indicator: enable Slack app Bot User > Always Show My Bot as Online; "
            "Socket Mode cannot force the green dot with users.setPresence."
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

    async def mark_online(self) -> None:
        await asyncio.to_thread(
            self._post_json,
            "https://slack.com/api/users.setPresence",
            {"presence": "auto"},
            failure_label="Slack presence update failed",
        )

    async def start_typing(self, *, channel: str, thread_ts: str) -> None:
        await asyncio.to_thread(
            self._post_json,
            "https://slack.com/api/assistant.threads.setStatus",
            {
                "channel_id": channel,
                "thread_ts": thread_ts,
                "status": "is typing...",
                "loading_messages": ["is typing..."],
            },
            failure_label="Slack typing indicator failed",
        )

    async def stop_typing(self, *, channel: str, thread_ts: str) -> None:
        await asyncio.to_thread(
            self._post_json,
            "https://slack.com/api/assistant.threads.setStatus",
            {"channel_id": channel, "thread_ts": thread_ts, "status": ""},
            failure_label="Slack typing indicator clear failed",
        )

    def _post_json(self, url: str, payload: dict[str, Any], *, failure_label: str = "Slack post failed") -> None:
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
            raise RuntimeError(f"{failure_label}: {exc}") from exc

        if not data.get("ok"):
            raise RuntimeError(f"{failure_label}: {data.get('error', 'unknown_error')}")


class PrintPoster:
    """Poster for local dry runs."""

    async def post_message(self, text: str, *, channel: str, thread_ts: str | None = None) -> None:
        target = channel if thread_ts is None else f"{channel} thread {thread_ts}"
        print(f"[slack dry-run -> {target}] {text}")

    async def mark_online(self) -> None:
        print("[slack dry-run] presence=auto")

    async def start_typing(self, *, channel: str, thread_ts: str) -> None:
        print(f"[slack dry-run] typing on {channel} thread {thread_ts}")

    async def stop_typing(self, *, channel: str, thread_ts: str) -> None:
        print(f"[slack dry-run] clear typing on {channel} thread {thread_ts}")


async def handle_slack_text(
    text: str,
    *,
    channel: str,
    user: str | None = None,
    thread_ts: str | None = None,
    event_type: str = "message",
    event_ts: str | None = None,
    typing_thread_ts: str | None = None,
    poster: Poster,
    activity: ActivityIndicator | None = None,
    runner: Runner | None = None,
    memory: InternMemory | None = None,
    logger: Logger | None = None,
    ensure_reply: bool = True,
) -> TurnResult:
    """Run one Intern turn for a Slack message and post the reply."""
    if logger is not None:
        logger(
            _format_received_log(
                event_type,
                channel=channel,
                user=user,
                thread_ts=thread_ts,
                event_ts=event_ts,
                text=text,
            )
        )

    prompt = format_slack_prompt(text, channel=channel, user=user, thread_ts=thread_ts)
    status_thread_ts = typing_thread_ts or thread_ts
    activity = activity or poster if _supports_activity(poster) else activity
    if activity is not None:
        await _best_effort_activity(activity.mark_online(), logger=logger)
    if activity is not None and status_thread_ts:
        await _best_effort_activity(
            activity.start_typing(channel=channel, thread_ts=status_thread_ts),
            logger=logger,
        )

    try:
        result = await (runner or run_turn)(prompt)
    except Exception as exc:
        reply = f"Intern runtime error: {_one_line(str(exc), limit=300)}"
        try:
            await poster.post_message(reply, channel=channel, thread_ts=thread_ts)
        finally:
            if activity is not None and status_thread_ts:
                await _best_effort_activity(
                    activity.stop_typing(channel=channel, thread_ts=status_thread_ts),
                    logger=logger,
                )
        if memory is not None:
            memory.append_event("slack_turn_error", _one_line(text))
        return TurnResult(text=reply)

    reply = result.text.strip()
    if ensure_reply and not reply:
        reply = "I got your message, but the Intern runtime did not return a reply."
        result.text = reply
    if reply:
        try:
            await poster.post_message(reply, channel=channel, thread_ts=thread_ts)
        finally:
            if activity is not None and status_thread_ts:
                await _best_effort_activity(
                    activity.stop_typing(channel=channel, thread_ts=status_thread_ts),
                    logger=logger,
                )
    elif activity is not None and status_thread_ts:
        await _best_effort_activity(
            activity.stop_typing(channel=channel, thread_ts=status_thread_ts),
            logger=logger,
        )
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
    parts.append("Slack response guidance:")
    parts.append("- Answer the latest user message directly.")
    parts.append("- Sound like a real intern in Slack: short, eager, specific, no assistant-y filler.")
    parts.append(
        "- For casual preference/opinion questions, pick a concrete answer with a short reason; "
        "do not dodge with generic flattery."
    )
    parts.append("- Keep casual replies to 1-2 short sentences.")
    parts.append("- Do not dump a long capability list or repeat sections unless the user asked for detail.")
    parts.append("")
    parts.append("User message:")
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
    """Start a Slack Socket Mode app for mentions, DMs, and thread replies."""
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
    activity = _BoltActivity(app.client)

    _log("starting Slack Socket Mode listener")
    _best_effort_sync_activity(lambda: app.client.users_setPresence(presence="auto"), logger=_log)

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
                event_type="app_mention",
                event_ts=event.get("ts"),
                typing_thread_ts=thread_ts,
                poster=_FunctionPoster(post),
                activity=activity,
                runner=lambda prompt: run_turn(prompt, model=runtime_config.claude_model),
                memory=memory or None,
                logger=_log,
            )
        )

    @app.event("message")
    def handle_message(event, say):  # type: ignore[no-untyped-def]
        if not should_reply_to_message_event(event):
            return
        channel = event["channel"]
        user = event.get("user")
        text = event.get("text", "")
        thread_ts = reply_thread_ts_for_message_event(event)
        typing_thread_ts = event.get("thread_ts") or event.get("ts")

        async def post(text: str, *, channel: str, thread_ts: str | None = None) -> None:
            say(text=text, channel=channel, thread_ts=thread_ts)

        asyncio.run(
            handle_slack_text(
                text,
                channel=channel,
                user=user,
                thread_ts=thread_ts,
                event_type=message_event_name(event),
                event_ts=event.get("ts"),
                typing_thread_ts=typing_thread_ts,
                poster=_FunctionPoster(post),
                activity=activity,
                runner=lambda prompt: run_turn(prompt, model=runtime_config.claude_model),
                memory=memory or None,
                logger=_log,
            )
        )

    SocketModeHandler(app, config.app_token).start()


def should_reply_to_message_event(event: dict[str, Any]) -> bool:
    """Reply to every human DM and every human-authored thread message."""
    if event.get("bot_id") or event.get("bot_profile"):
        return False
    if not event.get("user"):
        return False
    subtype = event.get("subtype")
    if subtype not in (None, "file_share"):
        return False
    if event.get("channel_type") in ("im", "mpim"):
        return True
    return bool(event.get("thread_ts"))


def reply_thread_ts_for_message_event(event: dict[str, Any]) -> str | None:
    thread_ts = event.get("thread_ts")
    if thread_ts:
        return str(thread_ts)
    return None


def message_event_name(event: dict[str, Any]) -> str:
    channel_type = event.get("channel_type")
    if channel_type == "im":
        return "message.im"
    if channel_type == "mpim":
        return "message.mpim"
    if channel_type == "group":
        return "message.groups"
    return "message.channels"


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


class _BoltActivity:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def mark_online(self) -> None:
        await asyncio.to_thread(self._client.users_setPresence, presence="auto")

    async def start_typing(self, *, channel: str, thread_ts: str) -> None:
        await asyncio.to_thread(
            self._client.api_call,
            "assistant.threads.setStatus",
            json={
                "channel_id": channel,
                "thread_ts": thread_ts,
                "status": "is typing...",
                "loading_messages": ["is typing..."],
            },
        )

    async def stop_typing(self, *, channel: str, thread_ts: str) -> None:
        await asyncio.to_thread(
            self._client.api_call,
            "assistant.threads.setStatus",
            json={"channel_id": channel, "thread_ts": thread_ts, "status": ""},
        )


async def _best_effort_activity(awaitable: Awaitable[Any], *, logger: Logger | None = None) -> None:
    try:
        result = await awaitable
    except Exception as exc:
        if logger is not None:
            logger(f"[slack] activity warning: {_one_line(str(exc), limit=240)}")
        return
    if isinstance(result, dict) and not result.get("ok", True) and logger is not None:
        logger(f"[slack] activity warning: {result.get('error', 'unknown_error')}")


def _supports_activity(value: Any) -> bool:
    return all(hasattr(value, name) for name in ("mark_online", "start_typing", "stop_typing"))


def _best_effort_sync_activity(callback: Callable[[], Any], *, logger: Logger | None = None) -> None:
    try:
        result = callback()
    except Exception as exc:
        if logger is not None:
            logger(f"[slack] activity warning: {_one_line(str(exc), limit=240)}")
        return
    if isinstance(result, dict) and not result.get("ok", True) and logger is not None:
        logger(f"[slack] activity warning: {result.get('error', 'unknown_error')}")


def _format_received_log(
    event_type: str,
    *,
    channel: str,
    user: str | None,
    thread_ts: str | None,
    event_ts: str | None,
    text: str,
) -> str:
    details = [f"type={event_type}", f"channel={channel}"]
    if user:
        details.append(f"user={user}")
    if thread_ts:
        details.append(f"thread_ts={thread_ts}")
    if event_ts:
        details.append(f"event_ts={event_ts}")
    details.append(f"text={_one_line(text, limit=220)!r}")
    return "[slack] received " + " ".join(details)


def _log(message: str) -> None:
    print(message, flush=True)


def _one_line(text: str, limit: int = 160) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."
