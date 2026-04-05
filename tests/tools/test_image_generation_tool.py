"""Tests for image generation option handling."""

import base64
import json
from unittest.mock import MagicMock

from tools import image_generation_tool as image_tool


class _DummyHandler:
    def __init__(self, payload):
        self.payload = payload

    def get(self):
        return self.payload


def test_image_generate_tool_supports_portrait_4_3_without_upscale(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(
        image_tool,
        "_submit_fal_request",
        lambda *_args, **_kwargs: _DummyHandler(
            {"images": [{"url": "https://example.com/avatar.png", "width": 768, "height": 1024}]}
        ),
    )
    upscaler = MagicMock(return_value={"url": "https://example.com/upscaled.png"})
    monkeypatch.setattr(image_tool, "_upscale_image", upscaler)

    result = json.loads(
        image_tool.image_generate_tool(
            prompt="Portrait avatar",
            aspect_ratio="portrait_4_3",
            upscale=False,
        )
    )

    assert result["success"] is True
    assert result["image"] == "https://example.com/avatar.png"
    upscaler.assert_not_called()


def test_handle_image_generate_forwards_new_options(monkeypatch):
    captured = {}

    def fake_image_generate_tool(**kwargs):
        captured.update(kwargs)
        return json.dumps({"success": True, "image": "https://example.com/avatar.png"})

    monkeypatch.setattr(image_tool, "image_generate_tool", fake_image_generate_tool)

    image_tool._handle_image_generate(
        {"prompt": "Portrait avatar", "aspect_ratio": "portrait_4_3", "upscale": False}
    )

    assert captured["aspect_ratio"] == "portrait_4_3"
    assert captured["upscale"] is False


def test_image_generate_tool_can_save_to_output_path(monkeypatch, tmp_path):
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(
        image_tool,
        "_submit_fal_request",
        lambda *_args, **_kwargs: _DummyHandler(
            {"images": [{"url": "https://example.com/avatar.png", "width": 768, "height": 1024}]}
        ),
    )
    monkeypatch.setattr(image_tool, "_upscale_image", MagicMock())
    monkeypatch.setattr(
        image_tool,
        "_save_generated_image",
        lambda url, output_path: str(tmp_path / "saved-avatar.png"),
    )

    result = json.loads(
        image_tool.image_generate_tool(
            prompt="Portrait avatar",
            aspect_ratio="portrait_4_3",
            upscale=False,
            output_path=str(tmp_path / "avatars" / "hermes-avatar.png"),
        )
    )

    assert result["success"] is True
    assert result["saved_path"] == str(tmp_path / "saved-avatar.png")


def test_image_generate_tool_does_not_force_sync_mode(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test-key")
    captured = {}

    def fake_submit(_model, arguments):
        captured.update(arguments)
        return _DummyHandler(
            {"images": [{"url": "https://example.com/avatar.png", "width": 768, "height": 1024}]}
        )

    monkeypatch.setattr(image_tool, "_submit_fal_request", fake_submit)
    monkeypatch.setattr(image_tool, "_upscale_image", MagicMock())

    result = json.loads(
        image_tool.image_generate_tool(
            prompt="Portrait avatar",
            aspect_ratio="portrait_4_3",
            upscale=False,
        )
    )

    assert result["success"] is True
    assert "sync_mode" not in captured
    assert captured["aspect_ratio"] == "3:4"


def test_save_generated_image_supports_data_uri(tmp_path):
    payload = base64.b64encode(b"fake-image-bytes").decode("ascii")
    output_path = tmp_path / "avatars" / "hermes-avatar.png"

    saved = image_tool._save_generated_image(
        f"data:image/png;base64,{payload}",
        str(output_path),
    )

    assert saved == str(output_path.resolve())
    assert output_path.read_bytes() == b"fake-image-bytes"
