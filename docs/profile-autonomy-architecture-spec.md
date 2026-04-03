# Profile-Scoped Gateway Autonomy Architecture Spec

Date: 2026-04-03
Status: Proposed
Audience: Hermes implementation agent working from a clean branch

## Summary

Hermes should gain a proactive autonomy subsystem that runs only when the gateway daemon is running, is scoped to the active Hermes profile via `HERMES_HOME`, and feels like the same agent as the normal hot path without exposing hidden background transcripts, tool chatter, or queue internals to the user.

This document is the source of truth for that design.

It intentionally does not preserve the current prototype branch as-is. Some ideas from that prototype are worth keeping, but the implementation should start fresh.

## Decision

Implement autonomy as a gateway-only, profile-scoped subsystem with five layers:

1. `Autonomy Intake`
   A hidden post-turn extractor that identifies watch items, follow-ups, deadlines, approvals, and draft opportunities from ordinary user conversations.
2. `Autonomy Supervisor`
   A periodic profile-wide background loop that prioritizes what to inspect, research, summarize, and draft.
3. `Structured Autonomy State`
   Durable database-backed records for watch items, findings, artifacts, inbox items, run summaries, and delivery attempts.
4. `Inbox + Delivery Policy`
   Delivery is not the source of truth. Findings and artifacts are durable. Delivery is a policy layer that decides whether to push, inject, or hold.
5. `Hot-Path Awareness + Handoff`
   The main Hermes agent receives compact autonomy deltas only when something changed and can later fetch exact structured artifacts for approval or execution.

The user sees only natural Hermes messages in normal channels. Background autonomy runs remain hidden.

## Runtime Cost Model

This design must not be implemented as "three always-on background LLM agents."

For v1, the intended runtime model is:

1. one real background autonomy LLM loop,
2. one deterministic queue/outbox processor implemented in ordinary code,
3. and occasional assistant-initiated Hermes synthesis turns only when proactive delivery is actually needed.

That means:

- the `Delivery Coordinator` is not a second LLM agent,
- it is policy code,
- and the synthesis pass is not continuously running.

If no surfaced-worthy autonomy items exist, there should be no synthesis turn at all.

## User Requirements Captured In This Spec

The design must satisfy these product constraints:

- Autonomy only runs when the gateway daemon is running.
- Autonomy is scoped per Hermes profile, not per platform user and not per session.
- Each profile should use its own config and state under `HERMES_HOME`.
- A configured home thread/channel is the initial, explicit surface for proactive outreach.
- Autonomy may research, inspect, summarize, and draft.
- Autonomy must not take final destructive or external actions on its own.
- If a background run wants to do something requiring human approval, the hot-path Hermes flow should surface it naturally and then execute it after user confirmation.
- Cron should not be the main solution for autonomy, and autonomy should not have cron or delegation/subagent capabilities by default.
- Autonomy should infer ongoing monitoring needs from ordinary conversation, with a configurable inference level.
- Proactivity style should be configurable, including a utility-only mode and optional social nudges.
- Hot-path Hermes should be aware of autonomy results without bloating context every turn.
- Background tool transcripts, queue internals, and hidden runs must not leak into user-facing conversation threads.

## Why This Design

### Why gateway-only

The gateway is the only Hermes runtime that already has:

- long-lived process lifetime,
- background watchers,
- durable session routing,
- platform delivery adapters,
- a natural concept of a home channel,
- and clean separation between hidden maintenance work and user-visible messages.

CLI mode should remain reactive.

### Why profile-scoped

Hermes already applies profile isolation by setting `HERMES_HOME` before imports in `hermes_cli/main.py`, and stateful paths are expected to hang off `get_hermes_home()` in `hermes_constants.py`.

Autonomy must follow the same rule:

- each profile has its own autonomy config,
- its own autonomy database rows,
- its own home channel,
- its own watch state,
- its own inbox,
- and its own quiet hours / proactivity settings.

### Why not use cron as the core mechanism

Cron is useful for explicit scheduling, not for background cognition.

Hermes cron runs today are:

- stateless across runs,
- intentionally delivered out-of-band,
- and clearly not part of the agent's ongoing conversational awareness.

Cron may later consume autonomy outputs, but autonomy itself should not be implemented as "just scheduled prompts."

### Why not use `HEARTBEAT.md` as canonical state

A file-based heartbeat is useful as a debugging or human-inspection projection, but it is not reliable enough as the canonical source of truth for:

- exact drafted email bodies,
- approval-required commands,
- durable delivery state,
- re-raise logic,
- watch item freshness,
- or compaction-safe hot-path handoff.

Free-text files are too lossy for later execution.

### Why not literal OpenClaw-style periodic main-session turns

Hermes persists transcripts and replays them back into the model on later turns. Hidden autonomy work written into ordinary user transcripts would:

- pollute future context,
- risk replaying tool chatter,
- and work against cached system prompt stability.

Background autonomy work must remain isolated from user session transcripts.

## Existing Hermes Constraints That Must Be Honored

These current codebase behaviors are important and should shape the implementation.

### `session_key` is stable, `session_id` is not

Hermes rotates `session_id` during compression in `run_agent.py` while preserving conversational continuity through parent/child sessions.

Implication:

- autonomy state, digest revision tracking, and seen state must key off profile scope and `session_key`, not `session_id`.

### Transcript replay is real

`gateway/run.py` rebuilds agent history from stored transcripts and passes rich assistant/tool sequences back into the model on later turns.

Implication:

- hidden autonomy tool chatter must not be written into ordinary user transcripts,
- only final polished user-facing proactive messages may be appended to visible session history.

### Session transcript loading prefers the longer source

`gateway/session.py` loads both SQLite and legacy JSONL and prefers whichever has more messages.

Implication:

- if user-visible proactive deliveries are appended, they must go through normal `SessionStore` helpers,
- hidden autonomy state should not rely on user transcript storage at all.

### Stored system prompts are reused for cache stability

`run_agent.py` reuses the stored system prompt on continuing sessions to preserve prefix caching behavior.

Implication:

- autonomy awareness must not be implemented by mutating the cached system prompt on normal turns,
- use API-only current-turn context injection instead.

### Gateway already supports hidden background maintenance patterns

The gateway already runs background tasks such as cron ticks, session expiry maintenance, and other watchers.

Implication:

- autonomy should be implemented as a gateway watcher/maintenance subsystem,
- not as a special case of CLI interaction.

### Approval prompting is designed for foreground user interaction

Hermes approval flows are oriented around the normal interactive user path.

Implication:

- hidden autonomy runs must not enter interactive approval loops,
- approval-worthy actions must become structured artifacts for later foreground execution.

## Prototype Lessons

If the discarded prototype branch existed locally, keep these ideas:

- a hidden post-turn extraction pass,
- isolated periodic background runs,
- durable autonomy run bookkeeping,
- and a dedicated delivery path separate from normal agent loops.

Do not copy these prototype choices directly:

- a profile-wide `HEARTBEAT.md` as the primary state store,
- session-by-session autonomy targeting as the main execution model,
- queue-only delivery truth,
- or unconditional prompt guidance that implies autonomy exists in all runtimes.

## Design Principles

1. Hidden background work must stay hidden.
2. Delivery is not truth; stored findings and artifacts are truth.
3. Hot-path awareness must be compact and change-driven.
4. Autonomy must be non-destructive by default.
5. Approval and final execution belong to the normal Hermes user flow.
6. The home thread/channel is the only proactive push surface in v1.
7. Profile isolation must follow `HERMES_HOME` exactly.
8. Session compaction and new sessions must not cause duplicate or stale injections.
9. The user should always have a way to inspect autonomy state explicitly.
10. Fresh implementation is preferred over patching together the prototype.
11. The autonomy supervisor may suggest urgency, but it must not be the sole authority on whether proactivity happens.
12. Important proactive information must remain recoverable even if one background run chose the wrong surface mode.

## Non-Goals

These are explicitly out of scope for v1:

- CLI-mode autonomy without the gateway
- direct final sending of email, messages, or other external side effects
- autonomy-created cron jobs
- autonomy spawning delegated child agents
- automatic routing of proactive pushes into arbitrary non-home conversation threads
- rich dashboard UI
- general-purpose social companion behavior by default
- full public documentation

## Terms

- `watch item`
  A long-lived thing Hermes should keep an eye on, such as a repo, deadline, person, issue, or topic.
- `finding`
  A concrete result of a background autonomy run, such as "CI failed on main" or "Alice replied about the contract."
- `artifact`
  A structured staged output that can later be executed or shown, such as a draft email or a proposed command.
- `inbox item`
  A user-facing candidate derived from findings or artifacts.
- `delivery attempt`
  A record that Hermes attempted to surface an inbox item.
- `autonomy digest`
  A compact summary injected into hot-path Hermes only when relevant changes occurred.
- `home channel`
  The configured platform/chat/thread where proactive user-facing outreach is sent.

## High-Level Architecture

```mermaid
flowchart TD
    A[Normal User Turn] --> B[Autonomy Intake]
    B --> C[Watch Items / Findings / Artifact Candidates]
    C --> D[Structured Autonomy Store]
    E[Periodic Supervisor Tick] --> D
    E --> F[External Checks / Research / Drafting]
    F --> D
    D --> G[Inbox Policy]
    G --> H[Home Channel Delivery]
    D --> I[Autonomy Digest Builder]
    I --> J[Hot-Path Hermes Turn Injection]
    D --> K[/autonomy Inspection]
    J --> L[User approves / asks / ignores]
    L --> M[Hot-Path Hermes fetches exact artifact]
    M --> N[Final execution in normal flow]
```

## Core Components

### 1. Autonomy Intake

Purpose:

- run after ordinary user turns,
- inspect the recent conversation,
- extract enduring autonomy candidates,
- and store them as structured records.

What it should extract:

- watch items,
- follow-up obligations,
- deadlines,
- unresolved approvals,
- possible draft artifacts,
- and "Hermes should monitor this" signals.

What it should not do:

- no user-facing delivery,
- no cron creation,
- no broad tool use,
- no direct memory write spam,
- no transcript mutation beyond normal user/assistant conversation.

Implementation notes:

- This should be a hidden gateway-side helper flow triggered after user turns.
- It may use a narrow autonomy-intake toolset.
- It may optionally project a human-readable heartbeat/debug file later, but that file is not canonical.

### 2. Autonomy Supervisor

Purpose:

- wake on an interval,
- prioritize what matters now,
- perform bounded research/checks,
- update state,
- and create artifacts or inbox items when appropriate.

Supervisor responsibilities:

- prioritize by urgency, due time, freshness, and user profile settings,
- respect quiet hours and local timezone,
- avoid repeated pointless checks,
- avoid direct final actions,
- and decide whether something deserves surfacing.

Supervisor restrictions:

- no cron tools,
- no delegation/subagents,
- no direct `send_message` or equivalent,
- no blocking approval prompts,
- no automatic final external side effects.

### 3. Structured Autonomy Store

This is the core state model. The autonomy system should be implemented on top of durable structured records in SQLite, likely in `hermes_state.py`.

Suggested tables are below. Exact naming may vary, but the separation should remain.

#### `autonomy_runs`

One row per intake or supervisor run.

Suggested fields:

- `id`
- `profile_id` or implicit current-profile scope
- `run_type` (`intake`, `supervisor`, `digest_build`, `delivery`)
- `status`
- `started_at`
- `finished_at`
- `summary`
- `error`
- `toolset_snapshot`
- `config_snapshot`

Purpose:

- observability,
- debugging,
- failure analysis,
- and compact historical awareness.

#### `autonomy_watch_items`

Long-lived monitored entities.

Suggested fields:

- `id`
- `scope_key`
- `kind` (`repo`, `issue`, `person`, `deadline`, `topic`, `other`)
- `title`
- `normalized_key`
- `description`
- `source_session_key`
- `source_message_ref`
- `inference_mode` (`explicit`, `implied`, `aggressive`)
- `status` (`active`, `paused`, `resolved`, `archived`)
- `importance`
- `due_at`
- `next_check_at`
- `last_checked_at`
- `last_changed_at`
- `last_run_id`
- `metadata_json`

Purpose:

- durable watchlist,
- prioritization,
- and re-check scheduling.

#### `autonomy_findings`

Concrete results discovered by the supervisor.

Suggested fields:

- `id`
- `watch_item_id` nullable
- `run_id`
- `kind` (`event`, `observation`, `fact`, `decision`)
- `title`
- `summary`
- `details_json`
- `importance`
- `freshness_expires_at`
- `source_refs_json`
- `revision`
- `created_at`

Purpose:

- preserve discovered information even if it is never immediately surfaced,
- support later digest building,
- and support `/autonomy`.

#### `autonomy_artifacts`

Staged outputs that can later be used by hot-path Hermes.

Suggested fields:

- `id`
- `run_id`
- `watch_item_id` nullable
- `artifact_type` (`draft_email`, `draft_message`, `proposed_command`, `research_brief`, `approval_request`, `draft_reminder`, `other`)
- `title`
- `summary`
- `payload_json`
- `target_json`
- `execution_requirements_json`
- `approval_required`
- `status` (`draft`, `approved`, `executed`, `discarded`, `superseded`)
- `revision`
- `created_at`
- `updated_at`

`payload_json` is the crucial handoff record.

Examples:

- exact email recipient, subject, body
- exact command string
- exact summary content for a briefing
- exact message text to send after approval

#### `autonomy_inbox_items`

User-facing candidates derived from findings or artifacts.

Suggested fields:

- `id`
- `source_type` (`finding`, `artifact`)
- `source_id`
- `title`
- `message_preview`
- `delivery_priority`
- `surface_policy` (`home_push`, `next_turn_context`, `both`, `digest_only`)
- `status` (`pending`, `seen`, `resolved`, `dismissed`, `superseded`)
- `seen_at`
- `last_delivered_at`
- `last_delivery_attempt_id`
- `reraisable`
- `reraisable_on_change_only`
- `reraisable_after`
- `created_at`
- `updated_at`

This is the replacement for a fragile "queue-only" model.

#### `autonomy_delivery_attempts`

Transport and delivery bookkeeping only.

Suggested fields:

- `id`
- `inbox_item_id`
- `run_id` nullable
- `delivery_mode` (`home_push`, `append_after_turn`, `suppressed`, `digest`, `context_only`)
- `target_platform`
- `target_chat_id`
- `target_thread_id`
- `status` (`pending`, `sent`, `failed`, `cancelled`)
- `message_text`
- `error`
- `created_at`
- `sent_at`

Purpose:

- auditability,
- retry handling,
- and "seen on delivery" semantics.

#### `autonomy_state`

Small profile-scoped key/value or singleton state.

Suggested fields:

- `current_revision`
- `last_supervisor_run_at`
- `last_digest_revision`
- `last_social_nudge_at`
- `last_home_delivery_at`

Purpose:

- change detection,
- digest versioning,
- and rate limiting.

### 4. Inbox + Delivery Policy

Delivery is policy, not truth.

Rules:

- if a run discovers something important, store a finding or artifact first,
- then optionally derive an inbox item,
- then optionally attempt delivery,
- but never rely on delivery success to preserve the underlying information.

This directly addresses the "misclassified queue item gets lost forever" risk.

### 4a. Delivery Coordinator (Outbox Processor)

The autonomy supervisor should not be the final decision-maker for:

- whether something should be proactively sent now,
- whether it should wait for the next user turn,
- whether several pending items should be grouped together,
- or whether a message should be sent immediately vs after the current turn completes.

Instead, a separate `Delivery Coordinator` should own final surfacing behavior.
This is effectively a deterministic outbox processor, not another autonomous agent.

Important:

- this coordinator should be implemented as deterministic policy code,
- not as a separate LLM agent.

The supervisor may emit hints such as:

- importance,
- approval required,
- explicit "keep me posted" intent,
- deadline proximity,
- social vs utility category,
- and suggested urgency.

Those are inputs to policy, not final decisions.

The Delivery Coordinator should apply deterministic rules using:

- user config,
- quiet hours,
- active-turn state,
- whether the home session is currently active,
- severity and deadline metadata,
- explicit monitoring intent,
- approval-required status,
- pending unsurfaced inbox items,
- and elapsed time since the last proactive delivery.

### 4b. Assistant-Initiated Synthesis Pass

When the system decides an inbox item should be surfaced proactively, it should not send the supervisor's raw output directly.

Instead, it should run an `assistant-initiated synthesis pass` over pending inbox items.

This pass is conceptually similar to a simulated turn, but it is not a normal hot-path user turn.

Its job is to:

- synthesize one natural Hermes message,
- fit pending autonomy items into the current visible conversation context when possible,
- debounce multiple inbox changes into a coherent update,
- avoid exposing background internals,
- and append only the final polished assistant message to the visible transcript.

This is the recommended implementation of "simulated turn" behavior in Hermes v1.

Cost guidance:

- this pass should only run when the pending surfaced set changed and policy says a proactive send is warranted,
- it should be debounced,
- it should use a tight turn budget,
- and it should be skipped entirely when the system chooses `context_only`.

### 5. Hot-Path Awareness + Handoff

The hot path should not read all raw autonomy runs every turn.

Instead:

- compute a compact autonomy digest,
- inject it only when something changed,
- and let Hermes fetch exact artifacts on demand.

That preserves context efficiency while still giving Hermes useful awareness.

## Canonical Behavior

### Home Channel

The home channel is the only proactive push target in v1.

Rules:

- all unsolicited proactive messages go to the configured home platform/chat/thread,
- social nudges are allowed only there,
- non-home conversation threads do not receive proactive pushes,
- but hot-path Hermes may still receive hidden autonomy digest context on normal user turns elsewhere.

### Delivery Modes

These modes belong to the Delivery Coordinator, not the supervisor.

- `context_only`
  Make the item available to the next hot-path digest injection only.
- `send_after_turn`
  If the home session currently has an active Hermes turn, wait for it to finish and then run the assistant-initiated synthesis pass.
- `send_now`
  If the home session is idle, run the assistant-initiated synthesis pass immediately and send to the home channel.
- `digest_hold`
  Hold for an explicit digest surface.

Important:

- choosing one mode does not destroy the underlying inbox item,
- and a one-off misclassification must not permanently suppress proactivity.

### Misclassification Safety Net

To prevent proactivity from being lost:

1. findings and artifacts are always stored first,
2. user-facing candidates become durable inbox items,
3. final delivery mode is chosen by the Delivery Coordinator instead of the supervisor alone,
4. important unsurfaced inbox items may escalate over time,
5. hot-path digest injection remains a fallback awareness path,
6. `/autonomy` remains an explicit inspection path.

An item should never vanish solely because one autonomy run decided it belonged on the next user turn.

### Seen Semantics

When an inbox item is successfully surfaced to the user, it is considered `seen`.

Do not require explicit acknowledgment to mark it seen.

Rationale:

- users often read a proactive message without replying,
- and treating silence as "not seen" would create noisy re-notification.

### Re-Raise Semantics

An inbox item may be re-raised only when one of these is true:

- the underlying source has a newer revision,
- severity increased,
- deadline is now near,
- the issue persisted beyond a configured threshold,
- or the item was superseded by a materially different artifact.

Do not re-raise just because the user did not reply.

### Hot-Path Injection Semantics

Autonomy digest injection should be dynamic and change-driven.

Rules:

- inject only when there are changes since the last injected revision,
- do not inject every turn,
- on compaction do not re-inject unchanged digests,
- on new sessions do not inject by default unless there is a new important unresolved change,
- and use session-key-aware revision tracking rather than session-id tracking.

This avoids bloating context while still preserving awareness.

### Compaction and New Sessions

Important Hermes constraint:

- `session_id` changes during compression,
- `session_key` is the durable conversational identity,
- and transcript replay can reload prior assistant/tool history.

Therefore:

- autonomy injection state must be keyed by profile and `session_key`, not `session_id`,
- compaction must carry forward "last autonomy digest revision seen",
- and any injected autonomy context must be non-persistent API-only turn context rather than transcript content.

### Approval Semantics

Autonomy must never block on approval prompts in the background.

If a proposed action would require approval:

- do not attempt the final action,
- create an `approval_required` artifact,
- derive an inbox item if needed,
- surface it through the home channel or next-turn digest,
- and let normal Hermes execute it later after the user approves in ordinary conversation.

This keeps a single approval path.

### Non-Destructive Constraint

Autonomy may:

- inspect,
- search,
- browse,
- summarize,
- draft,
- and stage.

Autonomy may not:

- send final emails,
- send final messages,
- mutate user systems in risky ways,
- create cron jobs,
- spawn delegated child agents,
- or trigger tools that require interactive approval in a hidden run.

## Detailed Flows

### Flow 1: Post-Turn Intake

1. User finishes a normal Hermes turn.
2. Gateway launches a hidden intake helper with a narrow toolset.
3. Intake reviews only the relevant recent conversation slice.
4. Intake writes:
   - watch items,
   - new findings,
   - artifact candidates,
   - and maybe inbox items.
5. Intake does not send any user-facing message.
6. Intake bumps the profile autonomy revision if anything meaningful changed.

### Flow 2: Periodic Supervisor Tick

1. Gateway checks `autonomy.enabled`.
2. Gateway uses profile-local config from the active `HERMES_HOME`.
3. Supervisor wakes on `autonomy.interval_seconds`.
4. It gathers:
   - current local time via `hermes_time.now()`,
   - active watch items,
   - unresolved inbox items,
   - recent findings,
   - artifact states,
   - and any relevant profile memory context.
5. It prioritizes a bounded set of checks.
6. It performs allowed work.
7. It persists findings and artifacts.
8. It derives inbox items when appropriate.
9. It records a compact run summary.

### Flow 3: Delivery

1. Delivery policy scans pending inbox items.
2. If an item should be surfaced proactively:
   - run the assistant-initiated synthesis pass over the current pending set,
   - produce one natural Hermes message,
   - send it only to the configured home channel,
   - record a delivery attempt,
   - mark the inbox item `seen`.
3. If the user is currently mid-turn in the home session, the send may be deferred until the turn finishes.
4. No hidden tool transcript is appended to the user conversation. Only the final polished message may be added to the visible transcript.

### Flow 3a: Cost-Optimized V1 Delivery Runtime

This is the recommended concrete implementation model for v1:

1. The autonomy supervisor writes findings, artifacts, and inbox items.
2. A deterministic outbox processor watches for pending surfaced-worthy inbox items.
3. If the home session is currently active:
   - debounce,
   - wait for the active turn to finish,
   - then trigger one assistant-initiated synthesis turn.
4. If the home session is idle:
   - trigger one assistant-initiated synthesis turn immediately.
5. Persist only the final polished assistant message to the visible transcript.

This means v1 uses:

- one actual autonomy LLM loop,
- zero LLM-based delivery coordinators,
- and occasional simulated Hermes turns only when proactively sending is justified.

That is intentionally much simpler and cheaper than a three-agent background architecture.

### Flow 4: Hot-Path User Turn Awareness

1. A user sends a message in any session.
2. Before the LLM call, Hermes checks whether the profile autonomy revision changed since the last injection relevant to that session.
3. If yes, Hermes attaches a small autonomy digest to the current-turn user message at API time only.
4. The digest is not written into the persisted transcript.
5. If the user asks about it, Hermes can fetch exact findings or artifacts from the autonomy store.

Important:

- hot-path injection is a fallback awareness mechanism,
- not the only way proactive items surface,
- and not a substitute for assistant-initiated proactive messaging when policy decides the user should hear about something now.

### Flow 5: Approval-Required Artifact

Example:

1. Supervisor drafts an email to Alice.
2. It stores the exact subject/body/recipient in an artifact.
3. It creates an inbox item with a short natural summary.
4. Hermes surfaces: "I drafted a reply to Alice about the contract renewal. Want me to send it?"
5. User says yes.
6. Hot-path Hermes loads the stored artifact and executes the send in the normal flow.

The hot path does not reconstruct the email from memory.

## Tooling Policy

Autonomy should use explicitly configured toolsets from profile config.

Recommended default tool policy:

- allow read/search/research style tools
- allow drafting helpers
- allow session search / context retrieval if needed
- disallow cron tools
- disallow delegation tools
- disallow direct outbound messaging tools
- disallow final side-effect tools by default

If terminal-like tools are allowed later, they must still obey a stricter autonomy policy:

- approval-requiring commands must not block hidden runs,
- risky commands should be denied and converted into proposed-command artifacts instead.

## `/autonomy` Command Surface

Provide an explicit user-facing inspection surface in both CLI and gateway command handling.

Minimum v1 command modes:

- `/autonomy`
  Show summary status: enabled/disabled, last run, pending inbox count, pending drafts count.
- `/autonomy inbox`
  Show unresolved inbox items.
- `/autonomy drafts`
  Show staged artifacts needing user input.
- `/autonomy watch`
  Show active watch items.
- `/autonomy pause`
  Pause supervisor execution for the current profile.
- `/autonomy resume`
  Resume supervisor execution.

This command exists so no item is trapped behind delivery policy.

## Config Schema

Add profile-local config in `config.yaml`.

Suggested shape:

```yaml
autonomy:
  enabled: false
  interval_seconds: 1800

  home_platform: telegram
  home_chat_id: "-1001234567890"
  home_thread_id: "42"

  allowed_toolsets:
    - search
    - web
    - session_search

  infer_level: implied
  proactivity_level: utility
  social_rate_limit_hours: 24

  quiet_hours:
    enabled: true
    start: "22:00"
    end: "08:00"

  inject_on_change_only: true
  new_session_injection: important_only

  allow_drafts: true
  allow_final_external_actions: false
```

Semantics:

- `enabled`
  Runs only when true and only in gateway mode.
- `home_*`
  Canonical surface for proactive messages.
- `allowed_toolsets`
  The supervisor capability budget.
- `infer_level`
  `explicit | implied | aggressive`
- `proactivity_level`
  `utility | social | both`
- `social_rate_limit_hours`
  Minimum spacing between unsolicited social nudges.
- `quiet_hours`
  Suppress non-urgent proactive outreach during local quiet times.
- `inject_on_change_only`
  Default true. Prevents constant hot-path context injection.
- `new_session_injection`
  `none | important_only`
- `allow_final_external_actions`
  Must default false.

Important:

- config must be loaded from the profile's active `HERMES_HOME`,
- not a hardcoded `~/.hermes`,
- and should remain isolated per profile.

## Prompt and Context Rules

### Do not mutate the cached system prompt for autonomy state

Hermes already relies on a stable per-session system prompt for cache behavior.

Autonomy awareness should therefore:

- not be baked into stored system prompts,
- not be appended into durable transcript history,
- and instead be attached as API-only current-turn context when needed.

### Suggested autonomy digest shape

The hot-path digest should stay tiny. Example structure:

```text
[Autonomy update since your last seen revision]
- New high-priority watch change: CI on main failed 2h ago.
- Draft prepared: reply to Alice about contract renewal.
- Pending approval-required action: send drafted email to Alice.
```

Rules:

- maximum a few bullets,
- only changed or urgent items,
- no raw tool output,
- no hidden run transcript content.

## Memory and Search Integration

Hermes should not blindly inject all autonomy state into normal memory files.

Preferred pattern:

- keep autonomy records in dedicated autonomy tables,
- optionally allow selected high-value findings to be summarized into memory later,
- and give hot-path Hermes a read path into autonomy state when relevant.

Possible integration points:

- explicit `/autonomy` command,
- targeted read-only autonomy tool,
- or internal helper methods used during digest building.

If hidden autonomy sessions are persisted as sessions for debugging, they should be excluded from ordinary session browsing/search by default, similar to hidden/tool sessions.

## Observability

At minimum, provide:

- last intake run time
- last supervisor run time
- last error
- inbox counts by status
- active watch item count
- number of drafts awaiting user action
- current profile autonomy revision
- count of pending important inbox items not yet proactively surfaced
- last assistant-initiated synthesis pass at

This is important because autonomy failures are easy to miss otherwise.

## Failure Modes and Mitigations

### Failure: item never reaches the user

Mitigation:

- findings and artifacts are durable,
- inbox items are durable,
- delivery is coordinator-owned instead of supervisor-owned,
- assistant-initiated synthesis allows proactive messaging without waiting for a user-initiated turn,
- important unsurfaced items can escalate,
- `/autonomy` exposes them,
- and hot-path digest injection can still surface changed important items later.

### Failure: hot path bloats with autonomy context

Mitigation:

- inject only on revision change,
- use compact digests,
- key by session key,
- and do not replay raw autonomy transcripts.

### Failure: autonomy nags the user repeatedly

Mitigation:

- mark surfaced items as seen on delivery,
- re-raise only on change/escalation/deadline rules,
- rate-limit social nudges.

### Failure: hidden run gets stuck on approval

Mitigation:

- no direct approval prompts in hidden runs,
- convert proposed risky actions into artifacts instead.

### Failure: prototype branch logic leaks into the user transcript

Mitigation:

- fresh implementation,
- hidden runs isolated from visible transcripts,
- only final polished proactive messages appended to home-channel history.

### Failure: profile mixing

Mitigation:

- all autonomy config and durable state must resolve via the active `HERMES_HOME`,
- no hardcoded `~/.hermes`,
- and no cross-profile sharing of home channels, inbox items, or watch state.

## Suggested Implementation Touchpoints

Primary likely files:

- `gateway/config.py`
  Add `autonomy` config model and `config.yaml` bridging.
- `gateway/run.py`
  Intake scheduling, supervisor watcher, delivery policy, hot-path digest injection, home-channel routing.
- `gateway/session.py`
  Session-key-aware tracking for digest revision seen, if needed.
- `hermes_state.py`
  Durable autonomy tables and helpers.
- `run_agent.py`
  API-only turn-context injection seam for compact autonomy digests.
- `hermes_cli/commands.py`
  `/autonomy` command registration.
- `cli.py`
  CLI handler if local inspection is supported there.

Potential optional files:

- `gateway/autonomy.py`
  Shared autonomy helpers and policy functions.
- `toolsets.py`
  Narrow autonomy-specific toolsets if implemented.
- `tools/*`
  Read-only autonomy inspection tool if needed.

## Rollout Plan

### Phase 1: Config + Schema

- add profile-scoped autonomy config
- add durable autonomy tables
- add minimal observability

### Phase 2: Intake

- implement hidden post-turn extraction
- create watch items/findings/artifacts/inbox rows
- no proactive delivery yet

### Phase 3: Supervisor

- implement periodic profile-wide supervisor
- enforce tool restrictions
- create findings/artifacts

### Phase 4: Delivery

- implement home-channel delivery policy
- mark inbox items seen on successful surfacing
- add re-raise logic

### Phase 5: Hot-Path Awareness

- add revision-based autonomy digest injection
- make it API-only and non-persistent
- handle compaction/new session rules

### Phase 6: `/autonomy`

- add explicit inspection commands
- expose inbox/watch/drafts/status

### Phase 7: Polish

- optional heartbeat/debug projection file
- richer observability
- social proactivity if enabled

## Testing Requirements

Implementation should include tests for:

- profile isolation via `HERMES_HOME`
- no autonomy behavior when gateway is not running
- no autonomy behavior when `enabled=false`
- intake extracting watch items without sending messages
- supervisor respecting quiet hours and tool restrictions
- no direct final actions from hidden runs
- delivery only to home channel
- seen-on-delivery behavior
- re-raise only on change/escalation/deadline
- compaction not duplicating autonomy digest injection
- new session behavior with `inject_on_change_only`
- `/autonomy` inspection output
- hidden autonomy records not polluting ordinary session browsing

## Appendix: External Design Influence

This design is informed by prior art but is not a direct clone:

- OpenClaw is directionally right to separate heartbeat from cron.
- Spacebot's profile-global background model is a better fit than literal main-session turns.
- Pika and Lindy are useful examples of identity-consistent proactivity.
- Hermes's own transcript replay and prompt caching model make hidden isolated runs a better fit than in-thread background agent turns.

## Final Guidance For The Implementing Agent

Treat this as a fresh architecture, not a salvage job.

Specifically:

- do not port the prototype branch verbatim,
- do not make `HEARTBEAT.md` canonical,
- do not let hidden background runs write raw tool chatter into user transcripts,
- do not rely on queue records as the sole source of truth,
- do not rebuild the system prompt every turn to inject autonomy state,
- do not let the supervisor be the sole authority on whether proactivity happens,
- do not implement the delivery coordinator as a second LLM agent,
- and do not create a permanently running synthesis agent.

The most important property of the implementation is not "background runs exist."
It is this:

Hermes should become proactively useful without ever feeling like two separate agents stitched together.
