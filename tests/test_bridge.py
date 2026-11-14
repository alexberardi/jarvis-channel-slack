"""The bridge against stand-ins for Slack and for Jarvis: who gets an answer, where, and
that an account Jarvis doesn't know sees no sign of the app."""

import asyncio

import pytest
from jarvis_command_sdk import ChannelReply, CommandReply, Outgoing
from slack_sdk.errors import SlackApiError

from daemons.slack.daemon import MAX_IMAGE_BYTES, MAX_REPLY, USAGE, Bridge, SlackChannel, escape

JARVIS_CHANNEL = "C500"


class FakeJarvis:
    """Channel's stand-in: answers from a script and records what it was asked."""

    def __init__(self, reply=ChannelReply(linked=True, say="hi")):
        self.reply, self.asked, self.commands = reply, [], []

    async def message(self, uid, place, text="", images=()):
        self.asked.append((uid, place, text, list(images)))
        return self.reply

    async def command(self, uid, place, name, **args):
        self.commands.append((uid, place, name, args))
        return CommandReply(text="done")


class FakeSlack:
    def __init__(self, post_error=None):
        self.posts, self.reactions, self.post_error = [], [], post_error

    async def chat_postMessage(self, channel, text, thread_ts=None):
        if self.post_error:
            raise SlackApiError("refused", {"error": self.post_error})
        self.posts.append((channel, text, thread_ts))

    async def reactions_add(self, channel, name, timestamp):
        self.reactions.append(name)


def dm(text="hello", **more):
    return {"user": "U7", "channel": "D42", "channel_type": "im", "ts": "100.1", "text": text, **more}


def in_channel(text="hello", **more):
    return {"user": "U7", "channel": JARVIS_CHANNEL, "channel_type": "channel", "ts": "200.1", "text": text, **more}


def bridge(jarvis=None, channel_id=JARVIS_CHANNEL, files=None):
    async def fetch(url):
        return (files or {}).get(url)

    return Bridge(jarvis or FakeJarvis(), channel_id, "UBOT", fetch)


def run(coro):
    return asyncio.run(coro)


def test_a_direct_message_is_answered_where_it_was_sent():
    b, slack = bridge(), FakeSlack()
    run(b.on_message(dm("what's for dinner"), slack))
    assert b.channel.asked == [("U7", "dm:D42", "what's for dinner", [])]
    assert slack.posts == [("D42", "hi", None)]
    assert slack.reactions == ["white_check_mark"]


def test_a_stranger_sees_no_sign_of_the_app():
    b, slack = bridge(FakeJarvis(ChannelReply(linked=False))), FakeSlack()
    run(b.on_message(in_channel(), slack))
    assert b.channel.asked  # Jarvis decides who is linked, so it is asked
    assert (slack.posts, slack.reactions) == ([], [])


def test_only_someone_jarvis_has_answered_sees_it_working():
    b, slack = bridge(), FakeSlack()
    run(b.on_message(dm(), slack))
    run(b.on_message(dm(), slack))
    assert slack.reactions == ["white_check_mark", "eyes", "white_check_mark"]
    b.channel.reply = ChannelReply(linked=False)  # unlinked since: a stranger again
    run(b.on_message(dm(), slack))
    assert b.known == set()


def test_a_message_in_the_channel_is_answered_in_its_thread():
    b, slack = bridge(), FakeSlack()
    run(b.on_message(in_channel("<@UBOT> plan the week"), slack))
    run(b.on_message(in_channel("and the weekend", ts="200.5", thread_ts="200.1"), slack))
    assert [a[1:3] for a in b.channel.asked] == [("th:200.1", "plan the week"), ("th:200.1", "and the weekend")]
    assert [p[2] for p in slack.posts] == ["200.1", "200.1"]


def test_other_channels_bots_edits_and_empty_messages_are_ignored():
    b, slack = bridge(), FakeSlack()
    for event in (in_channel(channel="C77"), dm(bot_id="B1"), dm(subtype="message_changed"), dm(""),
                  dm("<@UBOT>"), dm("", files=[{"mimetype": "audio/ogg", "url_private": "u", "size": 1}])):
        run(b.on_message(event, slack))
    run(bridge(channel_id="").on_message(in_channel(), slack))
    assert b.channel.asked == [] and slack.posts == []


def test_photos_go_along_and_the_size_limit_leaves_one_out():
    b = bridge(files={"a": b"jpeg", "c": b"png!"})
    files = [{"mimetype": "image/jpeg", "url_private": "a", "size": 4},
             {"mimetype": "image/png", "url_private": "big", "size": MAX_IMAGE_BYTES},
             {"mimetype": "image/png", "url_private": "c", "size": 4},
             {"mimetype": "image/png", "url_private": "missing", "size": 4}]
    run(b.on_message(dm("", subtype="file_share", files=files), FakeSlack()))
    assert [(i.data, i.mime) for i in b.channel.asked[0][3]] == [(b"jpeg", "image/jpeg"), (b"png!", "image/png")]


def test_a_reply_is_split_and_can_mention_nobody():
    b, slack = bridge(FakeJarvis(ChannelReply(linked=True, say="<!channel> <@U1> " + "word " * 900))), FakeSlack()
    run(b.on_message(dm(), slack))
    assert len(slack.posts) == 2 and all(len(p[1]) <= MAX_REPLY + 20 for p in slack.posts)
    assert slack.posts[0][1].startswith("&lt;!channel&gt; &lt;@U1&gt;")
    assert escape("a & <b>") == "a &amp; &lt;b&gt;"


def test_a_failed_turn_says_why_and_jarvis_out_of_reach_says_nothing():
    b, slack = bridge(FakeJarvis(ChannelReply(linked=True, say="The model is not running.", failed=True))), FakeSlack()
    run(b.on_message(dm(), slack))
    assert (slack.posts[0][1], slack.reactions) == ("The model is not running.", ["x"])
    b.channel.reply, slack = None, FakeSlack()
    run(b.on_message(dm(), slack))
    assert (slack.posts, slack.reactions) == ([], ["eyes", "x"])


def command(text, channel_id="D42", channel_name="directmessage"):
    said = []

    async def respond(text, response_type):
        said.append((text, response_type))

    return {"text": text, "user_id": "U7", "channel_id": channel_id, "channel_name": channel_name}, respond, said


def test_a_command_is_answered_privately():
    b = bridge()
    cmd, respond, said = command("link ABCD2345")
    run(b.on_command(cmd, respond))
    cmd2, respond2, _ = command("Notify on", JARVIS_CHANNEL, "jarvis")
    run(b.on_command(cmd2, respond2))
    cmd3, respond3, _ = command("status")
    run(b.on_command(cmd3, respond3))
    assert b.channel.commands == [("U7", "dm:D42", "link", {"code": "ABCD2345"}),
                                  ("U7", f"ch:{JARVIS_CHANNEL}", "notify", {"state": "on"}),
                                  ("U7", "dm:D42", "status", {})]
    assert said == [("done", "ephemeral")]


def test_an_unknown_command_gets_the_usage_and_elsewhere_is_refused():
    b = bridge()
    for text, where, name, want in (("", "D42", "directmessage", escape(USAGE)), ("dance", "D42", "directmessage", escape(USAGE)),
                                    ("new", "C77", "random", "I only work in direct messages and in the Jarvis channel.")):
        cmd, respond, said = command(text, where, name)
        run(b.on_command(cmd, respond))
        assert said == [(want, "ephemeral")]
    assert b.channel.commands == []


def test_a_reminder_goes_to_its_owner_and_one_that_can_never_arrive_is_dropped():
    b, slack = bridge(), FakeSlack()
    run(b.remind(Outgoing("1", "U7", "Take the bins out"), slack))
    assert slack.posts == [("U7", "Take the bins out", None)]
    run(b.remind(Outgoing("2", "U7", "x"), FakeSlack(post_error="account_inactive")))  # dropped: no raise
    with pytest.raises(SlackApiError):  # anything else is offered again
        run(b.remind(Outgoing("3", "U7", "x"), FakeSlack(post_error="ratelimited")))


def test_the_daemon_declares_its_secrets():
    secrets = {s.key: s for s in SlackChannel().required_secrets}
    assert secrets["SLACK_BOT_TOKEN"].is_sensitive and secrets["SLACK_APP_TOKEN"].is_sensitive
    assert not secrets["SLACK_CHANNEL_ID"].required and not secrets["SLACK_CHANNEL_ID"].is_sensitive
