---
sidebar_position: 5
title: "Autonomy"
description: "Profile-scoped proactive Hermes behavior in the messaging gateway — watch items, proactive updates, and hot-path awareness"
---

# Autonomy

Autonomy lets Hermes do more than wait for the next user message. When the messaging gateway is running, Hermes can extract things to watch, revisit them in the background, draft follow-ups, and surface useful updates back into the configured home chat in a natural voice.

Autonomy is **profile-scoped**. Each Hermes profile has its own autonomy config, state, watch list, home channel, and delivery history.

:::info
Autonomy runs only in the **gateway**. CLI-only sessions do not run the background autonomy loop.
:::

## What autonomy does

The gateway autonomy runtime has three main behaviors:

- **Post-turn intake** — after a normal user conversation turn, Hermes extracts durable watch items such as “keep an eye on OpenAI updates” or “follow up on this deadline next week”.
- **Background supervision** — on an interval, Hermes checks active watch items, does lightweight research with the allowed toolsets, and can create findings or drafts.
- **Natural surfacing** — when something is worth showing the user, Hermes sends a normal assistant message into the configured home chat or exposes the update on the next user turn through hidden context injection.

The user never sees internal background transcripts, queue state, or tool spam.

## Watch registration modes

Autonomy can register new watch items in three different ways, controlled by `autonomy.extract_behavior`:

- **`hermes`** — the hot path registers explicit open-ended monitoring requests immediately. Post-turn extraction is skipped.
- **`auto_extract`** — Hermes acknowledges the request naturally and a hidden post-turn extractor registers the watch after the turn.
- **`both`** — Hermes can register explicit watches immediately, while post-turn extraction still acts as a fallback for implied or missed watch items.

In `both` mode, Hermes skips post-turn extraction for a turn if it already used the direct watch-registration path, which prevents duplicate watch items.

## Autonomy vs cron

Use **autonomy** for open-ended monitoring:

- “Keep an eye on OpenAI news.”
- “Watch this repo for important changes.”
- “Let me know if this topic develops.”

Use **cron** for exact time-based scheduling:

- “Every weekday at 9am send me a brief.”
- “In 30 minutes remind me to check the build.”
- “Run this task every 6 hours.”

Autonomy is stateful and ongoing. Cron is scheduled and deterministic.

See [Scheduled tasks (cron)](/docs/user-guide/features/cron) for the cron side of the split.

## How proactive delivery works

Autonomy does not blindly push every background result immediately. Instead:

1. Hermes stores findings, drafts, and inbox items in the profile state database.
2. A deterministic outbox pass decides whether to surface them now.
3. If Hermes should proactively speak first, it synthesizes one natural message and sends it to the profile’s home chat.
4. If the user speaks first later, Hermes can see recent autonomy deltas through hidden turn context without polluting the visible transcript.

This keeps delivery natural while ensuring autonomy findings are still recoverable even if they are not pushed immediately.

## Setup

Add an `autonomy:` block to the active profile’s `config.yaml`:

```yaml
autonomy:
  enabled: true
  interval_seconds: 120
  poll_interval_seconds: 10
  max_iterations: 20
  max_recent_messages: 8

  home_platform: telegram
  home_chat_id: "123456789"
  home_thread_id: ""
  home_chat_type: dm

  extract_behavior: both
  infer_level: implied
  proactivity_level: utility
  social_rate_limit_hours: 24
  resolved_retention_days: 45

  inject_on_change_only: true
  new_session_injection: important_only

  allow_drafts: true
  allow_final_external_actions: false

  allowed_toolsets:
    - search
    - web
    - session_search
    - cron_read

  quiet_hours:
    enabled: false
    start: "22:00"
    end: "08:00"
```

Then either:

- set `home_platform`, `home_chat_id`, and optional `home_thread_id` in config, or
- open the chat you want Hermes to use and send `/sethome`

Restart the gateway after changing config:

```bash
hermes gateway run
```

## Home chat behavior

Autonomy v1 delivers proactive messages to one configured **home** chat or thread per profile.

That home target is where Hermes:

- sends proactive updates
- asks for approval on autonomy-created drafts
- keeps social nudges scoped, if social proactivity is enabled

This keeps surfacing predictable and avoids background messages appearing in unrelated threads.

## What autonomy can and cannot do

Autonomy can:

- register explicit open-ended watches immediately when `extract_behavior` allows it
- infer ongoing watch items from normal conversation
- revisit those watch items on a schedule
- use configured toolsets for research
- create findings
- create drafts such as draft emails or draft follow-ups
- bring important updates into the next hot-path turn
- proactively message the home chat when needed

Autonomy cannot:

- send final destructive/external actions when `allow_final_external_actions: false`
- recursively create cron jobs
- spawn unrestricted hidden agent swarms
- expose raw background transcripts to the user

In the default setup, autonomy stages work and the normal Hermes hot path handles final user approval and execution.

## Resolution and cleanup

Tracked items are not meant to grow forever.

Hermes tries to close watch items when:

- the user says the task is done
- the user says to stop tracking it
- the user confirms the follow-up has already happened
- the supervisor concludes the issue is resolved or no longer worth monitoring

Resolved watch items stop being checked, and old resolved watch records are pruned after `autonomy.resolved_retention_days`.

## Inspecting autonomy state

Messaging slash commands:

| Command | Description |
|---------|-------------|
| `/autonomy` | Show autonomy status, home target, pending counts, and current revision |
| `/autonomy pause` | Pause the autonomy runtime for the profile |
| `/autonomy resume` | Resume the autonomy runtime |
| `/autonomy inbox` | Show recent surfaced autonomy inbox items |
| `/autonomy watch` | Show active/paused watch items |
| `/autonomy drafts` | Show stored draft artifacts |
| `/autonomy runs` | Show recent autonomy intake/supervisor runs |

## Recommended defaults

For a practical first setup:

- `extract_behavior: both`
- `infer_level: implied`
- `proactivity_level: utility`
- `allow_drafts: true`
- `allow_final_external_actions: false`
- `inject_on_change_only: true`
- `quiet_hours.enabled: false` while testing

If you want fast feedback while testing, temporarily shorten:

- `interval_seconds`
- `poll_interval_seconds`

## Related docs

- [Messaging gateway](/docs/user-guide/messaging)
- [Scheduled tasks (cron)](/docs/user-guide/features/cron)
- [Configuration](/docs/user-guide/configuration)
- [Slash commands reference](/docs/reference/slash-commands)
