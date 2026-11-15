"""Tests for the issue #19 API client additions: upload and nozzle record.

Upload uses a fake session accepting the ``data=`` kwarg the multipart
call passes; nozzle record goes through the control POST path.
"""

import asyncio

import pytest

pytest.importorskip("aiohttp")

from custom_components.print_handy import api  # noqa: E402


class _FakeResponse:
    def __init__(self, status, body=None, headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def json(self):
        return self._body

    async def read(self):
        return b""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _UploadSession:
    """Fake session recording POSTs with their data kwarg."""

    def __init__(self, routes):
        self._routes = routes
        self.calls = []

    def post(self, url, headers=None, json=None, data=None):
        self.calls.append(
            {"url": url, "headers": headers or {}, "json": json, "data": data}
        )
        route = self._routes[url]
        if isinstance(route, Exception):
            raise route
        return route


def _client(session, token=""):
    return api.PrintHandyApiClient(
        session=session, base_url="http://ph.local:8080", token=token
    )


def test_upload_posts_multipart_to_upload_path():
    """Upload POSTs multipart form data to /api/upload and returns the body."""
    url = "http://ph.local:8080/api/upload"
    session = _UploadSession({url: _FakeResponse(200, {"ok": True, "name": "benchy.stl"})})
    result = asyncio.run(
        _client(session, token="sekrit").async_upload_file("benchy.stl", b"solid data")
    )
    assert result == {"ok": True, "name": "benchy.stl"}
    assert len(session.calls) == 1
    call = session.calls[0]
    assert call["url"] == url
    assert call["headers"] == {"X-WebUI-Token": "sekrit"}
    assert call["data"] is not None
    # aiohttp FormData introspection is awkward; the recorded kwarg
    # carrying a FormData instance for the right file is the check.
    assert type(call["data"]).__name__ == "FormData"


def test_upload_404_with_reason_raises_carrying_it():
    """A 404 on a server predating the endpoint raises carrying its reason."""
    url = "http://ph.local:8080/api/upload"
    body = {"ok": False, "reason": "no such route"}
    session = _UploadSession({url: _FakeResponse(404, body)})
    with pytest.raises(api.PrintHandyApiError) as excinfo:
        asyncio.run(_client(session).async_upload_file("benchy.stl", b"data"))
    assert "no such route" in str(excinfo.value)


def test_upload_401_is_auth_error():
    """A rejected token on upload is the auth error."""
    url = "http://ph.local:8080/api/upload"
    session = _UploadSession({url: _FakeResponse(401, {})})
    with pytest.raises(api.PrintHandyAuthError):
        asyncio.run(_client(session, token="wrong").async_upload_file("a.stl", b"x"))


def test_nozzle_record_posts_to_nozzle_path():
    """Recording a nozzle change POSTs {diameter_mm, type} to the nozzle path."""
    url = "http://ph.local:8080/api/printers/ad5x/nozzle"
    session = _UploadSession({url: _FakeResponse(200, {"ok": True})})
    # The control path uses _async_request_json, which calls post with json=.
    asyncio.run(_client(session, token="sekrit").async_record_nozzle_change("ad5x", 0.6, "hardened_steel"))
    call = session.calls[0]
    assert call["url"] == url
    assert call["headers"] == {"X-WebUI-Token": "sekrit"}
    assert call["json"] == {"diameter_mm": 0.6, "type": "hardened_steel"}


def test_nozzle_record_refusal_raises_control_refused():
    """A refused nozzle record raises the control refusal with its reason."""
    url = "http://ph.local:8080/api/printers/ad5x/nozzle"
    body = {"ok": False, "reason": "controls are disabled"}
    session = _UploadSession({url: _FakeResponse(403, body)})
    with pytest.raises(api.PrintHandyControlRefusedError) as excinfo:
        asyncio.run(_client(session).async_record_nozzle_change("ad5x", 0.4, "brass"))
    assert "controls are disabled" in str(excinfo.value)
