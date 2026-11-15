"""Text byte decoding that preserves the source encoding and line endings."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DecodedText:
    text: str
    encoding: str
    bom: bytes

    def encode(self, text: str) -> bytes:
        try:
            return self.bom + text.encode(self.encoding)
        except UnicodeEncodeError:
            raise ValueError("Replacement is not representable in the target encoding; explicitly convert the target to UTF-8 first") from None


_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\xef\xbb\xbf", "utf-8"),
    (b"\xfe\xff", "utf-16-be"),
    (b"\xff\xfe", "utf-16-le"),
)


def decode_text(data: bytes) -> DecodedText:
    """Decode common Unicode text and preserve its BOM when present.

    UTF-8 is preferred when no BOM exists.  Latin-1 is a lossless fallback for
    legacy single-byte files. Unrepresentable replacement text is rejected;
    the rest of a legacy document is never silently transcoded.
    """
    for bom, encoding in _BOMS:
        if data.startswith(bom):
            payload = data[len(bom) :]
            return DecodedText(payload.decode(encoding), encoding, bom)
    try:
        return DecodedText(data.decode("utf-8"), "utf-8", b"")
    except UnicodeDecodeError:
        return DecodedText(data.decode("latin-1"), "latin-1", b"")


def replace_newlines(text: str, newline: str) -> str:
    """Use the target file's dominant newline convention in inserted text."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", newline)


def dominant_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf:
        return "\r\n"
    if cr > lf and cr:
        return "\r"
    return "\n"
