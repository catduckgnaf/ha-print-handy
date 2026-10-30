"""Tests for the status API client, using a fake aiohttp-style session."""

import asyncio

import pytest

from custom_components.print_handy import api


class _FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        if self._error is not None:
            raise self._error
        return self._response


def _client(session, token=""):
    return api.PrintHandyApiClient(
        session=session, base_url="http://ph.local:8080", token=token
    )


def test_normalize_base_url():
    """Scheme is assumed, trailing slash stripped, empty rejected."""
    assert api.normalize_base_url("ph.local:8080/") == "http://ph.local:8080"
    assert api.normalize_base_url("https://ph.local/") == "https://ph.local"
    with pytest.raises(ValueError):
        api.normalize_base_url("  ")


def test_get_status_sends_token_header_and_parses():
    """A 200 with a snapshot body parses; the token goes out as the header."""
    body = {"version": "2.2.1", "host": "h", "ok": True, "printers": []}
    session = _FakeSession(_FakeResponse(200, body))
    snap = asyncio.run(_client(session, token="sekrit").async_get_status())
    assert snap.instance.version == "2.2.1"
    url, headers = session.calls[0]
    assert url == "http://ph.local:8080/api/status"
    assert headers == {"X-WebUI-Token": "sekrit"}


def test_get_status_401_is_auth_error():
    """HTTP 401 maps to the auth error the config flow keys on."""
    session = _FakeSession(_FakeResponse(401, {"ok": False}))
    with pytest.raises(api.PrintHandyAuthError):
        asyncio.run(_client(session, token="wrong").async_get_status())


def test_get_status_transport_failure_is_connection_error():
    """A session that raises maps to the connection error."""
    session = _FakeSession(error=OSError("no route to host"))
    with pytest.raises(api.PrintHandyConnectionError):
        asyncio.run(_client(session).async_get_status())


def test_get_status_http_error_is_api_error():
    """A non-200/401 status maps to the generic API error."""
    session = _FakeSession(_FakeResponse(500, {}))
    with pytest.raises(api.PrintHandyApiError):
        asyncio.run(_client(session).async_get_status())
