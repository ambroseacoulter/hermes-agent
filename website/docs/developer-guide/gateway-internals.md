---
sidebar_position: 7
title: "Gateway Internals"
description: "How the messaging gateway boots, authorizes users, routes sessions, and delivers messages"
---

# Gateway Internals

The messaging gateway is the long-running process that connects Hermes to external platforms.

Key files:

- `gateway/run.py`
- `gateway/config.py`
- `gateway/session.py`
- `gateway/autonomy.py`
- `gateway/delivery.py`
- `gateway/pairing.py`
- `gateway/channel_directory.py`
- `gateway/hooks.py`
- `gateway/mirror.py`
- `gateway/platforms/*`

## Core responsibilities

The gateway process is responsible for:

- loading configuration from `.env`, `config.yaml`, and `gateway.json`
- starting platform adapters
- authorizing users
- routing incoming events to sessions
- maintaining per-chat session continuity
- dispatching messages to `AIAgent`
- running cron ticks and background maintenance tasks
- running profile-scoped autonomy intake and supervisor loops
- mirroring/proactively delivering output to configured channels

## Config sources

The gateway has a multi-source config model:

- environment variables
- `~/.hermes/gateway.json`
- selected bridged values from `~/.hermes/config.yaml`

## Session routing

`gateway/session.py` and `GatewayRunner` cooperate to map incoming messages to active session IDs.

Session keying can depend on:

- platform
- user/chat identity
- thread/topic identity
- special platform-specific routing behavior

## Authorization layers

The gateway can authorize through:

- platform allowlists
- gateway-wide allowlists
- DM pairing flows
- explicit allow-all settings

Pairing support is implemented in `gateway/pairing.py`.

## Delivery path

Outgoing deliveries are handled by `gateway/delivery.py`, which knows how to:

- deliver to a home channel
- resolve explicit targets
- mirror some remote deliveries back into local history/session tracking

## Hooks

Gateway events emit hook callbacks through `gateway/hooks.py`. Hooks are local trusted Python code and can observe or extend gateway lifecycle events.

## Background maintenance

The gateway also runs maintenance tasks such as:

- cron ticking
- cache refreshes
- session expiry checks
- proactive memory flush before reset/expiry
- post-turn autonomy intake that extracts watch items from normal conversations
- periodic autonomy supervision that revisits active watch items, writes findings/drafts, and closes resolved items
- deterministic autonomy outbox polling that decides when pending items should be surfaced

## Autonomy runtime

When `autonomy.enabled` is turned on for a profile, the gateway runs a profile-scoped autonomy runtime alongside ordinary messaging work.

The runtime has three layers:

- **Intake** — after a normal user-facing turn finishes, the gateway can run a hidden extraction pass that turns follow-ups, monitoring requests, and ongoing commitments into durable watch items.
- **Supervisor** — on a timer, the gateway revisits active watch items, uses the allowed toolsets, writes findings or drafts, and can mark watches resolved when they are no longer needed.
- **Outbox-driven delivery** — a deterministic pass checks whether there are pending surfaced items. If the home session is idle, Hermes can proactively synthesize a natural assistant message into the configured home chat. If the home session is active, delivery can wait until the current turn finishes.

`autonomy.extract_behavior` controls how watch creation is split between the hot path and hidden intake:

- `hermes` — explicit open-ended monitoring requests are expected to be registered directly during the user turn.
- `auto_extract` — hidden intake is responsible for creating watches after the user-facing turn.
- `both` — direct registration is preferred for explicit requests, and intake remains as a fallback for implied or missed items.

In `both` mode, the gateway skips post-turn intake if the turn already used the direct autonomy watch-registration path, which keeps watch creation idempotent and avoids duplicates.

Autonomy state is durable and profile-scoped. The runtime stores watch items, findings, artifacts, inbox items, delivery attempts, and run summaries in the profile state database rather than relying on transient in-memory queues or a `HEARTBEAT.md` file.

### Hot-path awareness

The gateway also lets normal user turns see recent autonomy deltas without exposing the background run transcript itself.

- hidden autonomy context is injected only when policy says it should be, typically on change
- the visible user transcript remains clean
- if the user approves an autonomy-created draft later, the hot path resolves the exact stored artifact rather than guessing from a prose summary

This keeps proactive behavior integrated with the main Hermes persona while avoiding background tool spam in user-facing channels.

## Honcho interaction

When Honcho is enabled, the gateway keeps persistent Honcho managers aligned with session lifetimes and platform-specific session keys.

### Session routing

Honcho tools (`honcho_profile`, `honcho_search`, `honcho_context`, `honcho_conclude`) need to execute against the correct user's Honcho session. In a multi-user gateway, the process-global module state in `tools/honcho_tools.py` is insufficient — multiple sessions may be active concurrently.

The solution threads session context through the call chain:

```
AIAgent._invoke_tool()
  → handle_function_call(honcho_manager=..., honcho_session_key=...)
    → registry.dispatch(**kwargs)
      → _handle_honcho_*(args, **kw)
        → _resolve_session_context(**kw)   # prefers explicit kwargs over module globals
```

`_resolve_session_context()` in `honcho_tools.py` checks for `honcho_manager` and `honcho_session_key` in the kwargs first, falling back to the module-global `_session_manager` / `_session_key` for CLI mode where there's only one session.

### Memory flush lifecycle

When a session is reset, resumed, or expires, the gateway flushes memories before discarding context. The flush creates a temporary `AIAgent` with:

- `session_id` set to the old session's ID (so transcripts load correctly)
- `honcho_session_key` set to the gateway session key (so Honcho writes go to the right place)
- `sync_honcho=False` passed to `run_conversation()` (so the synthetic flush turn doesn't write back to Honcho's conversation history)

After the flush completes, any queued Honcho writes are drained and the gateway-level Honcho manager is shut down for that session key.

## Related docs

- [Session Storage](./session-storage.md)
- [Cron Internals](./cron-internals.md)
- [ACP Internals](./acp-internals.md)
