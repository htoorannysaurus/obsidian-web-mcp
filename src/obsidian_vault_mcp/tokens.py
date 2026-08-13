"""Issue and validate short-lived OAuth access tokens."""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time

from . import config


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_access_token(client_id: str, now: int | None = None) -> str:
    """Issue a signed token bound to one OAuth client and expiration time."""
    if not config.VAULT_MCP_TOKEN:
        raise ValueError("VAULT_MCP_TOKEN is required to sign access tokens")
    issued_at = int(time.time()) if now is None else now
    payload = {
        "client_id": client_id,
        "exp": issued_at + config.OAUTH_TOKEN_TTL,
        "iat": issued_at,
        "nonce": secrets.token_urlsafe(12),
    }
    encoded_payload = _encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signature = hmac.new(
        config.VAULT_MCP_TOKEN.encode("utf-8"),
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"v1.{encoded_payload}.{_encode(signature)}"


def validate_access_token(token: str, now: int | None = None) -> bool:
    """Validate signature, expiration, and optional client revocation."""
    if not config.VAULT_MCP_TOKEN:
        return False
    try:
        version, encoded_payload, encoded_signature = token.split(".", 2)
        if version != "v1":
            return False
        expected_signature = hmac.new(
            config.VAULT_MCP_TOKEN.encode("utf-8"),
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_decode(encoded_signature), expected_signature):
            return False
        payload = json.loads(_decode(encoded_payload))
        current_time = int(time.time()) if now is None else now
        if int(payload["exp"]) <= current_time:
            return False
        if str(payload["client_id"]) in config.REVOKED_OAUTH_CLIENT_IDS:
            return False
        return True
    except (
        binascii.Error,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        return False
