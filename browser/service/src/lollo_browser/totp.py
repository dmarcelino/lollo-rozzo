"""RFC 6238 one-time codes, computed here so the key never reaches the page or
the agent. Takes a base32 key (as authenticator apps show it, spaces allowed)
or an otpauth://totp/ URI (as a vault login's totp field may hold)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import re
import struct
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

_ALGORITHMS = {"SHA1": hashlib.sha1, "SHA256": hashlib.sha256, "SHA512": hashlib.sha512}


class TotpError(ValueError):
    pass


@dataclass(frozen=True)
class TotpKey:
    secret: bytes
    raw_secret: str
    digits: int = 6
    period: int = 30
    algorithm: str = "SHA1"


def _decode_base32(text: str) -> bytes:
    cleaned = re.sub(r"[\s-]", "", text).upper().rstrip("=")
    if not cleaned or not re.fullmatch(r"[A-Z2-7]+", cleaned):
        raise TotpError("the key is not base32 (letters A-Z and digits 2-7)")
    try:
        return base64.b32decode(cleaned + "=" * (-len(cleaned) % 8))
    except (binascii.Error, ValueError) as e:
        raise TotpError("the key is not valid base32") from e


def parse_key(key: str) -> TotpKey:
    """Error messages never include the key."""
    if not isinstance(key, str) or not key.strip():
        raise TotpError("an authenticator key is required")
    key = key.strip()
    if key.lower().startswith("otpauth://"):
        try:
            parts = urlsplit(key)
        except ValueError as e:
            raise TotpError("the otpauth URI is not valid") from e
        if parts.netloc.lower() != "totp":
            raise TotpError("only otpauth://totp/ URIs are supported")
        query = {k.lower(): v[-1] for k, v in parse_qs(parts.query).items()}
        secret = query.get("secret", "")
        algorithm = query.get("algorithm", "SHA1").upper()
        if algorithm not in _ALGORITHMS:
            raise TotpError("unsupported algorithm (SHA1, SHA256 or SHA512)")
        digits, period = query.get("digits", "6"), query.get("period", "30")
        if not digits.isdigit() or int(digits) not in (6, 7, 8):
            raise TotpError("digits must be 6, 7 or 8")
        if not period.isdigit() or not 1 <= int(period) <= 300:
            raise TotpError("period must be between 1 and 300 seconds")
        return TotpKey(_decode_base32(secret), secret, int(digits), int(period), algorithm)
    if "://" in key:
        raise TotpError("only base32 keys and otpauth://totp/ URIs are supported")
    return TotpKey(_decode_base32(key), key)


def code(key: TotpKey, at: float | None = None) -> str:
    counter = int((time.time() if at is None else at) // key.period)
    digest = hmac.new(key.secret, struct.pack(">Q", counter), _ALGORITHMS[key.algorithm]).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**key.digits).zfill(key.digits)
