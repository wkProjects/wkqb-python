"""Removing passwords and session ids from data that gets logged or recorded."""

from collections.abc import Mapping
from typing import Any

#: Shorter secrets are not replaced, as they could be part of normal words
MIN_LENGTH = 6


def redact(value: Any, secrets: Mapping[str, str]) -> Any:
    """Replaces each secret by its placeholder in all strings and bytes of value (also in dicts, lists, tuples)."""
    if isinstance(value, bytes):
        for secret, placeholder in secrets.items():
            value = value.replace(secret.encode(), placeholder.encode())
        return value
    if isinstance(value, str):
        for secret, placeholder in secrets.items():
            value = value.replace(secret, placeholder)
        return value
    if isinstance(value, dict):
        return {k: redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, tuple):
        return tuple(redact(v, secrets) for v in value)
    return value


def secrets(**placeholders: str | None) -> dict[str, str]:
    """{secret: "<name>"} for the given secrets that are long enough, e.g. secrets(sid=sid, password=password)."""
    return {secret: f"<{name}>" for name, secret in placeholders.items() if secret and len(secret) >= MIN_LENGTH}
