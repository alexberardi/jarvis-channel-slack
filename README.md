# jarvis-channel-slack

Talk to Jarvis from Slack. You create your own Slack app; this package keeps its connection open
from one of your nodes (Socket Mode) and hands Jarvis what linked people send. Nothing is opened
on your network: the app connects out to Slack.

It is a Jarvis Automation package (one daemon) that nodes install through Pantry.

## What it does

- **Direct messages.** Message the app and Jarvis answers there.
- **One channel.** In the channel you choose, Jarvis answers each message in its thread. What you
  say in that thread continues the same conversation.
- **Photos.** Attach a photo and ask about it. Up to four per message, 10 MB together.
- **Commands.** `/jarvis link CODE`, `/jarvis new`, `/jarvis node NAME`, `/jarvis notify on` or
  `off`, `/jarvis status`. Answers are shown only to you.
- **Reminders.** If you turn them on (`/jarvis notify on`), reminders, alarms and timers you set
  are also sent to your direct messages. Off by default.
- **Reactions.** ✅ when Jarvis has answered, ❌ when it could not. Once Jarvis has answered you,
  you also see 👀 while it works.

Jarvis never starts a conversation. The only thing it sends unasked is a reminder, to the person
who set it, and only if they turned that on.

Not yet: voice clips.

## Who gets an answer

- **Only linked accounts.** Each person links their own Slack account with a one-time code from
  the Jarvis app. Everyone else gets no sign that the app is listening: no reply, no reaction.
- **A linked Slack account acts as that Jarvis user.** Treat it like a login.
- **Replies never mention anyone**, whatever the text says: `<`, `>` and `&` are escaped, so
  nothing in a reply is an @-mention or `@channel`.
- The app answers only in direct messages, the one channel you set, and that channel's threads.

## Setup

Do this once, as the household's admin. Never paste a token into a chat.

1. **Create the app.** At <https://api.slack.com/apps> choose Create New App, From scratch, and
   pick your workspace.
2. **Socket Mode.** Turn Socket Mode on. Slack asks you to create an app-level token with the
   `connections:write` scope: copy it (it starts with `xapp-`).
3. **Scopes.** Under OAuth & Permissions, Bot Token Scopes, add: `chat:write`, `im:history`,
   `im:write`, `reactions:write`, `files:read`, `commands`, and for the channel `channels:history`
   (a public channel) or `groups:history` (a private one).
4. **Events.** Under Event Subscriptions turn events on and subscribe the bot to `message.im`,
   and to `message.channels` or `message.groups` for the channel.
5. **The command.** Under Slash Commands create `/jarvis`.
6. **Direct messages.** Under App Home, turn the Messages tab on and allow people to send
   messages and commands from it.
7. **Install the app** to your workspace and copy the Bot User OAuth Token (it starts with `xoxb-`).
8. **Install this package** on one node from the Jarvis app's store. A household runs it on one
   node only.
9. **Enter the settings** in the app, under the package's settings:
   - **Bot token**: the `xoxb-` token from step 7.
   - **App token**: the `xapp-` token from step 2.
   - **Jarvis channel ID**: optional. Open the channel's details in Slack; the ID is at the
     bottom. Invite the app to that channel (`/invite @YourApp`). Leave empty for direct messages only.
10. **Link yourself.** In the Jarvis app open your channels, make a link code for Slack, then in
    Slack run `/jarvis link CODE`. The code lasts ten minutes and works once.

## How it works

`daemons/slack/daemon.py` is an `IJarvisDaemon`: the node starts it once and restarts it if it
stops. It talks to Jarvis only through the SDK's `Channel` helper: who an account is, the chat
turn, the commands' answers and the reminders are all Jarvis's. This package only knows Slack.

It is the second channel, after [jarvis-channel-discord](https://github.com/alexberardi/jarvis-channel-discord),
and was written without changing Jarvis's core, the node or the SDK.

## Development

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install "jarvis-command-sdk @ git+https://github.com/alexberardi/jarvis-command-sdk@main" \
  "jarvis-developer-toolkit @ git+https://github.com/alexberardi/jarvis-developer-toolkit@main" \
  "slack_bolt>=1.20,<2" "aiohttp>=3.9,<4" pytest
jdt test . --install-deps
python -m pytest tests
```

The tests drive the bridge with stand-ins for Slack and for Jarvis; none connects anywhere.

## Credit

How a channel behaves (direct messages and one channel, a thread per conversation, the commands,
silence towards accounts that are not linked, opt-in reminders) follows the Discord bridge that
opticon454 contributed to jarvis-server (pull request 649).

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
