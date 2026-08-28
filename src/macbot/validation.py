"""Validate protocol data without modifying user language or trusting media headers."""

from __future__ import annotations

import base64
import binascii
from typing import Any


class ValidationError(ValueError):
    pass


def json_object() -> dict[str, Any]:
    """Reject scalar/list/null JSON uniformly at all service boundaries."""
    from flask import request

    value = request.get_json()
    if not isinstance(value, dict):
        raise ValidationError("Request body must be a JSON object")
    return value


def validate_chat_message(text: Any) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > 10000 or "\x00" in text:
        raise ValidationError("Message must contain 1–10000 characters without NUL")
    return text.strip()


def decode_audio(data: Any) -> tuple[bytes, str]:
    if not isinstance(data, str) or len(data) > 12 * 1024 * 1024:
        raise ValidationError("Audio payload exceeds the limit or is not text")
    suffix = ".webm"
    if data.startswith("data:"):
        header, separator, data = data.partition(",")
        types = {
            "audio/webm": ".webm",
            "audio/ogg": ".ogg",
            "audio/wav": ".wav",
            "audio/x-wav": ".wav",
            "audio/mp4": ".m4a",
            "audio/mpeg": ".mp3",
        }
        mime = header[5:].split(";")[0]
        if not separator or not header.endswith(";base64") or mime not in types:
            raise ValidationError("Unsupported audio DataURL")
        suffix = types[mime]
    try:
        content = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("Invalid base64 audio") from exc
    if not content or len(content) > 8 * 1024 * 1024:
        raise ValidationError("Audio must contain 1 byte to 8 MiB")
    return content, suffix
