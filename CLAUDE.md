# jarvis-channel-slack

**Slack**: a chat-service channel for Jarvis Automation. One daemon (`daemons/slack/daemon.py`,
an `IJarvisDaemon`) that holds a Slack app's Socket Mode connection and talks to Jarvis through the SDK's
`Channel`. The contract it follows is in jarvis-server's `docs/CHANNELS.md`.

**Rules of the channel** (tests pin them):

- An account Jarvis says is not linked sees nothing: no reply and no reaction.
- Nothing sent mentions anyone (everything sent goes through `escape`).
- Answer only where asked: a direct message, the one configured channel, its threads.
- Service knowledge stays here. If Slack needs something Jarvis's core or the SDK lacks, the
  need is generic (Discord would want it too) or it does not belong there.

**Verify** (what CI runs: the toolkit's reusable `package-ci.yml`):

```bash
jdt test . --install-deps
python -m pytest tests
```

Tests use stand-ins for Slack and Jarvis; never connect to Slack in a test, never wait on
the wall clock.

**Merging.** An agent may squash-merge its own PR once CI has passed. Hold for the user's review
when the PR changes `jarvis_package.yaml`, touches `.github/**`, adds a secret, a scope or event,
or an outbound host, or deletes or weakens a test.

**Releases are the user's.** Never push a tag or submit to Pantry.
