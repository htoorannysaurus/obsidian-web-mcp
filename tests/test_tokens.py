"""Tests for expiring, client-bound OAuth access tokens."""

from obsidian_vault_mcp import config
from obsidian_vault_mcp.tokens import issue_access_token, validate_access_token


def test_access_tokens_are_unique_and_valid(vault_dir):
    first = issue_access_token("client-a", now=1_000)
    second = issue_access_token("client-a", now=1_000)
    assert first != second
    assert validate_access_token(first, now=1_001)


def test_access_token_expires(vault_dir):
    token = issue_access_token("client-a", now=1_000)
    assert not validate_access_token(token, now=1_000 + config.OAUTH_TOKEN_TTL)


def test_access_token_can_be_revoked_by_client(vault_dir, monkeypatch):
    token = issue_access_token("client-a", now=1_000)
    monkeypatch.setattr(config, "REVOKED_OAUTH_CLIENT_IDS", {"client-a"})
    assert not validate_access_token(token, now=1_001)


def test_tampered_access_token_is_rejected(vault_dir):
    token = issue_access_token("client-a", now=1_000)
    assert not validate_access_token(token + "x", now=1_001)
