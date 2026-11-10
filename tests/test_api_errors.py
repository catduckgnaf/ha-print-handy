"""Error mapping of the API client: reads, controls and the batch polls."""

import asyncio

import pytest

from custom_components.print_handy import api
from tests.fixtures import MOONRAKER_ROW, printer_body, status_body


class _Response:
    def __init__(self, status, body=None, headers=None, json_error=False):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self._json_error = json_error

    async def json(self):
        if self._json_error:
            raise ValueError("not json")
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    """Routes by (method, url); a route may be a response or an exception."""

    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def _go(self, method, url, **kwargs):
        self.calls.append((method, url))
        route = self._routes[(method, url)]
        if isinstance(route, Exception):
            raise route
        return route

    def get(self, url, headers=None):
        return self._go("get", url)

    def post(self, url, headers=None, json=None):
        return self._go("post", url)


BASE = "http://ph.local:8088"


def _client(routes):
    return api.PrintHandyApiClient(_Session(routes), BASE, "tok")


def _run(coro):
    return asyncio.run(coro)


def test_hub_429_carries_retry_after():
    """Hub 429 carries retry after."""
    routes = {("get", f"{BASE}/api/status"): _Response(429, {"ok": False}, {"Retry-After": "42"})}
    with pytest.raises(api.PrintHandyRateLimitedError) as err:
        _run(_client(routes).async_get_status())
    assert err.value.retry_after == 42


def test_hub_429_without_header_uses_default_wait():
    """Hub 429 without header uses default wait."""
    routes = {("get", f"{BASE}/api/status"): _Response(429, {})}
    with pytest.raises(api.PrintHandyRateLimitedError) as err:
        _run(_client(routes).async_get_status())
    assert err.value.retry_after == api.DEFAULT_RETRY_AFTER


def test_hub_403_names_the_host_rule():
    """Hub 403 names the host rule."""
    routes = {("get", f"{BASE}/api/status"): _Response(403, None, json_error=True)}
    with pytest.raises(api.PrintHandyHostRefusedError) as err:
        _run(_client(routes).async_get_status())
    assert "IP address" in str(err.value)


def test_hub_5xx_is_a_server_error_with_reason():
    """Hub 5xx is a server error with reason."""
    routes = {("get", f"{BASE}/api/status"): _Response(500, {"reason": "boom"})}
    with pytest.raises(api.PrintHandyServerError) as err:
        _run(_client(routes).async_get_status())
    assert "boom" in str(err.value)


def test_hub_unusable_body_is_a_response_error():
    """Hub unusable body is a response error."""
    routes = {("get", f"{BASE}/api/status"): _Response(200, {"nope": 1})}
    with pytest.raises(api.PrintHandyResponseError):
        _run(_client(routes).async_get_status())


def test_timeout_message_says_so():
    """Timeout message says so."""
    routes = {("get", f"{BASE}/api/status"): TimeoutError()}
    with pytest.raises(api.PrintHandyConnectionError) as err:
        _run(_client(routes).async_get_status())
    assert "did not answer" in str(err.value)


def test_batch_reraises_rate_limit_and_host_refusal():
    """Batch reraises rate limit and host refusal."""
    for response, expected in (
        (_Response(429, {}, {"Retry-After": "5"}), api.PrintHandyRateLimitedError),
        (_Response(403, None, json_error=True), api.PrintHandyHostRefusedError),
    ):
        routes = {
            ("get", f"{BASE}/api/printers/voron/status"): response,
            ("get", f"{BASE}/api/printers/x1c/status"): _Response(200, printer_body("x1c")),
        }
        with pytest.raises(expected):
            _run(_client(routes).async_get_printer_statuses(["voron", "x1c"]))


def test_batch_total_transport_outage_raises_but_partial_does_not():
    """Batch total transport outage raises but partial does not."""
    down = {
        ("get", f"{BASE}/api/printers/a/status"): OSError("down"),
        ("get", f"{BASE}/api/printers/b/status"): OSError("down"),
    }
    with pytest.raises(api.PrintHandyConnectionError):
        _run(_client(down).async_get_printer_statuses(["a", "b"]))
    partial = {
        ("get", f"{BASE}/api/printers/a/status"): OSError("down"),
        ("get", f"{BASE}/api/printers/b/status"): _Response(200, printer_body("b")),
    }
    statuses = _run(_client(partial).async_get_printer_statuses(["a", "b"]))
    assert statuses["a"].unavailable
    assert statuses["b"].state == "standby"


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (502, "printer unreachable (OSError: x). Nothing sent."),
        (409, "printer is not printing"),
        (501, "send-G-code is not supported for bambulab_lan printers. Nothing sent."),
        (403, "controls are disabled"),
    ],
)
def test_every_control_failure_carries_the_server_reason(status, reason):
    """Every control failure carries the server reason."""
    url = f"{BASE}/api/printers/voron/emergency_stop"
    routes = {("post", url): _Response(status, {"ok": False, "reason": reason})}
    with pytest.raises(api.PrintHandyApiError) as err:
        _run(_client(routes).async_printer_control("voron", "emergency_stop"))
    assert reason in str(err.value)
    assert f"HTTP {status}" in str(err.value)


def test_control_403_without_json_hints_at_the_host_rule():
    """Control 403 without json hints at the host rule."""
    url = f"{BASE}/api/printers/voron/pause"
    routes = {("post", url): _Response(403, None, json_error=True)}
    with pytest.raises(api.PrintHandyControlRefusedError) as err:
        _run(_client(routes).async_printer_control("voron", "pause"))
    assert "IP address" in str(err.value)


def test_control_429_mentions_the_wait():
    """Control 429 mentions the wait."""
    url = f"{BASE}/api/printers/voron/pause"
    routes = {("post", url): _Response(429, {"reason": "too many failed attempts"}, {"Retry-After": "30"})}
    with pytest.raises(api.PrintHandyApiError) as err:
        _run(_client(routes).async_printer_control("voron", "pause"))
    assert "30 seconds" in str(err.value)


def test_spoolman_null_parses_as_unknown():
    """Spoolman null parses as unknown."""
    from custom_components.print_handy.model import parse_snapshot

    assert parse_snapshot(status_body(MOONRAKER_ROW, spoolman_ok=None)).instance.spoolman_ok is None
    assert parse_snapshot(status_body(MOONRAKER_ROW, spoolman_ok=False)).instance.spoolman_ok is False
