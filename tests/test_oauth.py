"""Security-focused tests for the OAuth compatibility layer."""

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from starlette.applications import Starlette
from starlette.testclient import TestClient

from obsidian_vault_mcp.oauth import oauth_routes
from obsidian_vault_mcp.tokens import validate_access_token


def _client() -> TestClient:
    return TestClient(Starlette(routes=oauth_routes))


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def test_client_credentials_grant_is_rejected():
    response = _client().post(
        "/oauth/token",
        data={"grant_type": "client_credentials", "client_id": "vault-mcp-client"},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "unsupported_grant_type"}


def test_authorization_requires_s256_pkce():
    response = _client().get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "test-client",
            "redirect_uri": "https://chatgpt.com/connector/oauth/test",
        },
    )

    assert response.status_code == 400
    assert "PKCE" in response.json()["error_description"]


def test_token_exchange_rejects_different_client_id():
    verifier = "a" * 64
    client = _client()
    authorization = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "expected-client",
            "redirect_uri": "https://chatgpt.com/connector/oauth/test",
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(authorization.headers["location"]).query)["code"][0]

    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "different-client",
            "code": code,
            "redirect_uri": "https://chatgpt.com/connector/oauth/test",
            "code_verifier": verifier,
        },
    )

    assert response.status_code == 400
    assert response.json()["error_description"] == "client_id mismatch"


def test_token_exchange_issues_expiring_client_token(vault_dir):
    verifier = "b" * 64
    client = _client()
    authorization = client.get(
        "/oauth/authorize",
        params={
            "response_type": "code",
            "client_id": "expected-client",
            "redirect_uri": "https://chatgpt.com/connector/oauth/test",
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
        },
        follow_redirects=False,
    )
    code = parse_qs(urlparse(authorization.headers["location"]).query)["code"][0]

    response = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": "expected-client",
            "code": code,
            "redirect_uri": "https://chatgpt.com/connector/oauth/test",
            "code_verifier": verifier,
        },
    )

    assert response.status_code == 200
    assert response.json()["expires_in"] == 86400
    assert validate_access_token(response.json()["access_token"])
