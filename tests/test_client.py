"""Tests for IntervalsClient auth, headers and error handling.

No network access — every request is served by httpx.MockTransport.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from garmin_mcp_bridge.client import USER_AGENT, IntervalsError


def test_uses_basic_auth_with_literal_username_api_key(mock_intervals_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers["Authorization"]
        return httpx.Response(200, json={"id": "i1"})

    client = mock_intervals_client(handler)
    client.get_athlete()

    scheme, _, encoded = captured["auth"].partition(" ")
    assert scheme == "Basic"
    username, _, password = base64.b64decode(encoded).decode().partition(":")
    assert username == "API_KEY"
    assert password == "test-placeholder-key"


def test_sends_browser_like_user_agent(mock_intervals_client):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers["User-Agent"]
        return httpx.Response(200, json={"id": "i1"})

    client = mock_intervals_client(handler)
    client.get_athlete()

    assert captured["user_agent"] == USER_AGENT
    assert "Mozilla" in captured["user_agent"]


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_raise_intervals_error_mentioning_key(mock_intervals_client, status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"message": "nope"})

    client = mock_intervals_client(handler)

    with pytest.raises(IntervalsError, match="(?i)key"):
        client.get_athlete()


def test_long_retry_after_raises_instead_of_blocking(mock_intervals_client):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    client = mock_intervals_client(handler)

    with pytest.raises(IntervalsError, match="Rate limited"):
        client.get_athlete()
