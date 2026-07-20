"""Shared pytest fixtures.

Every test wires its own httpx.MockTransport handler, so the suite needs
neither network access nor a real INTERVALS_API_KEY (see CLAUDE.md#Testing).
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from garmin_mcp_bridge import server
from garmin_mcp_bridge.client import IntervalsClient

FAKE_API_KEY = "test-placeholder-key"

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def mock_intervals_client() -> Callable[[Handler], IntervalsClient]:
    """Factory: build an IntervalsClient whose requests are served by ``handler``."""

    def _build(handler: Handler) -> IntervalsClient:
        return IntervalsClient(api_key=FAKE_API_KEY, transport=httpx.MockTransport(handler))

    return _build


@pytest.fixture
def install_server_client(monkeypatch) -> Callable[[Handler], IntervalsClient]:
    """Point the server's lazily-built client at a MockTransport for this test."""

    def _install(handler: Handler) -> IntervalsClient:
        instance = IntervalsClient(api_key=FAKE_API_KEY, transport=httpx.MockTransport(handler))
        monkeypatch.setattr(server, "_client", instance)
        return instance

    return _install
