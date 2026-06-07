"""Slack runtime integration for the Intern."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import asyncio
import hmac
import hashlib
import inspect
import json
import os
import re
import time
from typing import Any, Awaitable, Callable, Iterator, Protocol, Sequence
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


class ThreadHistoryProvider(Protocol):
    async def fetch_thread_messages(self, *, channel: str, thread_ts: str, limit: int = 20) -> Sequence[dict[str, Any]]:
        """Fetch recent Slack thread messages, oldest to newest."""


class ThreadContextStore(Protocol):
    def recent_messages(self, *, channel: str, thread_ts: str, limit: int = 20) -> Sequence[dict[str, Any]]:
        """Return locally remembered thread messages, oldest to newest."""

    def append_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        user: str | None = None,
        bot: bool = False,
        ts: str | None = None,
    ) -> None:
        """Remember a Slack thread message."""


Logger = Callable[[str], None]

CAT_PHOTO_URL = "https://cataas.com/cat?type=square"


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


class SlackThreadContext:
    def __init__(self, *, max_messages_per_thread: int = 40) -> None:
        self.max_messages_per_thread = max_messages_per_thread
        self._messages: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def recent_messages(self, *, channel: str, thread_ts: str, limit: int = 20) -> Sequence[dict[str, Any]]:
        return tuple(self._messages.get((channel, thread_ts), ())[-limit:])

    def append_message(
        self,
        *,
        channel: str,
        thread_ts: str,
        text: str,
        user: str | None = None,
        bot: bool = False,
        ts: str | None = None,
    ) -> None:
        if not text.strip():
            return
        message: dict[str, Any] = {"text": text}
        if ts:
            message["ts"] = ts
        if bot:
            message["bot_id"] = "local-intern"
        elif user:
            message["user"] = user
        key = (channel, thread_ts)
        messages = self._messages.setdefault(key, [])
        messages.append(message)
        del messages[: max(0, len(messages) - self.max_messages_per_thread)]


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
    thread_history: ThreadHistoryProvider | None = None,
    thread_context: ThreadContextStore | None = None,
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

    thread_messages = _merge_thread_messages(
        await _fetch_thread_history(
            thread_history,
            channel=channel,
            thread_ts=thread_ts,
            logger=logger,
        ),
        _fetch_cached_thread_context(
            thread_context,
            channel=channel,
            thread_ts=thread_ts,
            logger=logger,
        ),
    )

    effective_thread_ts = thread_ts or event_ts
    if thread_context is not None and effective_thread_ts:
        _append_thread_context(
            thread_context,
            channel=channel,
            thread_ts=effective_thread_ts,
            text=text,
            user=user,
            ts=event_ts,
            logger=logger,
        )

    thread_messages = _merge_thread_messages(
        thread_messages,
        _fetch_cached_thread_context(
            thread_context,
            channel=channel,
            thread_ts=thread_ts,
            logger=logger,
        ),
    )
    _log_thread_context_summary(
        logger,
        channel=channel,
        thread_ts=thread_ts,
        message_count=len(thread_messages),
    )

    casual_reply = casual_intern_reply(text)
    if casual_reply and not _thread_has_work_context(thread_messages):
        result = TurnResult(text=casual_reply)
        await poster.post_message(casual_reply, channel=channel, thread_ts=thread_ts)
        if thread_context is not None and effective_thread_ts:
            _append_thread_context(
                thread_context,
                channel=channel,
                thread_ts=effective_thread_ts,
                text=casual_reply,
                bot=True,
                logger=logger,
            )
        if memory is not None:
            memory.append_event("slack_banter", _one_line(text))
        return result

    prompt = format_slack_prompt(
        text,
        channel=channel,
        user=user,
        thread_ts=thread_ts,
        thread_messages=thread_messages,
    )
    status_thread_ts = typing_thread_ts or thread_ts
    activity = activity or poster if _supports_activity(poster) else activity
    if activity is not None:
        await _best_effort_activity(activity.mark_online(), logger=logger)
    if activity is not None and status_thread_ts:
        await _best_effort_activity(
            activity.start_typing(channel=channel, thread_ts=status_thread_ts),
            logger=logger,
        )

    work_ack_sent = False
    if _should_send_work_ack(text, thread_messages):
        ack = _work_ack_text(text, thread_messages)
        await poster.post_message(
            ack,
            channel=channel,
            thread_ts=thread_ts,
        )
        work_ack_sent = True
        if thread_context is not None and effective_thread_ts:
            _append_thread_context(
                thread_context,
                channel=channel,
                thread_ts=effective_thread_ts,
                text=ack,
                bot=True,
                logger=logger,
            )

    try:
        if logger is not None:
            logger(
                "[slack] agent turn start "
                f"channel={channel} thread_ts={thread_ts or '-'} "
                f"context_messages={len(thread_messages)} work_ack_sent={work_ack_sent}"
            )
        result = await (runner or run_turn)(prompt)
    except Exception as exc:
        reply = f"Intern runtime error: {_one_line(str(exc), limit=300)}"
        try:
            await poster.post_message(reply, channel=channel, thread_ts=thread_ts)
            if thread_context is not None and effective_thread_ts:
                _append_thread_context(
                    thread_context,
                    channel=channel,
                    thread_ts=effective_thread_ts,
                    text=reply,
                    bot=True,
                    logger=logger,
                )
        finally:
            if activity is not None and status_thread_ts:
                await _best_effort_activity(
                    activity.stop_typing(channel=channel, thread_ts=status_thread_ts),
                    logger=logger,
                )
        if memory is not None:
            memory.append_event("slack_turn_error", _one_line(text))
        return TurnResult(text=reply)
    if logger is not None:
        logger(
            "[slack] agent turn complete "
            f"channel={channel} thread_ts={thread_ts or '-'} "
            f"reply_chars={len(result.text.strip())} cost_usd={result.total_cost_usd:.6f}"
        )

    reply = result.text.strip()
    if ensure_reply and not reply:
        reply = "I got your message, but the Intern runtime did not return a reply."
        result.text = reply
    if reply:
        try:
            await poster.post_message(reply, channel=channel, thread_ts=thread_ts)
            if thread_context is not None and effective_thread_ts:
                _append_thread_context(
                    thread_context,
                    channel=channel,
                    thread_ts=effective_thread_ts,
                    text=reply,
                    bot=True,
                    logger=logger,
                )
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
    thread_messages: Sequence[dict[str, Any]] | None = None,
) -> str:
    parts = [f"Slack channel: {channel}"]
    if user:
        parts.append(f"Slack user: {user}")
    if thread_ts:
        parts.append(f"Slack thread_ts: {thread_ts}")
    parts.append("")
    parts.append("Slack response guidance:")
    parts.append("- Answer the latest user message directly.")
    parts.append("- Sound like a barely-trained intern in Slack: short, eager, specific, no assistant-y filler.")
    parts.append("- One Slack bubble by default; do not write a mini project brief.")
    parts.append("- No markdown headings, bold section labels, numbered plans, or multi-question questionnaires unless asked.")
    parts.append("- For pure banter, it is okay to be dumb-funny before being useful.")
    parts.append(
        "- For casual preference/opinion questions, pick a concrete answer with a short reason; "
        "do not dodge with generic flattery."
    )
    parts.append("- If asked what you can do, do a joking shrug first, then name the useful work in one line.")
    parts.append("- If scoping a small ticket, give the read in one short paragraph with at most one tiny question.")
    parts.append(
        "- If the thread context already contains a concrete small code/PR request and the latest user message is "
        "affirming it (yes, ya, yep, do it, sounds good), treat that as permission to proceed."
    )
    parts.append(
        "- For obvious tiny repo tasks like a README text change, do not keep asking for more context; make a "
        "reasonable small change, then open a draft PR."
    )
    parts.append(
        "- If the user asks where the PR is after requesting work in this thread, continue/check the work from "
        "the thread context instead of asking which PR."
    )
    parts.append(
        "- If answering requires inspecting the codebase, delegate to the Agent tool with "
        "`subagent_type: coder`; CODER must use Perseus before broad file search when it can."
    )
    parts.append("- Keep casual replies to 1-2 short sentences.")
    parts.append("- Do not dump a long capability list or repeat sections unless the user asked for detail.")
    if thread_messages:
        parts.append("")
        parts.append("Recent Slack thread context (oldest to newest):")
        parts.extend(_format_thread_messages(thread_messages))
    parts.append("")
    parts.append("User message:")
    parts.append(text)
    return "\n".join(parts)


def casual_intern_reply(text: str) -> str | None:
    """Return deterministic low-stakes Slack banter without spending a model turn."""
    normalized = _normalize_casual_text(text)
    if normalized in {"hi", "hello", "hey", "yo", "sup"}:
        return f"hi\n{CAT_PHOTO_URL}"

    if re.search(r"\bwhat\b.*\b(can you|can u|do you|do u)\b.*\b(do|doing)\b", normalized):
        return (
            "uhhhhh idk make u some coffee?\n"
            "also tickets, code, tests, and PRs when someone points me at the mess"
        )

    if normalized in {"why is prod down", "whys prod down", "why prod down", "prod down"}:
        return "uhhh mb guys\nI can look, but I am not touching prod without on-call"

    return None


def _thread_has_work_context(thread_messages: Sequence[dict[str, Any]]) -> bool:
    thread_text = _thread_text(thread_messages)
    normalized = _normalize_casual_text(thread_text)
    return _looks_like_work_request(thread_text) or bool(re.search(r"\b(ticket|issue|linear|tot\s*\d+)\b", normalized))


def _should_send_work_ack(text: str, thread_messages: Sequence[dict[str, Any]]) -> bool:
    normalized = _normalize_casual_text(text)
    if _looks_like_pr_status_question(normalized):
        return False
    if _looks_like_work_request(text):
        return True
    if _looks_like_followup_work_request(normalized):
        return _thread_has_work_context(thread_messages)
    if normalized in {"yes", "yeah", "yep", "ya", "ya bro", "yes bro", "sure", "do it", "go for it"}:
        return _thread_has_work_context(thread_messages)
    return False


def _work_ack_text(text: str, thread_messages: Sequence[dict[str, Any]]) -> str:
    combined = f"{_thread_text(thread_messages)}\n{text}".lower()
    normalized = _normalize_casual_text(combined)
    if "pr" in normalized or "pull request" in normalized or re.search(r"\bopen\b.*\bp\b", normalized):
        return "on it, I'll make the change and open a draft PR"
    return "on it, I'll work it and report back here"


def _looks_like_pr_status_question(normalized: str) -> bool:
    return bool(re.search(r"\b(where|wheres|where is|status|link)\b.*\b(pr|pull request)\b", normalized))


def _looks_like_work_request(text: str) -> bool:
    normalized = _normalize_casual_text(text)
    return bool(
        re.search(r"\b(open|create|make|raise)\b.*\b(pr|pull request)\b", normalized)
        or re.search(r"\b(add|change|update|fix|implement|edit)\b.*\b(readme|file|code|test|bug|ticket)\b", normalized)
        or re.search(r"\b(readme|repo|branch|commit)\b.*\b(change|update|fix|pr|pull request)\b", normalized)
    )


def _looks_like_followup_work_request(normalized: str) -> bool:
    return bool(
        re.search(r"\b(fix|do|take|make|open)\b.*\b(those|that|it|changes|change|p|pr)\b", normalized)
        or re.search(r"\b(can u|can you)\b.*\b(fix|do|take|open)\b", normalized)
    )


async def _fetch_thread_history(
    provider: ThreadHistoryProvider | None,
    *,
    channel: str,
    thread_ts: str | None,
    logger: Logger | None,
) -> Sequence[dict[str, Any]]:
    if provider is None or not thread_ts:
        if logger is not None and thread_ts:
            logger(f"[slack] thread history skipped source=slack reason=no_provider channel={channel} thread_ts={thread_ts}")
        return ()
    try:
        messages = await provider.fetch_thread_messages(channel=channel, thread_ts=thread_ts)
    except Exception as exc:
        if logger is not None:
            logger(
                "[slack] thread history warning "
                f"source=slack channel={channel} thread_ts={thread_ts} "
                f"error={_one_line(str(exc), limit=240)!r}"
            )
        return ()
    if logger is not None:
        logger(f"[slack] thread history source=slack count={len(messages)} channel={channel} thread_ts={thread_ts}")
    return messages


def _fetch_cached_thread_context(
    store: ThreadContextStore | None,
    *,
    channel: str,
    thread_ts: str | None,
    logger: Logger | None,
) -> Sequence[dict[str, Any]]:
    if store is None or not thread_ts:
        if logger is not None and thread_ts:
            logger(f"[slack] thread context skipped source=cache reason=no_store channel={channel} thread_ts={thread_ts}")
        return ()
    try:
        messages = store.recent_messages(channel=channel, thread_ts=thread_ts)
    except Exception as exc:
        if logger is not None:
            logger(
                "[slack] thread cache read warning "
                f"source=cache channel={channel} thread_ts={thread_ts} "
                f"error={_one_line(str(exc), limit=240)!r}"
            )
        return ()
    if logger is not None:
        logger(f"[slack] thread context source=cache count={len(messages)} channel={channel} thread_ts={thread_ts}")
    return messages


def _append_thread_context(
    store: ThreadContextStore,
    *,
    channel: str,
    thread_ts: str,
    text: str,
    user: str | None = None,
    bot: bool = False,
    ts: str | None = None,
    logger: Logger | None,
) -> None:
    try:
        store.append_message(
            channel=channel,
            thread_ts=thread_ts,
            text=text,
            user=user,
            bot=bot,
            ts=ts,
        )
    except Exception as exc:
        if logger is not None:
            logger(f"[slack] thread cache write warning: {_one_line(str(exc), limit=240)}")
        return
    if logger is not None:
        speaker = "bot" if bot else "user"
        logger(
            "[slack] thread context append "
            f"source=cache speaker={speaker} channel={channel} thread_ts={thread_ts} "
            f"text_chars={len(text)}"
        )


def _log_thread_context_summary(
    logger: Logger | None,
    *,
    channel: str,
    thread_ts: str | None,
    message_count: int,
) -> None:
    if logger is None:
        return
    logger(
        "[slack] thread context merged "
        f"channel={channel} thread_ts={thread_ts or '-'} "
        f"context_messages={message_count}"
    )


def _merge_thread_messages(
    first: Sequence[dict[str, Any]],
    second: Sequence[dict[str, Any]],
) -> Sequence[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen = set()
    for message in (*first, *second):
        identity = _thread_message_identity(message)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(dict(message))
    return tuple(merged)


def _thread_message_identity(message: dict[str, Any]) -> tuple[str, str, str]:
    ts = str(message.get("ts") or "")
    speaker = "bot" if message.get("bot_id") or message.get("bot_profile") else str(message.get("user") or "user")
    text = str(message.get("text") or "")
    return (ts, speaker, text)


def _format_thread_messages(messages: Sequence[dict[str, Any]]) -> list[str]:
    return [f"- {_format_thread_message(message)}" for message in messages[-20:]]


def _format_thread_message(message: dict[str, Any]) -> str:
    speaker = "Intern" if message.get("bot_id") or message.get("bot_profile") else str(message.get("user") or "user")
    text = _one_line(str(message.get("text") or ""), limit=500)
    return f"{speaker}: {text}"


def _thread_text(messages: Sequence[dict[str, Any]]) -> str:
    return "\n".join(str(message.get("text") or "") for message in messages)


def _normalize_casual_text(text: str) -> str:
    text = re.sub(r"<@[A-Z0-9]+>", " ", text)
    text = re.sub(r"[^a-zA-Z0-9\s]", " ", text)
    return " ".join(text.lower().split())


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
    target_cwd = str(runtime_config.target_repo_path) if runtime_config.target_repo_path else None
    memory = InternMemory(runtime_config.memory_path)
    activity = _BoltActivity(app.client)
    thread_history = _BoltThreadHistory(app.client)
    thread_context = SlackThreadContext()

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
                thread_history=thread_history,
                thread_context=thread_context,
                runner=lambda prompt: run_turn(
                    prompt,
                    cwd=target_cwd,
                    model=runtime_config.claude_model,
                    permission_mode=runtime_config.permission_mode,
                    git_author_name=runtime_config.git_author_name,
                    git_author_email=runtime_config.git_author_email,
                    logger=_log,
                ),
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
                thread_history=thread_history,
                thread_context=thread_context,
                runner=lambda prompt: run_turn(
                    prompt,
                    cwd=target_cwd,
                    model=runtime_config.claude_model,
                    permission_mode=runtime_config.permission_mode,
                    git_author_name=runtime_config.git_author_name,
                    git_author_email=runtime_config.git_author_email,
                    logger=_log,
                ),
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


class _BoltThreadHistory:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def fetch_thread_messages(
        self,
        *,
        channel: str,
        thread_ts: str,
        limit: int = 20,
    ) -> Sequence[dict[str, Any]]:
        result = await asyncio.to_thread(
            self._client.conversations_replies,
            channel=channel,
            ts=thread_ts,
            limit=limit,
        )
        ok = _slack_response_get(result, "ok", True)
        if not ok:
            error = _slack_response_get(result, "error", "unknown_error")
            raise RuntimeError(
                f"Slack thread fetch failed: error={error} response_type={type(result).__name__} "
                "hint=check bot scopes for conversations.replies history access"
            )
        messages = _slack_response_get(result, "messages", ()) or ()
        return tuple(message for message in messages if isinstance(message, dict))


def _slack_response_get(response: Any, key: str, default: Any = None) -> Any:
    if isinstance(response, Mapping):
        return response.get(key, default)
    data = getattr(response, "data", None)
    if isinstance(data, Mapping):
        return data.get(key, default)
    getter = getattr(response, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            try:
                return getter(key)
            except Exception:
                return default
        except Exception:
            return default
    return default


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
