"""Stable, sanitized errors exposed by provider adapters."""

from __future__ import annotations

import re


_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_SECRET_PATTERNS = (
    re.compile(r"(?i)(authorization\s*[:=]\s*bearer\s+)\S+"),
    re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
)


def sanitize_message(message: object, *, fallback: str = "The provider request failed.") -> str:
    """Bound error text and remove common credential forms.

    Provider code paths use fixed messages and never include response bodies.
    This helper also makes the public exception safe when callers pass a
    provider supplied message by mistake.
    """

    text = " ".join(str(message or "").split())
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = text[:240].strip()
    return text or fallback


class ProviderError(Exception):
    """A provider failure that can be safely persisted or returned to clients."""

    def __init__(self, code: str, message: str, retryable: bool = True) -> None:
        normalized_code = code.upper().replace("-", "_")
        self.code = normalized_code if _CODE.fullmatch(normalized_code) else "PROVIDER_ERROR"
        self.message = sanitize_message(message)
        self.retryable = bool(retryable)
        super().__init__(self.message)


__all__ = ["ProviderError", "sanitize_message"]
