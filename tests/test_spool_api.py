"""Tests for the #789 spool API client, using a fake aiohttp-style session.

Request and response shapes are the ones Print Handy's
webui/spools.py sends: GET slots/shelf/spools/conflicts, POST bodies
``{slot, spool_id}`` / ``{slot_a, slot_b}`` / ``{slot}`` /
``{brand, material, color, grams, hex}`` / no body for sync, and
refusals as ``{ok: false, reason}`` with a named HTTP status.
"""

import asyncio

import pytest

from custom_components.print_handy import api
from tests.test_spool_model import SPOOLS_BODY


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


class _Session:
    """Fake session dispatching GET and POST on the exact URL."""

    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append(("get", url, headers or {}, None))
        route = self._routes[url]
        if isinstance(route, Exception):
            raise route
        return route

    def post(self, url, headers=None, json=None):
        self.calls.append(("post", url, headers or {}, json))
        route = self._routes[url]
        if isinstance(route, Exception):
            raise route
        return route


def _client(session, token=""):
    return api.PrintHandyApiClient(
        session=session, base_url="http://ph.local:8080", token=token
    )


def _spools_url(printer_id, action=None):
    base = f"http://ph.local:8080/api/printers/{printer_id}/spools"
    return f"{base}/{action}" if action else base


def test_get_printer_spools_parses_and_sends_token():
    """A 200 spools body parses; the token header goes out as usual."""
    session = _Session({_spools_url("flashforge-ad5x"): _FakeResponse(200, SPOOLS_BODY)})
    view = asyncio.run(
        _client(session, token="sekrit").async_get_printer_spools("flashforge-ad5x")
    )
    assert view.available is True
    assert view.slot(2).spool.spool_id == 3
    method, url, headers, _ = session.calls[0]
    assert method == "get"
    assert url == _spools_url("flashforge-ad5x")
    assert headers == {"X-WebUI-Token": "sekrit"}


def test_get_printer_spools_quotes_the_id():
    """Printer ids are URL-quoted into the spools path."""
    session = _Session({_spools_url("my%20printer"): _FakeResponse(200, SPOOLS_BODY)})
    asyncio.run(_client(session).async_get_printer_spools("my printer"))
    assert session.calls[0][1] == _spools_url("my%20printer")


def test_get_printer_spools_404_is_unavailable_not_error():
    """An unknown id (or endpoint-less old server) reads as unavailable."""
    body = {"ok": False, "reason": "no printer with id 'ghost' is configured"}
    session = _Session({_spools_url("ghost"): _FakeResponse(404, body)})
    view = asyncio.run(_client(session).async_get_printer_spools("ghost"))
    assert view.available is False
    assert "no printer with id 'ghost'" in view.detail


def test_get_printer_spool_views_tolerates_per_printer_failures():
    """One printer's 500/transport failure never fails the batch."""
    session = _Session(
        {
            _spools_url("good"): _FakeResponse(200, SPOOLS_BODY),
            _spools_url("sad"): _FakeResponse(500, {}),
            _spools_url("down"): OSError("connection reset"),
        }
    )
    views = asyncio.run(
        _client(session).async_get_printer_spool_views(["good", "sad", "down"])
    )
    assert set(views) == {"good", "sad", "down"}
    assert views["good"].available is True
    assert views["sad"].available is False
    assert views["down"].available is False


def test_get_printer_spool_views_propagates_auth_errors():
    """A 401 on any spools view means the entry's token is wrong."""
    session = _Session(
        {
            _spools_url("good"): _FakeResponse(200, SPOOLS_BODY),
            _spools_url("locked"): _FakeResponse(401, {"ok": False}),
        }
    )
    with pytest.raises(api.PrintHandyAuthError):
        asyncio.run(
            _client(session, token="wrong").async_get_printer_spool_views(
                ["good", "locked"]
            )
        )


def test_assign_posts_slot_and_spool_id():
    """Assign POSTs {slot, spool_id} to that printer's assign endpoint."""
    session = _Session(
        {_spools_url("ad5x", "assign"): _FakeResponse(200, {"ok": True})}
    )
    asyncio.run(_client(session, token="sekrit").async_assign_spool("ad5x", 1, 7))
    method, url, headers, body = session.calls[0]
    assert method == "post"
    assert url == _spools_url("ad5x", "assign")
    assert headers == {"X-WebUI-Token": "sekrit"}
    assert body == {"slot": 1, "spool_id": 7}


def test_swap_posts_both_slots():
    """Swap POSTs {slot_a, slot_b} to that printer's swap endpoint."""
    session = _Session({_spools_url("ad5x", "swap"): _FakeResponse(200, {"ok": True})})
    asyncio.run(_client(session).async_swap_spools("ad5x", 1, 2))
    assert session.calls[0][1] == _spools_url("ad5x", "swap")
    assert session.calls[0][3] == {"slot_a": 1, "slot_b": 2}


def test_unassign_posts_the_slot():
    """Unassign POSTs {slot} to that printer's unassign endpoint."""
    session = _Session(
        {_spools_url("ad5x", "unassign"): _FakeResponse(200, {"ok": True})}
    )
    asyncio.run(_client(session).async_unassign_spool("ad5x", 2))
    assert session.calls[0][1] == _spools_url("ad5x", "unassign")
    assert session.calls[0][3] == {"slot": 2}


def test_add_posts_the_creation_fields():
    """Add POSTs the API's creation body; optional fields ride when given."""
    session = _Session({_spools_url("ad5x", "add"): _FakeResponse(200, {"ok": True})})
    asyncio.run(
        _client(session).async_add_spool(
            "ad5x", material="PLA", color="White", grams=1000, brand="Sunlu"
        )
    )
    assert session.calls[0][1] == _spools_url("ad5x", "add")
    assert session.calls[0][3] == {
        "material": "PLA",
        "color": "White",
        "grams": 1000.0,
        "brand": "Sunlu",
    }


def test_add_omits_optional_fields_when_not_given():
    """No brand or hex key is sent for a plain add."""
    session = _Session({_spools_url("ad5x", "add"): _FakeResponse(200, {"ok": True})})
    asyncio.run(
        _client(session).async_add_spool("ad5x", material="PLA", color="White", grams=500)
    )
    assert session.calls[0][3] == {"material": "PLA", "color": "White", "grams": 500.0}


def test_sync_posts_no_body():
    """Sync POSTs to the sync endpoint with no body, per the API contract."""
    session = _Session({_spools_url("ad5x", "sync"): _FakeResponse(200, {"ok": True})})
    asyncio.run(_client(session).async_sync_spools("ad5x"))
    assert session.calls[0][1] == _spools_url("ad5x", "sync")
    assert session.calls[0][3] is None


@pytest.mark.parametrize(
    ("status", "reason"),
    [
        (403, "spool controls are disabled: set controls_enabled"),
        (404, "no printer with id 'ghost' is configured"),
        (409, "Spoolman has no spool #999"),
        (400, "slot 9 is not a slot (slots are 1-4). Nothing changed."),
        (502, "the spool operation did not complete"),
    ],
)
def test_spool_refusals_carry_the_server_reason(status, reason):
    """Every named refusal raises the spool refusal error with its reason."""
    session = _Session(
        {_spools_url("ad5x", "assign"): _FakeResponse(status, {"ok": False, "reason": reason})}
    )
    with pytest.raises(api.PrintHandySpoolRefusedError) as excinfo:
        asyncio.run(_client(session).async_assign_spool("ad5x", 1, 7))
    assert reason in str(excinfo.value)


def test_spool_401_is_auth_error_not_refusal():
    """A rejected token on a spool POST is the auth error."""
    session = _Session({_spools_url("ad5x", "sync"): _FakeResponse(401, {})})
    with pytest.raises(api.PrintHandyAuthError):
        asyncio.run(_client(session, token="wrong").async_sync_spools("ad5x"))


def test_spool_500_without_reason_is_generic_api_error():
    """A server error with no named reason is not dressed as a refusal."""
    session = _Session({_spools_url("ad5x", "sync"): _FakeResponse(500, {})})
    with pytest.raises(api.PrintHandyApiError) as excinfo:
        asyncio.run(_client(session).async_sync_spools("ad5x"))
    assert type(excinfo.value) is api.PrintHandyApiError


def test_spool_transport_failure_is_connection_error():
    """An unreachable Web UI on a spool POST is a connection error."""
    session = _Session({_spools_url("ad5x", "sync"): OSError("no route")})
    with pytest.raises(api.PrintHandyConnectionError):
        asyncio.run(_client(session).async_sync_spools("ad5x"))


def test_unknown_spool_action_is_rejected_without_a_post():
    """An action outside the contract is a programming error, not a POST."""
    session = _Session({})
    with pytest.raises(ValueError, match="unknown spool action"):
        asyncio.run(_client(session)._async_post_spool("ad5x", "explode"))
    assert session.calls == []
