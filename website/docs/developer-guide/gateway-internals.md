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

When a memory provider plugin (e.g. Honcho) is enabled, the gateway creates an AIAgent per incoming message with the same session ID. The memory provider's `initialize()` receives the session ID and creates the appropriate backend session. Tools are routed through the `MemoryManager`, which handles all provider lifecycle hooks (prefetch, sync, session end).

### Memory provider session routing

Memory provider tools (e.g. `honcho_profile`, `viking_search`) are routed through the MemoryManager in `_invoke_tool()`:

```
AIAgent._invoke_tool()
  → self._memory_manager.handle_tool_call(name, args)
    → provider.handle_tool_call(name, args)
```

Each memory provider manages its own session lifecycle internally. The `initialize()` method receives the session ID, and `on_session_end()` handles cleanup and final flush.

### Memory flush lifecycle

When a session is reset, resumed, or expires, the gateway flushes built-in memories before discarding context. The flush creates a temporary `AIAgent` that runs a memory-only conversation turn. The memory provider's `on_session_end()` hook fires during this process, giving external providers a chance to persist any buffered data.

## Related docs

- [Session Storage](./session-storage.md)
- [Cron Internals](./cron-internals.md)
- [ACP Internals](./acp-internals.md)
