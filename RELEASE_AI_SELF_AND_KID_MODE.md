# AI Self + Kid Mode Update

This release adds two user-facing features:

1. `AI Self` / `hatch`
2. `kid mode`

This guide is the quick setup/readme for both.

---

## What This Update Adds

### AI Self / Hatch

Hermes can now hatch into a more personal identity through a guided onboarding flow.

- The user starts it with `/hatch` from a messaging platform
- Hermes asks natural follow-up questions instead of using a rigid fixed wizard
- Hermes fills in the editable parts of `SOUL.md`
- Hermes generates and saves its avatar
- Hermes introduces itself with its new avatar
- Hatch state is persistent, so it can resume after interruptions or session resets
- Hatch is intended to be one-time per profile

Important behavior:

- Hatch is **gateway-only** right now
- Hatch completes only when the required `SOUL.md` fields are filled and the avatar exists locally
- Hermes now chooses **its own name** during hatch

### Kid Mode

Kid mode adds a system-prompt overlay that tells Hermes it is talking to a child.

- Safer tone
- Simpler wording
- More age-appropriate choices
- Applies to normal chat
- Also applies to hatch onboarding, SOUL writing, and avatar generation

---

## Quick Start

### Start Hatch

From Telegram, Discord, Slack, WhatsApp, or another gateway platform:

```text
/hatch
```

Useful hatch commands:

```text
/hatch
/hatch status
/hatch cancel
/hatch restart
```

What they do:

- `/hatch` starts or resumes hatch
- `/hatch status` shows what is still missing
- `/hatch cancel` stops hatch without completing it
- `/hatch restart` resets hatch and starts over

---

## Required Setup

### 1. Messaging Gateway

Hatch is triggered through the messaging gateway, not the local CLI.

You need:

- a configured gateway platform
- an authorized user/chat
- Hermes running in gateway mode

### 2. Avatar Generation

Hatch expects image generation to be available so it can create an avatar.

The easiest setup is:

```bash
export FAL_KEY="your-key-here"
```

Hermes uses:

- model: `nano-banana-2`
- aspect ratio: `portrait_4_3`
- upscaling: `false`

If image generation is unavailable, hatch can gather info but will not fully finish the avatar step.

---

## SOUL.md Behavior

On a fresh profile, Hermes seeds a base `SOUL.md` template automatically.

The new template is based on your desktop SOUL example and includes pending fields for hatch to fill.

### Hatch Editable Sections

Hermes is allowed to update:

- `<facts>`
- `<appearance>`
- `<personality>`

### Locked Sections

Hermes should not rewrite:

- `<overview>`
- `<chat-style>`
- `<core-rules>`
- `<growth>`
- `<boundaries>`
- `<internal-check>`
- `<continuity-and-meta>`

### Fields Hatch Must Complete

Hatch is considered incomplete until these are filled:

- `Name`
- `Vibe`
- `Bio`
- `Aspiration`
- `Emoji`
- `Avatar`
- `User's Name`
- `Appearance`
- `Personality`

Notes:

- `Name` is no longer hardcoded to Hermes
- Hermes should choose its own name based on identity and vibe
- It should not default to `Hermes` unless the user explicitly wants that
- Timezone is intentionally left out for now

---

## Kid Mode Config

Add this to `~/.hermes/config.yaml`:

```yaml
kid_mode:
  enabled: true
  prompt_file: ""
```

### What `prompt_file` Does

You can point `kid_mode.prompt_file` at a custom prompt file if you want your own kid-mode instructions.

Example:

```yaml
kid_mode:
  enabled: true
  prompt_file: "my-kid-mode.md"
```

Path resolution:

- first relative to `HERMES_HOME`
- then relative to the project root

If no custom file is set, Hermes falls back to:

- bundled `SOUL_CHILDREN.md`
- then a built-in default kid-mode block

### Kid Mode + Hatch

If kid mode is enabled when hatch runs:

- onboarding questions stay child-friendly
- generated `Bio`, `Vibe`, `Aspiration`, `Emoji`, `Appearance`, and `Personality` stay child-friendly
- avatar generation is kept safe and age-appropriate
- the avatar is steered toward a youthful or childlike look instead of an adult-coded one

---

## Files You May Want To Edit

### Main user files

- `~/.hermes/SOUL.md`
- `~/.hermes/config.yaml`
- `~/.hermes/.env`

### Project-side reference files

- `SOUL_CHILDREN.md`
- `RELEASE_AI_SELF_AND_KID_MODE.md`

### Persistent hatch state

Hermes stores hatch progress here:

- `~/.hermes/hatch/state.json`

If you use profiles, this lives inside that profile’s `HERMES_HOME`.

---

## Recommended Config Examples

### Normal Mode

```yaml
kid_mode:
  enabled: false
  prompt_file: ""
```

### Kid Mode

```yaml
kid_mode:
  enabled: true
  prompt_file: ""
```

### Kid Mode With Custom Rules

```yaml
kid_mode:
  enabled: true
  prompt_file: "kid-mode-custom.md"
```

And in `~/.hermes/.env`:

```bash
FAL_KEY="your-key-here"
```

---

## Recommended First-Run Flow

1. Configure your gateway platform
2. Set `FAL_KEY`
3. Optionally enable `kid_mode`
4. Start Hermes gateway
5. Message Hermes with `/hatch`
6. Let Hermes finish the onboarding naturally

---

## Good To Know

- Hatch is meant to feel conversational, not like a form
- Hatch can survive interruptions
- Hatch resumes from persistent state
- Hatch resets the session after completion so normal conversation starts clean
- Hatch is designed as a one-time profile setup

If you want to test hatch repeatedly, the cleanest path is usually using a fresh Hermes profile.

