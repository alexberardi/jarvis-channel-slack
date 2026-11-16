"""Slack channel for Jarvis.

Holds a Slack app's Socket Mode connection and hands Jarvis what linked people
send: in a direct message with the app, or in the one Jarvis channel, where
each message is answered in its thread. Jarvis decides who an account is and
what to answer (the SDK's ``Channel``); this file only knows Slack.

Accounts that are not linked get no sign that anything is here: no reply and
no reaction.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, List

import aiohttp
from jarvis_command_sdk import (
    Channel,
    ChannelImage,
    IJarvisDaemon,
    IJarvisSecret,
    JarvisSecret,
    JarvisStorage,
    Outgoing,
    clip_to_wav,
    split_reply,
)
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError

_log = logging.getLogger(__name__)
_storage = JarvisStorage("slack")

PROVIDER = "slack"
MAX_REPLY = 3000  # Slack asks for under 4000 characters; escaping can add some
MAX_IMAGES = 4  # what one Jarvis chat turn takes
MAX_IMAGE_BYTES = 10 << 20  # a message's photos together: the request to Jarvis is one line of at most 16 MiB, base64
MAX_CLIP_BYTES = 8 << 20  # a voice clip as Slack stores it; Jarvis takes two minutes
SEEN, DONE, FAILED = "eyes", "white_check_mark", "x"
ELSEWHERE = "I only work in direct messages and in the Jarvis channel."
UNREACHABLE = "I can't reach Jarvis right now. Try again in a moment."
USAGE = ("Try one of: `/jarvis link CODE`, `/jarvis new`, `/jarvis node NAME`, "
         "`/jarvis notify on` or `off`, `/jarvis status`.")
# The one argument each command takes, by the name Jarvis knows it.
ARGUMENT = {"link": "code", "new": None, "node": "name", "notify": "state", "status": None}
# Why a direct message can never arrive: asking again won't help.
GONE = {"user_not_found", "channel_not_found", "account_inactive", "user_disabled", "cannot_dm_bot"}


def escape(text: str) -> str:
    """Slack's own escaping. With it nothing in a reply is a mention (<@U…>, <!channel>) or a
    link Slack would build, whatever the text says."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Bridge:
    """What to do with a Slack event or command. It touches Slack only through the client it
    is handed and ``fetch``, so the tests drive it with stand-ins."""

    def __init__(self, channel: Channel, channel_id: str = "", bot_id: str = "",
                 fetch: Callable[[str], Awaitable[bytes | None]] | None = None) -> None:
        self.channel = channel
        self.channel_id = channel_id
        self.bot_id = bot_id
        self.fetch = fetch
        # Accounts Jarvis has answered since we started. Only they get the "seen" reaction
        # while a turn runs, so a stranger sees nothing at all.
        self.known: set[str] = set()

    async def on_message(self, event: dict, client) -> None:
        if event.get("bot_id") or event.get("subtype") not in (None, "file_share") or not event.get("user"):
            return
        where, ts = event["channel"], event["ts"]
        thread = event.get("thread_ts")
        if event.get("channel_type") == "im":
            place = f"dm:{where}"
        elif self.channel_id and where == self.channel_id:
            # Every message in the channel is answered in its thread: one conversation each.
            thread = thread or ts
            place = f"th:{thread}"
        else:
            return
        text = (event.get("text") or "").replace(f"<@{self.bot_id}>", "").strip()
        files = event.get("files") or []
        photos = [f for f in files if (f.get("mimetype") or "").startswith("image/")]
        # Slack's own recorder marks its clips; an audio file someone attached counts too.
        clips = [f for f in files if f.get("subtype") == "slack_audio" or (f.get("mimetype") or "").startswith("audio/")]
        if not text and not photos and not clips:
            return

        uid = event["user"]
        known = uid in self.known

        async def react(name: str) -> None:
            try:  # a nicety that must not cost the answer
                await client.reactions_add(channel=where, name=name, timestamp=ts)
            except SlackApiError as exc:
                _log.debug("slack: %s", exc)

        if known:
            await react(SEEN)
        images = await self._read_images(photos)
        audio = await self._read_clip(clips)
        if not text and not images and not audio:
            if known:  # all they sent was something we couldn't read
                await react(FAILED)
            return
        reply = await self.channel.message(uid, place, text, images, audio)
        if reply is None:
            if known:
                await react(FAILED)
            return
        if not reply.linked:
            self.known.discard(uid)
            return
        self.known.add(uid)
        if reply.heard:  # what Jarvis heard in a voice clip, above its answer, so a mishearing shows
            heard = escape(" ".join(reply.heard.split()))[:MAX_REPLY]
            await client.chat_postMessage(channel=where, text=f"> 🎤 {heard}", thread_ts=thread)
        for part in split_reply(reply.say, MAX_REPLY):
            if part:
                await client.chat_postMessage(channel=where, text=escape(part), thread_ts=thread)
        await react(FAILED if reply.failed else DONE)

    async def _read_images(self, files: list[dict]) -> list[ChannelImage]:
        out: list[ChannelImage] = []
        total = 0
        for f in files[:MAX_IMAGES]:
            url = f.get("url_private") or ""
            if not url or not self.fetch or total + int(f.get("size") or 0) > MAX_IMAGE_BYTES:
                continue
            data = await self.fetch(url)
            if not data or total + len(data) > MAX_IMAGE_BYTES:
                continue
            total += len(data)
            out.append(ChannelImage(data, f["mimetype"]))
        return out

    async def _read_clip(self, files: list[dict]) -> bytes:
        """The message's voice clip (its first) as the WAV Jarvis takes, or b"" without one."""
        for f in files[:1]:
            url = f.get("url_private") or ""
            if not url or not self.fetch or int(f.get("size") or 0) > MAX_CLIP_BYTES:
                break
            data = await self.fetch(url)
            if not data:
                break
            try:
                return await asyncio.to_thread(clip_to_wav, data)
            except ValueError as exc:
                _log.warning("slack: could not read a voice clip: %s", exc)
        return b""

    async def on_command(self, command: dict, respond) -> None:
        """A /jarvis command. Its answer is shown only to the person who asked."""
        words = (command.get("text") or "").split()
        name = words[0].lower() if words else ""
        where = command.get("channel_id") or ""
        if name not in ARGUMENT:
            text = USAGE
        elif command.get("channel_name") == "directmessage":
            text = await self._command(command, f"dm:{where}", name, words[1:])
        elif self.channel_id and where == self.channel_id:
            text = await self._command(command, f"ch:{where}", name, words[1:])
        else:
            text = ELSEWHERE
        await respond(text=escape(text), response_type="ephemeral")

    async def _command(self, command: dict, place: str, name: str, rest: list[str]) -> str:
        args = {ARGUMENT[name]: " ".join(rest)} if ARGUMENT[name] else {}
        reply = await self.channel.command(command["user_id"], place, name, **args)
        return reply.text if reply else UNREACHABLE

    async def remind(self, msg: Outgoing, client) -> None:
        """A reminder, to its owner's direct messages. Raising has Jarvis offer it again."""
        try:
            for part in split_reply(msg.text, MAX_REPLY):
                await client.chat_postMessage(channel=msg.external_user_id, text=escape(part))
        except SlackApiError as exc:
            if exc.response.get("error") not in GONE:
                raise
            _log.warning("slack: dropped a reminder that can't be delivered: %s", exc.response.get("error"))


class SlackChannel(IJarvisDaemon):
    @property
    def name(self) -> str:
        return "slack"

    @property
    def description(self) -> str:
        return "Talk to Jarvis from Slack: direct messages, one channel, photos, voice clips, commands and reminders"

    @property
    def required_secrets(self) -> List[IJarvisSecret]:
        return [
            JarvisSecret("SLACK_BOT_TOKEN", "Your Slack app's bot token (starts with xoxb-)", "integration",
                         "string", friendly_name="Bot token"),
            JarvisSecret("SLACK_APP_TOKEN", "Your Slack app's app-level token for Socket Mode (starts with xapp-)",
                         "integration", "string", friendly_name="App token"),
            JarvisSecret("SLACK_CHANNEL_ID", "ID of the one channel Jarvis answers in. "
                         "Leave empty for direct messages only.", "integration", "string",
                         required=False, is_sensitive=False, friendly_name="Jarvis channel ID"),
        ]

    async def run(self) -> None:
        bot_token = (_storage.get_secret("SLACK_BOT_TOKEN") or "").strip()
        app_token = (_storage.get_secret("SLACK_APP_TOKEN") or "").strip()
        if not bot_token or not app_token:
            raise RuntimeError("SLACK_BOT_TOKEN and SLACK_APP_TOKEN must both be set")
        channel = Channel(PROVIDER)
        app = AsyncApp(token=bot_token)
        me = await app.client.auth_test()  # also refuses a wrong bot token now, not at the first message

        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {bot_token}"}) as http:

            async def fetch(url: str) -> bytes | None:
                # Slack's file links answer a sign-in page, not the file, without the files:read scope.
                async with http.get(url, allow_redirects=False) as resp:
                    if resp.status != 200 or not resp.content_type.startswith(("image/", "audio/", "video/")):
                        _log.warning("slack: could not fetch a file (status %s, %s)", resp.status, resp.content_type)
                        return None
                    return await resp.read()  # its size was checked against the limit before asking

            bridge = Bridge(channel, (_storage.get_secret("SLACK_CHANNEL_ID") or "").strip(), me["user_id"], fetch)

            @app.event("message")
            async def on_message(event, client) -> None:
                await bridge.on_message(event, client)

            @app.command("/jarvis")
            async def on_command(ack, command, respond) -> None:
                await ack()
                await bridge.on_command(command, respond)

            async def remind(msg: Outgoing) -> None:
                await bridge.remind(msg, app.client)

            handler = AsyncSocketModeHandler(app, app_token)
            # Not start_async(): it can print to stdout, which is this process's line to the node.
            try:
                await handler.connect_async()
                await channel.deliver(remind)  # until the node cancels us
            finally:
                await handler.close_async()
