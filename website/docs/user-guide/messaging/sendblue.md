---
sidebar_position: 8
title: "Sendblue"
description: "Set up Hermes Agent as a Sendblue messaging bot for iMessage, SMS, and RCS"
---

# Sendblue Setup

Hermes integrates with [Sendblue](https://sendblue.com/) as a first-class messaging channel. Once configured, Hermes can chat over iMessage, SMS, and RCS using Sendblue's API and webhooks, with native media delivery, automatic typing indicators, automatic iMessage read receipts, and Sendblue-specific tapback reactions.

---

## Prerequisites

- A Sendblue account with API access
- A Sendblue line number to send from
- A publicly reachable HTTPS endpoint for webhooks
- `aiohttp` and `httpx` available in your Hermes environment

:::tip
If you already installed Hermes with the normal gateway extras, you likely already have what you need. The Sendblue adapter uses the same Hermes gateway process as Telegram, Slack, and the other messaging channels.
:::

---

## Step 1: Get Your Sendblue Credentials

From the Sendblue dashboard / API credentials page, copy:

- **API key**
- **API secret**
- **from number** — the Sendblue number Hermes should send from, in E.164 format (for example `+15551234567`)

---

## Step 2: Configure Hermes

### Interactive setup (recommended)

```bash
hermes gateway setup
```

Select **Sendblue** from the platform list. Hermes writes the Sendblue gateway config into `~/.hermes/config.yaml` and seeds `platform_toolsets.sendblue` automatically.

### Manual setup

Add this to `~/.hermes/config.yaml`:

```yaml
platforms:
  sendblue:
    enabled: true
    api_key: your_api_key
    extra:
      api_secret: your_api_secret
      from_number: "+15551234567"
      allowed_users: "+15559876543,+15551112222"
      webhook_port: 8645
      webhook_path: /webhooks/sendblue
    home_channel:
      platform: sendblue
      chat_id: "+15559876543"
      name: Home

platform_toolsets:
  sendblue:
    - hermes-sendblue
```

Use `hermes tools --platform sendblue` if you want to customize Sendblue's tool access after setup.

### Optional environment-variable overrides

If you prefer env-based configuration or want temporary overrides, Hermes still supports the `SENDBLUE_*` variables as compatibility overrides. Environment variables take precedence over `config.yaml` when both are set.

---

## Step 3: Configure Sendblue Webhooks

Point your Sendblue webhooks at Hermes:

```text
https://your-server:8645/webhooks/sendblue
```

Recommended webhook types:

- `receive`
- `outbound`
- `typing_indicator`

:::warning Do not configure webhook secrets yet
For now, do **not** set `platforms.sendblue.extra.webhook_secret` or `platforms.sendblue.extra.webhook_secret_header` in Hermes, and do **not** enable a Sendblue webhook secret for this integration. We have confirmed webhook-secret handling still needs further debugging, and enabling it can cause Hermes to reject valid Sendblue webhooks with `401 Invalid webhook secret`.
:::

Optional listener overrides:

```yaml
platforms:
  sendblue:
    extra:
      webhook_host: 0.0.0.0
      webhook_port: 8645
      webhook_path: /webhooks/sendblue
```

:::warning
Your webhook endpoint must be publicly reachable over HTTPS. If Hermes is running on a local machine, expose it through a secure tunnel or reverse proxy.
:::

### Quick local option: Tailscale Funnel

If Hermes is running on your laptop or a workstation, [Tailscale Funnel](https://tailscale.com/docs/features/tailscale-funnel) is a simple way to expose the webhook without opening router ports.

1. Install Tailscale, sign in, and make sure Funnel is enabled for your tailnet
2. Start Hermes so it is listening on `platforms.sendblue.extra.webhook_port` (default `8645`)
3. In another terminal, run:

```bash
tailscale funnel --bg 8645
```

4. Tailscale prints a public HTTPS URL such as `https://your-machine.your-tailnet.ts.net`
5. In Sendblue, set the webhook URL to:

```text
https://your-machine.your-tailnet.ts.net/webhooks/sendblue
```

Useful commands:

```bash
tailscale funnel status   # show the active public URL
tailscale funnel reset    # disable Funnel and clear the config
```

:::tip
If you change `platforms.sendblue.extra.webhook_port` or `platforms.sendblue.extra.webhook_path`, make the same change in the Tailscale URL you register with Sendblue. Public DNS for a new Funnel URL can also take a few minutes to propagate.
:::

---

## Step 4: Start the Gateway

```bash
hermes gateway
```

You should see a log line similar to:

```text
[sendblue] Webhook server listening on 0.0.0.0:8645/webhooks/sendblue, from: +1555***4567
```

Then send Hermes a message through Sendblue.

---

## `config.yaml` Keys

Most users should configure Sendblue in `~/.hermes/config.yaml`.

| Key | Required | Description |
|-----|----------|-------------|
| `platforms.sendblue.enabled` | Yes | Enable the Sendblue adapter |
| `platforms.sendblue.api_key` | Yes | Sendblue API key |
| `platforms.sendblue.extra.api_secret` | Yes | Sendblue API secret |
| `platforms.sendblue.extra.from_number` | Yes | Sendblue line number Hermes sends from |
| `platforms.sendblue.extra.allowed_users` | No | Comma-separated E.164 phone numbers allowed to chat |
| `platforms.sendblue.extra.allow_all_users` | No | Allow all Sendblue users without an allowlist |
| `platforms.sendblue.home_channel.chat_id` | No | Default phone number or `group_id` for cron delivery |
| `platforms.sendblue.home_channel.name` | No | Display name for the home target |
| `platforms.sendblue.extra.webhook_host` | No | Webhook bind address (default: `0.0.0.0`) |
| `platforms.sendblue.extra.webhook_port` | No | Webhook port (default: `8645`) |
| `platforms.sendblue.extra.webhook_path` | No | Webhook path (default: `/webhooks/sendblue`) |
| `platforms.sendblue.extra.webhook_secret` | Avoid for now | Not recommended currently; webhook-secret support still needs further debugging |
| `platforms.sendblue.extra.webhook_secret_header` | Avoid for now | Not recommended currently; webhook-secret support still needs further debugging |
| `platforms.sendblue.extra.auto_mark_read` | No | Automatically mark inbound iMessage DMs as read (default: `true`) |
| `platforms.sendblue.extra.status_callback_url` | No | Optional status callback URL for direct outbound Sendblue sends |
| `platform_toolsets.sendblue` | No | Toolset list for Sendblue sessions (defaults to `hermes-sendblue`) |

For env-based overrides and compatibility, see the [environment variables reference](/docs/reference/environment-variables#messaging).

---

## Sendblue-Specific Behavior

- **Plain text replies** — Hermes strips markdown before sending, so assistant responses read naturally in iMessage/SMS/RCS
- **Native media** — image, audio, video, and document attachments are uploaded through Sendblue and delivered as real media
- **Automatic typing indicators** — Hermes sends Sendblue typing indicators while it is working on a response
- **Automatic iMessage read receipts** — inbound iMessage DMs are automatically marked as read when Hermes processes them
- **Tapback reactions** — available through the Sendblue-only `sendblue_action` tool in Sendblue chats

### Reactions

When the current session platform is Sendblue, Hermes exposes the `sendblue_action` tool. It can:

- send tapback reactions (`love`, `like`, `dislike`, `laugh`, `emphasize`, `question`)
- manually mark the current conversation as read

By default, reactions target the latest inbound `message_handle` Hermes saw in that Sendblue chat.

---

## Security

**The gateway denies all users by default.** Recommended configuration:

```yaml
platforms:
  sendblue:
    extra:
      allowed_users: "+15559876543,+15551112222"
```

Or, if you explicitly want an open bot:

```yaml
platforms:
  sendblue:
    extra:
      allow_all_users: true
```

:::warning
`platforms.sendblue.extra.allow_all_users: true` is not recommended for a Hermes instance with terminal access.
:::

---

## Troubleshooting

### Webhooks are not arriving

1. Confirm your Sendblue webhook URL is public and HTTPS
2. Verify `platforms.sendblue.extra.webhook_port` / `platforms.sendblue.extra.webhook_path` match the URL configured in Sendblue
3. If you enabled `platforms.sendblue.extra.webhook_secret` or `platforms.sendblue.extra.webhook_secret_header`, remove them for now and retry — webhook-secret support still needs further debugging
4. Check Hermes gateway logs for Sendblue webhook validation or parse errors

### Hermes can receive but not send

1. Re-check `platforms.sendblue.api_key`, `platforms.sendblue.extra.api_secret`, and `platforms.sendblue.extra.from_number`
2. Make sure the `from_number` is one of your registered Sendblue lines
3. Check whether the target user is allowed by your Sendblue plan and by `platforms.sendblue.extra.allowed_users`

### Typing indicators do not appear

Typing indicators are only meaningful for direct conversations where Sendblue has an existing route mapping. If Sendblue returns a route-mapping error, send a normal message first to establish the conversation.
