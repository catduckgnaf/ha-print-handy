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


class _ControlSession:
    """Fake session recording POSTs; routes map URL -> response or Exception."""

    def __init__(self, routes):
        self._routes = routes
        self.posts = []

    def post(self, url, headers=None, json=None):
        self.posts.append((url, headers or {}, json))
        route = self._routes[url]
        if isinstance(route, Exception):
            raise route
        return route


def _control_url(printer_id, action):
    return f"http://ph.local:8080/api/printers/{printer_id}/{action}"


def test_control_actions_post_to_the_right_printer_urls():
    """Pause / resume / cancel each POST to that printer's own endpoint."""
    for action in ("pause", "resume", "cancel"):
        session = _ControlSession({_control_url("ad5x", action): _FakeResponse(200, {})})
        asyncio.run(_client(session, token="sekrit").async_printer_control("ad5x", action))
        url, headers, body = session.posts[0]
        assert url == _control_url("ad5x", action)
        assert headers == {"X-WebUI-Token": "sekrit"}
        # Print Handy refuses cancel without an explicit confirm (#708).
        assert body == ({"confirm": True} if action == "cancel" else None)


def test_control_quotes_the_printer_id():
    """Control URLs quote the printer id exactly like the status URL."""
    session = _ControlSession(
        {_control_url("my%20printer", "pause"): _FakeResponse(200, {})}
    )
    asyncio.run(_client(session).async_printer_control("my printer", "pause"))
    assert session.posts[0][0] == _control_url("my%20printer", "pause")


def test_control_unknown_action_is_rejected_without_a_post():
    """An action outside the contract is a programming error, not a POST."""
    session = _ControlSession({})
    with pytest.raises(ValueError, match="unknown control action"):
        asyncio.run(_client(session).async_printer_control("ad5x", "explode"))
    assert session.posts == []


def test_control_403_is_refused_with_the_server_reason():
    """Controls disabled on the Print Handy side reads as a refusal."""
    body = {"ok": False, "reason": "controls are disabled"}
    session = _ControlSession({_control_url("ad5x", "pause"): _FakeResponse(403, body)})
    with pytest.raises(api.PrintHandyControlRefusedError) as excinfo:
        asyncio.run(_client(session).async_printer_control("ad5x", "pause"))
    assert "controls are disabled" in str(excinfo.value)


def test_control_404_is_refused():
    """A 404 (unknown printer, or no control API) also reads as a refusal."""
    session = _ControlSession({_control_url("ghost", "cancel"): _FakeResponse(404, {})})
    with pytest.raises(api.PrintHandyControlRefusedError):
        asyncio.run(_client(session).async_printer_control("ghost", "cancel"))


def test_control_401_is_auth_error():
    """A rejected token on a control POST is the auth error, not a refusal."""
    session = _ControlSession({_control_url("ad5x", "resume"): _FakeResponse(401, {})})
    with pytest.raises(api.PrintHandyAuthError):
        asyncio.run(_client(session, token="wrong").async_printer_control("ad5x", "resume"))


def test_control_500_is_a_generic_api_error():
    """A server error is an API error distinct from a refusal."""
    session = _ControlSession({_control_url("ad5x", "pause"): _FakeResponse(500, {})})
    with pytest.raises(api.PrintHandyApiError) as excinfo:
        asyncio.run(_client(session).async_printer_control("ad5x", "pause"))
    assert type(excinfo.value) is api.PrintHandyApiError


def test_control_transport_failure_is_connection_error():
    """An unreachable Web UI on a control POST is a connection error."""
    session = _ControlSession({_control_url("ad5x", "pause"): OSError("no route")})
    with pytest.raises(api.PrintHandyConnectionError):
        asyncio.run(_client(session).async_printer_control("ad5x", "pause"))


def test_set_target_posts_both_keys_with_null_for_unknown_heater():
    """The target body always carries both keys; unknown heaters are null."""
    session = _ControlSession({_control_url("ad5x", "target"): _FakeResponse(200, {})})
    asyncio.run(
        _client(session, token="sekrit").async_set_printer_target("ad5x", hotend_c=210.0)
    )
    url, headers, body = session.posts[0]
    assert url == _control_url("ad5x", "target")
    assert headers == {"X-WebUI-Token": "sekrit"}
    assert body == {"hotend_c": 210.0, "bed_c": None}


def test_set_target_posts_both_values():
    """Setting both heaters posts both numbers."""
    session = _ControlSession({_control_url("ad5x", "target"): _FakeResponse(200, {})})
    asyncio.run(
        _client(session).async_set_printer_target("ad5x", hotend_c=200.0, bed_c=60.0)
    )
    assert session.posts[0][2] == {"hotend_c": 200.0, "bed_c": 60.0}


def test_set_target_refusal_surfaces():
    """A refused target set raises the refusal error, never a silent no-op."""
    session = _ControlSession({_control_url("ad5x", "target"): _FakeResponse(403, {})})
    with pytest.raises(api.PrintHandyControlRefusedError):
        asyncio.run(_client(session).async_set_printer_target("ad5x", bed_c=60.0))
