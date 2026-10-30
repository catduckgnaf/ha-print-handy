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


class _RoutingSession:
    """Fake session dispatching on the exact URL; routes may raise."""

    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        route = self._routes[url]
        if isinstance(route, Exception):
            raise route
        return route


def _status_url(printer_id):
    return f"http://ph.local:8080/api/printers/{printer_id}/status"


_PRINTING_BODY = {
    "ok": True,
    "id": "ad5x",
    "status": {"state": "printing", "filename": "a.gcode", "progress": 0.5,
               "hotend_c": 210, "bed_c": 60},
}


def test_get_printer_status_parses_and_sends_token():
    """A 200 printer status parses; the token header goes out as usual."""
    session = _RoutingSession({_status_url("ad5x"): _FakeResponse(200, _PRINTING_BODY)})
    status = asyncio.run(_client(session, token="sekrit").async_get_printer_status("ad5x"))
    assert status.state == "printing"
    assert status.progress == 0.5
    url, headers = session.calls[0]
    assert url == _status_url("ad5x")
    assert headers == {"X-WebUI-Token": "sekrit"}


def test_get_printer_status_quotes_the_id():
    """Printer ids are URL-quoted into the path."""
    session = _RoutingSession(
        {_status_url("my%20printer"): _FakeResponse(200, _PRINTING_BODY)}
    )
    asyncio.run(_client(session).async_get_printer_status("my printer"))
    assert session.calls[0][0] == _status_url("my%20printer")


def test_get_printer_status_404_is_unavailable_not_error():
    """An unknown id (or endpoint-less old server) reads as unavailable."""
    body = {"ok": False, "reason": "no printer with id 'ghost' is configured"}
    session = _RoutingSession({_status_url("ghost"): _FakeResponse(404, body)})
    status = asyncio.run(_client(session).async_get_printer_status("ghost"))
    assert status.state == "unavailable"
    assert "no printer with id 'ghost'" in status.detail


def test_get_printer_statuses_tolerates_per_printer_failures():
    """One printer's 404/500/transport failure never fails the batch."""
    session = _RoutingSession(
        {
            _status_url("good"): _FakeResponse(200, _PRINTING_BODY),
            _status_url("gone"): _FakeResponse(404, {"ok": False, "reason": "nope"}),
            _status_url("sad"): _FakeResponse(500, {}),
            _status_url("down"): OSError("connection reset"),
        }
    )
    statuses = asyncio.run(
        _client(session).async_get_printer_statuses(["good", "gone", "sad", "down"])
    )
    assert set(statuses) == {"good", "gone", "sad", "down"}
    assert statuses["good"].state == "printing"
    assert statuses["gone"].state == "unavailable"
    assert statuses["sad"].state == "unavailable"
    assert statuses["down"].state == "unavailable"


def test_get_printer_statuses_propagates_auth_errors():
    """A 401 on any printer status means the entry's token is wrong."""
    session = _RoutingSession(
        {
            _status_url("good"): _FakeResponse(200, _PRINTING_BODY),
            _status_url("locked"): _FakeResponse(401, {"ok": False}),
        }
    )
    with pytest.raises(api.PrintHandyAuthError):
        asyncio.run(
            _client(session, token="wrong").async_get_printer_statuses(["good", "locked"])
        )
