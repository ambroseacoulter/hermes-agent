"""Tests for Sendblue gateway integration."""

import asyncio
import os
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import MessageType


class TestSendblueConfigLoading:
    def test_sendblue_platform_enum_exists(self):
        assert Platform.SENDBLUE.value == "sendblue"

    def test_env_overrides_create_sendblue_config(self):
        from gateway.config import load_gateway_config

        env = {
            "SENDBLUE_API_KEY": "key",
            "SENDBLUE_API_SECRET": "secret",
            "SENDBLUE_FROM_NUMBER": "+15551234567",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_gateway_config()
            assert Platform.SENDBLUE in config.platforms
            pc = config.platforms[Platform.SENDBLUE]
            assert pc.enabled is True
            assert pc.api_key == "key"
            assert pc.extra["api_secret"] == "secret"
            assert pc.extra["from_number"] == "+15551234567"

    def test_env_overrides_set_home_channel(self):
        from gateway.config import load_gateway_config

        env = {
            "SENDBLUE_API_KEY": "key",
            "SENDBLUE_API_SECRET": "secret",
            "SENDBLUE_FROM_NUMBER": "+15551234567",
            "SENDBLUE_HOME_CHANNEL": "+15557654321",
            "SENDBLUE_HOME_CHANNEL_NAME": "My iPhone",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_gateway_config()
            hc = config.platforms[Platform.SENDBLUE].home_channel
            assert hc is not None
            assert hc.chat_id == "+15557654321"
            assert hc.name == "My iPhone"
            assert hc.platform == Platform.SENDBLUE

    def test_sendblue_in_connected_platforms(self):
        from gateway.config import load_gateway_config

        env = {
            "SENDBLUE_API_KEY": "key",
            "SENDBLUE_API_SECRET": "secret",
            "SENDBLUE_FROM_NUMBER": "+15551234567",
        }
        with patch.dict(os.environ, env, clear=False):
            config = load_gateway_config()
            assert Platform.SENDBLUE in config.get_connected_platforms()


class TestSendblueFormatAndRequirements:
    def test_sendblue_settings_default_secret_header(self, monkeypatch):
        from gateway.platforms.sendblue import get_sendblue_settings

        monkeypatch.delenv("SENDBLUE_WEBHOOK_SECRET_HEADER", raising=False)
        settings = get_sendblue_settings(
            PlatformConfig(
                enabled=True,
                api_key="key",
                extra={
                    "api_secret": "secret",
                    "from_number": "+15551234567",
                    "webhook_secret": "webhook-secret",
                },
            )
        )

        assert settings.webhook_secret_header == "sb-signing-secret"

    def test_config_yaml_loads_sendblue_platform(self, tmp_path, monkeypatch):
        from gateway.config import load_gateway_config

        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            """platforms:
  sendblue:
    enabled: true
    api_key: key
    extra:
      api_secret: secret
      from_number: '+15551234567'
      allowed_users: '+15557654321'
    home_channel:
      platform: sendblue
      chat_id: '+15557654321'
      name: My iPhone
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.delenv("SENDBLUE_API_KEY", raising=False)
        monkeypatch.delenv("SENDBLUE_API_SECRET", raising=False)
        monkeypatch.delenv("SENDBLUE_FROM_NUMBER", raising=False)
        monkeypatch.delenv("SENDBLUE_ALLOWED_USERS", raising=False)

        config = load_gateway_config()

        pc = config.platforms[Platform.SENDBLUE]
        assert pc.enabled is True
        assert pc.api_key == "key"
        assert pc.extra["api_secret"] == "secret"
        assert pc.extra["from_number"] == "+15551234567"
        assert pc.home_channel is not None
        assert pc.home_channel.chat_id == "+15557654321"

    def test_config_yaml_bridges_sendblue_values_to_env(self, tmp_path, monkeypatch):
        from gateway.config import load_gateway_config

        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        (hermes_home / "config.yaml").write_text(
            """platforms:
  sendblue:
    enabled: true
    api_key: key
    extra:
      api_secret: secret
      from_number: '+15551234567'
      allowed_users: '+15557654321'
""",
            encoding="utf-8",
        )
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.delenv("SENDBLUE_API_KEY", raising=False)
        monkeypatch.delenv("SENDBLUE_API_SECRET", raising=False)
        monkeypatch.delenv("SENDBLUE_FROM_NUMBER", raising=False)
        monkeypatch.delenv("SENDBLUE_ALLOWED_USERS", raising=False)

        load_gateway_config()

        assert os.environ["SENDBLUE_API_KEY"] == "key"
        assert os.environ["SENDBLUE_API_SECRET"] == "secret"
        assert os.environ["SENDBLUE_FROM_NUMBER"] == "+15551234567"
        assert os.environ["SENDBLUE_ALLOWED_USERS"] == "+15557654321"

    def _make_adapter(self):
        from gateway.platforms.sendblue import SendblueAdapter

        env = {
            "SENDBLUE_API_KEY": "key",
            "SENDBLUE_API_SECRET": "secret",
            "SENDBLUE_FROM_NUMBER": "+15551234567",
        }
        with patch.dict(os.environ, env, clear=False):
            return SendblueAdapter(PlatformConfig(enabled=True, api_key="key", extra={"api_secret": "secret", "from_number": "+15551234567"}))

    def test_sendblue_strips_markdown(self):
        adapter = self._make_adapter()
        assert adapter.format_message("**hello** [world](https://example.com)") == "hello world"

    def test_check_sendblue_requirements(self):
        from gateway.platforms.sendblue import check_sendblue_requirements

        with patch.dict(
            os.environ,
            {
                "SENDBLUE_API_KEY": "key",
                "SENDBLUE_API_SECRET": "secret",
                "SENDBLUE_FROM_NUMBER": "+15551234567",
            },
            clear=False,
        ):
            assert check_sendblue_requirements() is True


class TestSendblueWebhookHandling:
    @pytest.mark.asyncio
    async def test_receive_webhook_dispatches_message(self, monkeypatch):
        from gateway.platforms.sendblue import SendblueAdapter

        monkeypatch.setenv("SENDBLUE_API_KEY", "key")
        monkeypatch.setenv("SENDBLUE_API_SECRET", "secret")
        monkeypatch.setenv("SENDBLUE_FROM_NUMBER", "+15551234567")
        monkeypatch.setenv("SENDBLUE_WEBHOOK_SECRET", "webhook-secret")
        monkeypatch.setenv("SENDBLUE_WEBHOOK_SECRET_HEADER", "X-Test-Secret")

        adapter = SendblueAdapter(PlatformConfig(enabled=True, api_key="key", extra={"api_secret": "secret", "from_number": "+15551234567"}))
        adapter.handle_message = AsyncMock()
        adapter.mark_read = AsyncMock(return_value=None)

        app = web.Application()
        app.router.add_post("/webhooks/sendblue", adapter._handle_webhook)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/webhooks/sendblue",
                json={
                    "content": "hello there",
                    "message_handle": "guid-123",
                    "number": "+15557654321",
                    "from_number": "+15557654321",
                    "to_number": "+15551234567",
                    "service": "iMessage",
                },
                headers={"X-Test-Secret": "webhook-secret"},
            )
            assert resp.status == 200
            await asyncio.sleep(0)

        adapter.handle_message.assert_awaited_once()
        event = adapter.handle_message.await_args.args[0]
        assert event.text == "hello there"
        assert event.message_id == "guid-123"
        assert event.source.chat_id == "+15557654321"
        assert event.message_type == MessageType.TEXT
        adapter.mark_read.assert_awaited_once_with("+15557654321")


    @pytest.mark.asyncio
    async def test_send_typing_uses_sendblue_indicator_for_dm(self, monkeypatch):
        from gateway.platforms.sendblue import SendblueAdapter

        monkeypatch.setenv("SENDBLUE_API_KEY", "key")
        monkeypatch.setenv("SENDBLUE_API_SECRET", "secret")
        monkeypatch.setenv("SENDBLUE_FROM_NUMBER", "+15551234567")
        adapter = SendblueAdapter(PlatformConfig(enabled=True, api_key="key", extra={"api_secret": "secret", "from_number": "+15551234567"}))

        with patch("gateway.platforms.sendblue.sendblue_send_typing_indicator", new=AsyncMock()) as typing_mock:
            await adapter.send_typing("+15557654321")

        typing_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_receive_webhook_accepts_quoted_secret(self, monkeypatch):
        from gateway.platforms.sendblue import SendblueAdapter

        monkeypatch.setenv("SENDBLUE_API_KEY", "key")
        monkeypatch.setenv("SENDBLUE_API_SECRET", "secret")
        monkeypatch.setenv("SENDBLUE_FROM_NUMBER", "+15551234567")
        monkeypatch.setenv("SENDBLUE_WEBHOOK_SECRET", "webhook-secret")
        monkeypatch.setenv("SENDBLUE_WEBHOOK_SECRET_HEADER", "X-Test-Secret")

        adapter = SendblueAdapter(PlatformConfig(enabled=True, api_key="key", extra={"api_secret": "secret", "from_number": "+15551234567"}))
        adapter.handle_message = AsyncMock()
        adapter.mark_read = AsyncMock(return_value=None)

        app = web.Application()
        app.router.add_post("/webhooks/sendblue", adapter._handle_webhook)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/webhooks/sendblue",
                json={
                    "content": "hello there",
                    "message_handle": "guid-quoted-secret",
                    "number": "+15557654321",
                    "from_number": "+15557654321",
                    "to_number": "+15551234567",
                    "service": "iMessage",
                },
                headers={"X-Test-Secret": '  "webhook-secret"  '},
            )
            assert resp.status == 200

        adapter.handle_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_outbound_webhook_is_ignored(self, monkeypatch):
        from gateway.platforms.sendblue import SendblueAdapter

        monkeypatch.setenv("SENDBLUE_API_KEY", "key")
        monkeypatch.setenv("SENDBLUE_API_SECRET", "secret")
        monkeypatch.setenv("SENDBLUE_FROM_NUMBER", "+15551234567")
        adapter = SendblueAdapter(PlatformConfig(enabled=True, api_key="key", extra={"api_secret": "secret", "from_number": "+15551234567"}))
        adapter.handle_message = AsyncMock()

        app = web.Application()
        app.router.add_post("/webhooks/sendblue", adapter._handle_webhook)
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                "/webhooks/sendblue",
                json={
                    "is_outbound": True,
                    "message_handle": "guid-123",
                    "number": "+15557654321",
                },
            )
            assert resp.status == 200
            body = await resp.json()
            assert body["reason"] == "outbound"

        adapter.handle_message.assert_not_awaited()


class TestSendblueToolset:
    def test_hermes_sendblue_toolset_exists(self):
        from toolsets import get_toolset

        ts = get_toolset("hermes-sendblue")
        assert ts is not None

    def test_sendblue_platform_hint_exists(self):
        from agent.prompt_builder import PLATFORM_HINTS

        assert "sendblue" in PLATFORM_HINTS
        assert "plain text" in PLATFORM_HINTS["sendblue"].lower()
