"""Sendblue platform adapter.

First-class Hermes gateway integration for Sendblue (iMessage/SMS/RCS).
Provides REST-based outbound messaging plus a lightweight aiohttp webhook
receiver for inbound messages, media, and typing indicator events.
"""

from __future__ import annotations

import asyncio
import mimetypes
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency-gated
    AIOHTTP_AVAILABLE = False
    web = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    cache_audio_from_bytes,
    cache_document_from_bytes,
    cache_image_from_bytes,
)

import logging

logger = logging.getLogger(__name__)

SENDBLUE_API_BASE = "https://api.sendblue.co"
DEFAULT_WEBHOOK_HOST = "0.0.0.0"
DEFAULT_WEBHOOK_PORT = 8645
DEFAULT_WEBHOOK_PATH = "/webhooks/sendblue"
DEFAULT_TIMEOUT = 30.0
MAX_MESSAGE_LENGTH = 4000
MAX_BODY_BYTES = 1_048_576

_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
_VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
_AUDIO_EXTS = {".ogg", ".opus", ".mp3", ".wav", ".m4a", ".aac", ".caf"}
_DEFAULT_SECRET_HEADERS = (
    "X-Sendblue-Secret",
    "X-Webhook-Secret",
    "X-Webhook-Token",
    "X-Sendblue-Webhook-Secret",
)

_RECENT_INBOUND: dict[str, dict[str, Any]] = {}
_RECENT_INBOUND_TTL_SECONDS = 7 * 24 * 3600


def _now_ts() -> float:
    return time.time()


def _prune_recent() -> None:
    now = _now_ts()
    expired = [
        chat_id
        for chat_id, payload in _RECENT_INBOUND.items()
        if now - float(payload.get("ts", now)) > _RECENT_INBOUND_TTL_SECONDS
    ]
    for chat_id in expired:
        _RECENT_INBOUND.pop(chat_id, None)


def remember_recent_inbound(chat_id: str, message_handle: str, payload: dict[str, Any]) -> None:
    if not chat_id or not message_handle:
        return
    _prune_recent()
    _RECENT_INBOUND[str(chat_id)] = {
        "message_handle": str(message_handle),
        "payload": dict(payload or {}),
        "ts": _now_ts(),
    }


def get_recent_inbound(chat_id: str) -> Optional[dict[str, Any]]:
    _prune_recent()
    return _RECENT_INBOUND.get(str(chat_id))


@dataclass(frozen=True)
class SendblueSettings:
    api_key: str
    api_secret: str
    from_number: str
    webhook_host: str = DEFAULT_WEBHOOK_HOST
    webhook_port: int = DEFAULT_WEBHOOK_PORT
    webhook_path: str = DEFAULT_WEBHOOK_PATH
    webhook_secret: str = ""
    webhook_secret_header: str = ""
    auto_mark_read: bool = True
    status_callback_url: str = ""
    timeout_seconds: float = DEFAULT_TIMEOUT


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _clean_webhook_path(path: str) -> str:
    value = (path or DEFAULT_WEBHOOK_PATH).strip()
    if not value.startswith("/"):
        value = "/" + value
    return value


def _resolve_setting(config: Optional[PlatformConfig], key: str, env_name: str, default: Any = "") -> Any:
    if env_name in os.environ and os.environ[env_name] != "":
        return os.environ[env_name]
    if config:
        if key == "api_key":
            if config.api_key:
                return config.api_key
        elif key == "api_secret":
            if config.extra.get("api_secret"):
                return config.extra.get("api_secret")
        elif key == "from_number":
            if config.extra.get("from_number"):
                return config.extra.get("from_number")
        elif key in config.extra and config.extra.get(key) not in (None, ""):
            return config.extra.get(key)
    return default


def get_sendblue_settings(config: Optional[PlatformConfig] = None) -> SendblueSettings:
    port_raw = _resolve_setting(config, "webhook_port", "SENDBLUE_WEBHOOK_PORT", DEFAULT_WEBHOOK_PORT)
    timeout_raw = _resolve_setting(config, "timeout_seconds", "SENDBLUE_TIMEOUT_SECONDS", DEFAULT_TIMEOUT)
    try:
        port = int(port_raw)
    except (TypeError, ValueError):
        port = DEFAULT_WEBHOOK_PORT
    try:
        timeout_seconds = float(timeout_raw)
    except (TypeError, ValueError):
        timeout_seconds = DEFAULT_TIMEOUT
    return SendblueSettings(
        api_key=str(_resolve_setting(config, "api_key", "SENDBLUE_API_KEY", "") or "").strip(),
        api_secret=str(_resolve_setting(config, "api_secret", "SENDBLUE_API_SECRET", "") or "").strip(),
        from_number=str(_resolve_setting(config, "from_number", "SENDBLUE_FROM_NUMBER", "") or "").strip(),
        webhook_host=str(_resolve_setting(config, "webhook_host", "SENDBLUE_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST) or DEFAULT_WEBHOOK_HOST).strip() or DEFAULT_WEBHOOK_HOST,
        webhook_port=port,
        webhook_path=_clean_webhook_path(str(_resolve_setting(config, "webhook_path", "SENDBLUE_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH) or DEFAULT_WEBHOOK_PATH)),
        webhook_secret=str(_resolve_setting(config, "webhook_secret", "SENDBLUE_WEBHOOK_SECRET", "") or "").strip(),
        webhook_secret_header=str(_resolve_setting(config, "webhook_secret_header", "SENDBLUE_WEBHOOK_SECRET_HEADER", "") or "").strip(),
        auto_mark_read=_coerce_bool(_resolve_setting(config, "auto_mark_read", "SENDBLUE_AUTO_MARK_READ", True), True),
        status_callback_url=str(_resolve_setting(config, "status_callback_url", "SENDBLUE_STATUS_CALLBACK_URL", "") or "").strip(),
        timeout_seconds=timeout_seconds,
    )


def check_sendblue_requirements(config: Optional[PlatformConfig] = None) -> bool:
    if not AIOHTTP_AVAILABLE:
        return False
    try:
        import httpx as _httpx  # noqa: F401
    except ImportError:
        return False
    settings = get_sendblue_settings(config)
    return bool(settings.api_key and settings.api_secret and settings.from_number)


def _redact_phone(phone: str) -> str:
    if not phone:
        return "<none>"
    if len(phone) <= 8:
        return phone[:2] + "***" + phone[-2:] if len(phone) > 4 else "****"
    return phone[:5] + "***" + phone[-4:]


def _is_group_chat_id(chat_id: str) -> bool:
    return not bool(_PHONE_RE.fullmatch((chat_id or "").strip()))


def strip_sendblue_markdown(content: str) -> str:
    text = content or ""
    text = re.sub(r"```[a-zA-Z0-9_+-]*\n?", "", text)
    text = text.replace("```", "")
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"\*(.+?)\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"_(.+?)_", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _normalize_secret(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    return text


def _candidate_secret_headers(settings: SendblueSettings) -> list[str]:
    candidates = []
    if settings.webhook_secret_header:
        candidates.append(settings.webhook_secret_header)
    candidates.extend(_DEFAULT_SECRET_HEADERS)
    seen = set()
    ordered = []
    for header in candidates:
        lowered = (header or "").lower()
        if not lowered or lowered in seen:
            continue
        seen.add(lowered)
        ordered.append(header)
    return ordered


def _present_secret_headers(request: "web.Request", settings: SendblueSettings) -> list[str]:
    present = [header for header in _candidate_secret_headers(settings) if request.headers.get(header)]
    if request.headers.get("Authorization"):
        present.append("Authorization")
    return present


def _extract_header_secret(request: "web.Request", settings: SendblueSettings) -> Optional[str]:
    for header in _candidate_secret_headers(settings):
        value = request.headers.get(header)
        if value:
            return _normalize_secret(value)
    auth = request.headers.get("Authorization", "").strip()
    if auth:
        if auth.lower().startswith("bearer "):
            return _normalize_secret(auth[7:])
        return _normalize_secret(auth)
    return None


async def _parse_json_body(request: "web.Request") -> dict[str, Any]:
    try:
        return await request.json()
    except Exception:
        raw = await request.read()
        if not raw:
            return {}
        import json

        return json.loads(raw.decode("utf-8"))


def _parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return datetime.now()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now()


async def _download_inbound_media(url: str, client: Optional[httpx.AsyncClient] = None) -> tuple[str, str, MessageType]:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True)
    try:
        response = await client.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; HermesAgent/1.0)",
                "Accept": "*/*",
            },
        )
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "application/octet-stream").split(";")[0].strip().lower()
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "attachment"
        ext = Path(filename).suffix.lower()
        if content_type.startswith("image/") or ext in _IMAGE_EXTS:
            if ext not in _IMAGE_EXTS:
                ext = ".jpg"
            return cache_image_from_bytes(response.content, ext), content_type or "image/jpeg", MessageType.PHOTO
        if content_type.startswith("audio/") or ext in _AUDIO_EXTS:
            if ext not in _AUDIO_EXTS:
                ext = ".ogg"
            return cache_audio_from_bytes(response.content, ext), content_type or "audio/ogg", MessageType.VOICE
        if content_type.startswith("video/") or ext in _VIDEO_EXTS:
            if not ext:
                ext = ".mp4"
            return cache_document_from_bytes(response.content, filename or f"video{ext}"), content_type or "video/mp4", MessageType.VIDEO
        if not ext:
            guessed_ext = mimetypes.guess_extension(content_type or "") or ".bin"
            filename = f"attachment{guessed_ext}"
        return cache_document_from_bytes(response.content, filename), content_type or "application/octet-stream", MessageType.DOCUMENT
    finally:
        if own_client and client is not None:
            await client.aclose()


async def _request_json(
    method: str,
    path: str,
    settings: SendblueSettings,
    *,
    payload: Optional[dict[str, Any]] = None,
    files: Optional[dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[int, dict[str, Any]]:
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=settings.timeout_seconds, follow_redirects=True)
    url = f"{SENDBLUE_API_BASE}{path}"
    headers = {
        "sb-api-key-id": settings.api_key,
        "sb-api-secret-key": settings.api_secret,
    }
    try:
        if files is not None:
            response = await client.request(method, url, headers=headers, files=files)
        else:
            headers["Content-Type"] = "application/json"
            response = await client.request(method, url, headers=headers, json=payload or {})
        try:
            body = response.json()
        except ValueError:
            body = {"message": response.text}
        return response.status_code, body
    finally:
        if own_client and client is not None:
            await client.aclose()


async def upload_sendblue_file(
    settings: SendblueSettings,
    file_path: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[bool, Optional[str], dict[str, Any]]:
    path = Path(file_path)
    if not path.exists():
        return False, None, {"message": f"File not found: {file_path}"}
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        status, body = await _request_json(
            "POST",
            "/api/upload-file",
            settings,
            files={"file": (path.name, handle, mime_type)},
            client=client,
        )
    media_url = body.get("media_url")
    ok = status < 400 and bool(media_url)
    return ok, media_url, body


async def upload_sendblue_media_url(
    settings: SendblueSettings,
    media_url: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[bool, Optional[str], dict[str, Any]]:
    status, body = await _request_json(
        "POST",
        "/api/upload-media-object",
        settings,
        payload={"media_url": media_url},
        client=client,
    )
    resolved_url = body.get("media_url") or media_url
    ok = status < 400 and body.get("status") != "ERROR"
    return ok, resolved_url, body


async def sendblue_send_message(
    settings: SendblueSettings,
    chat_id: str,
    content: str,
    *,
    media_url: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> SendResult:
    text = strip_sendblue_markdown(content)
    if not text and not media_url:
        return SendResult(success=False, error="Sendblue requires content or media")

    is_group = _is_group_chat_id(str(chat_id))
    path = "/api/send-group-message" if is_group else "/api/send-message"
    payload: dict[str, Any] = {"from_number": settings.from_number}
    if text:
        payload["content"] = text
    if media_url:
        payload["media_url"] = media_url
    if settings.status_callback_url and not is_group:
        payload["status_callback"] = settings.status_callback_url
    if is_group:
        payload["group_id"] = str(chat_id)
    else:
        payload["number"] = str(chat_id)

    status, body = await _request_json("POST", path, settings, payload=payload, client=client)
    if status >= 400 or body.get("status") == "ERROR":
        message = body.get("message") or body.get("error_message") or str(body)
        return SendResult(success=False, error=f"Sendblue {status}: {message}", raw_response=body)
    return SendResult(
        success=True,
        message_id=body.get("message_handle") or body.get("id"),
        raw_response=body,
    )


async def sendblue_mark_read(
    settings: SendblueSettings,
    number: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> SendResult:
    status, body = await _request_json(
        "POST",
        "/api/mark-read",
        settings,
        payload={"number": number, "from_number": settings.from_number},
        client=client,
    )
    if status >= 400 or body.get("status") == "ERROR":
        message = body.get("message") or body.get("error_message") or str(body)
        return SendResult(success=False, error=f"Sendblue {status}: {message}", raw_response=body)
    return SendResult(success=True, raw_response=body)


async def sendblue_send_typing_indicator(
    settings: SendblueSettings,
    number: str,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> SendResult:
    status, body = await _request_json(
        "POST",
        "/api/send-typing-indicator",
        settings,
        payload={"number": number, "from_number": settings.from_number},
        client=client,
    )
    if status >= 400 or body.get("status") == "ERROR":
        message = body.get("message") or body.get("error_message") or str(body)
        return SendResult(success=False, error=f"Sendblue {status}: {message}", raw_response=body)
    return SendResult(success=True, raw_response=body)


async def sendblue_send_reaction(
    settings: SendblueSettings,
    message_handle: str,
    reaction: str,
    *,
    part_index: int = 0,
    client: Optional[httpx.AsyncClient] = None,
) -> SendResult:
    payload: dict[str, Any] = {
        "from_number": settings.from_number,
        "message_handle": message_handle,
        "reaction": reaction,
    }
    if part_index:
        payload["part_index"] = int(part_index)
    status, body = await _request_json(
        "POST",
        "/api/send-reaction",
        settings,
        payload=payload,
        client=client,
    )
    if status >= 400 or body.get("status") == "ERROR":
        message = body.get("message") or body.get("error_message") or str(body)
        return SendResult(success=False, error=f"Sendblue {status}: {message}", raw_response=body)
    return SendResult(success=True, message_id=body.get("message_handle"), raw_response=body)


class SendblueAdapter(BasePlatformAdapter):
    """Sendblue <-> Hermes gateway adapter."""

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.SENDBLUE)
        self.settings = get_sendblue_settings(config)
        self._runner = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._seen_events: dict[str, float] = {}
        self._seen_ttl_seconds = 3600

    async def connect(self) -> bool:
        if not (self.settings.api_key and self.settings.api_secret and self.settings.from_number):
            logger.error("[sendblue] Missing SENDBLUE_API_KEY, SENDBLUE_API_SECRET, or SENDBLUE_FROM_NUMBER")
            return False
        if not AIOHTTP_AVAILABLE:
            logger.error("[sendblue] aiohttp not installed")
            return False

        app = web.Application(client_max_size=MAX_BODY_BYTES)
        app.router.add_post(self.settings.webhook_path, self._handle_webhook)
        app.router.add_get("/health", lambda _: web.Response(text="ok"))

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.settings.webhook_host, self.settings.webhook_port)
        await site.start()
        self._http_client = httpx.AsyncClient(timeout=self.settings.timeout_seconds, follow_redirects=True)
        self._running = True

        logger.info(
            "[sendblue] Webhook server listening on %s:%d%s, from: %s",
            self.settings.webhook_host,
            self.settings.webhook_port,
            self.settings.webhook_path,
            _redact_phone(self.settings.from_number),
        )
        return True

    async def disconnect(self) -> None:
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._running = False
        logger.info("[sendblue] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        return await sendblue_send_message(self.settings, chat_id, content, client=self._http_client)

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        if _is_group_chat_id(chat_id):
            return
        result = await sendblue_send_typing_indicator(self.settings, chat_id, client=self._http_client)
        if not result.success:
            logger.debug("[sendblue] typing indicator failed for %s: %s", _redact_phone(chat_id), result.error)

    async def send_image(
        self,
        chat_id: str,
        image_url: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        ok, resolved_url, body = await upload_sendblue_media_url(self.settings, image_url, client=self._http_client)
        if not ok or not resolved_url:
            message = body.get("message") or body.get("error_message") or str(body)
            return SendResult(success=False, error=f"Sendblue media upload failed: {message}", raw_response=body)
        return await sendblue_send_message(self.settings, chat_id, caption or "", media_url=resolved_url, client=self._http_client)

    async def send_image_file(
        self,
        chat_id: str,
        image_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> SendResult:
        ok, media_url, body = await upload_sendblue_file(self.settings, image_path, client=self._http_client)
        if not ok or not media_url:
            message = body.get("message") or body.get("error_message") or str(body)
            return SendResult(success=False, error=f"Sendblue upload failed: {message}", raw_response=body)
        return await sendblue_send_message(self.settings, chat_id, caption or "", media_url=media_url, client=self._http_client)

    async def send_document(
        self,
        chat_id: str,
        file_path: str,
        caption: Optional[str] = None,
        file_name: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        ok, media_url, body = await upload_sendblue_file(self.settings, file_path, client=self._http_client)
        if not ok or not media_url:
            message = body.get("message") or body.get("error_message") or str(body)
            return SendResult(success=False, error=f"Sendblue upload failed: {message}", raw_response=body)
        return await sendblue_send_message(self.settings, chat_id, caption or "", media_url=media_url, client=self._http_client)

    async def send_voice(
        self,
        chat_id: str,
        audio_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        ok, media_url, body = await upload_sendblue_file(self.settings, audio_path, client=self._http_client)
        if not ok or not media_url:
            message = body.get("message") or body.get("error_message") or str(body)
            return SendResult(success=False, error=f"Sendblue upload failed: {message}", raw_response=body)
        return await sendblue_send_message(self.settings, chat_id, caption or "", media_url=media_url, client=self._http_client)

    async def send_video(
        self,
        chat_id: str,
        video_path: str,
        caption: Optional[str] = None,
        reply_to: Optional[str] = None,
        **kwargs,
    ) -> SendResult:
        ok, media_url, body = await upload_sendblue_file(self.settings, video_path, client=self._http_client)
        if not ok or not media_url:
            message = body.get("message") or body.get("error_message") or str(body)
            return SendResult(success=False, error=f"Sendblue upload failed: {message}", raw_response=body)
        return await sendblue_send_message(self.settings, chat_id, caption or "", media_url=media_url, client=self._http_client)

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "group" if _is_group_chat_id(chat_id) else "dm", "chat_id": chat_id}

    def format_message(self, content: str) -> str:
        return strip_sendblue_markdown(content)

    async def mark_read(self, number: str) -> SendResult:
        return await sendblue_mark_read(self.settings, number, client=self._http_client)

    async def add_reaction(self, message_handle: str, reaction: str, part_index: int = 0) -> SendResult:
        return await sendblue_send_reaction(
            self.settings,
            message_handle,
            reaction,
            part_index=part_index,
            client=self._http_client,
        )

    def _validate_webhook_secret(self, request: "web.Request") -> bool:
        if not self.settings.webhook_secret:
            return True
        header_secret = _extract_header_secret(request, self.settings)
        expected_secret = _normalize_secret(self.settings.webhook_secret)
        return bool(header_secret and header_secret == expected_secret)

    def _is_duplicate_event(self, event_id: str) -> bool:
        now = _now_ts()
        self._seen_events = {
            key: ts
            for key, ts in self._seen_events.items()
            if now - ts < self._seen_ttl_seconds
        }
        if event_id in self._seen_events:
            return True
        self._seen_events[event_id] = now
        return False

    def _build_event_id(self, payload: dict[str, Any]) -> str:
        if payload.get("message_handle"):
            return str(payload["message_handle"])
        if "is_typing" in payload:
            return f"typing:{payload.get('from_number') or payload.get('number')}:{payload.get('timestamp')}:{payload.get('is_typing')}"
        return f"event:{int(_now_ts() * 1000)}"

    async def _handle_webhook(self, request) -> "web.Response":
        if (request.content_length or 0) > MAX_BODY_BYTES:
            return web.json_response({"error": "Payload too large"}, status=413)

        if not self._validate_webhook_secret(request):
            header_secret = _extract_header_secret(request, self.settings)
            present_headers = _present_secret_headers(request, self.settings)
            logger.warning(
                "[sendblue] invalid webhook secret (configured_header=%s, present_headers=%s, expected_len=%d, received_len=%d)",
                self.settings.webhook_secret_header or "<auto>",
                ",".join(present_headers) if present_headers else "<none>",
                len(_normalize_secret(self.settings.webhook_secret)),
                len(header_secret or ""),
            )
            return web.json_response({"error": "Invalid webhook secret"}, status=401)

        try:
            payload = await _parse_json_body(request)
        except Exception as exc:
            logger.error("[sendblue] webhook parse error: %s", exc)
            return web.json_response({"error": "Invalid JSON"}, status=400)

        event_id = self._build_event_id(payload)
        if self._is_duplicate_event(event_id):
            return web.json_response({"status": "duplicate", "event_id": event_id}, status=200)

        if payload.get("is_outbound"):
            logger.debug("[sendblue] ignoring outbound webhook %s", payload.get("message_handle"))
            return web.json_response({"status": "ignored", "reason": "outbound"}, status=200)

        if "is_typing" in payload:
            logger.debug(
                "[sendblue] typing indicator from %s => %s",
                _redact_phone(str(payload.get("number") or payload.get("from_number") or "")),
                bool(payload.get("is_typing")),
            )
            return web.json_response({"status": "ok", "event": "typing_indicator"}, status=200)

        try:
            event = await self._build_message_event(payload)
        except Exception as exc:
            logger.exception("[sendblue] failed to build inbound event: %s", exc)
            return web.json_response({"error": "Failed to process payload"}, status=400)

        if not event:
            return web.json_response({"status": "ignored"}, status=200)

        task = asyncio.create_task(self._process_inbound_event(event, payload))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return web.json_response({"status": "ok", "message_id": event.message_id}, status=200)

    async def _process_inbound_event(self, event: MessageEvent, payload: dict[str, Any]) -> None:
        if (
            self.settings.auto_mark_read
            and event.source
            and event.source.chat_type == "dm"
            and not _is_group_chat_id(event.source.chat_id)
            and str(payload.get("service") or "").lower() == "imessage"
        ):
            try:
                await self.mark_read(event.source.chat_id)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.debug("[sendblue] auto mark-read failed: %s", exc)
        await self.handle_message(event)

    async def _build_message_event(self, payload: dict[str, Any]) -> Optional[MessageEvent]:
        text = str(payload.get("content") or "").strip()
        media_url = str(payload.get("media_url") or "").strip()
        message_handle = str(payload.get("message_handle") or "").strip()
        group_id = str(payload.get("group_id") or "").strip()
        sender_number = str(payload.get("number") or payload.get("from_number") or "").strip()
        sendblue_number = str(payload.get("to_number") or payload.get("sendblue_number") or self.settings.from_number or "").strip()

        if not sender_number and not group_id:
            return None

        chat_id = group_id or sender_number
        chat_type = "group" if group_id else "dm"
        chat_name = str(payload.get("group_display_name") or chat_id)
        user_id = sender_number or chat_id
        user_name = user_id

        media_urls: list[str] = []
        media_types: list[str] = []
        message_type = MessageType.TEXT
        if media_url:
            cached_path, media_type, detected_type = await _download_inbound_media(media_url, client=self._http_client)
            media_urls.append(cached_path)
            media_types.append(media_type)
            message_type = detected_type
        elif text:
            message_type = MessageType.TEXT

        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type=chat_type,
            user_id=user_id,
            user_name=user_name,
        )
        timestamp = _parse_timestamp(payload.get("date_sent") or payload.get("date_updated"))
        event = MessageEvent(
            text=text,
            message_type=message_type,
            source=source,
            raw_message=payload,
            message_id=message_handle or None,
            media_urls=media_urls,
            media_types=media_types,
            timestamp=timestamp,
        )
        remember_recent_inbound(chat_id, message_handle or event.message_id or "", payload)
        logger.info(
            "[sendblue] inbound %s -> %s (%s): %s",
            _redact_phone(sender_number),
            _redact_phone(sendblue_number),
            chat_type,
            (text or media_url)[:80],
        )
        return event
