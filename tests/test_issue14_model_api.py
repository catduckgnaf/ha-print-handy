"""Issue #14 remainder, HA-free part: model parsing and the camera client.

Covers the data the five remaining features ride on: doctor rows kept
per check (Repairs and the doctor binary sensors), the roster camera
URL (the camera entity), Bambu fault data in the live status (the
fault sensor) and the snapshot-proxy read in the API client.
"""

import asyncio

import pytest

from custom_components.print_handy import api
from custom_components.print_handy.model import parse_printer_status, parse_snapshot
from tests.fixtures import MOONRAKER_ROW, printer_body, status_body

CAMERA_ROW = {
    **MOONRAKER_ROW,
    "id": "cam-printer",
    "camera_snapshot_url": "http://cam.local/snapshot",
}

DOCTOR = [
    {"label": "settings", "status": "ok", "detail": "fine"},
    {"label": "orca cli", "status": "fail", "detail": "orca not found"},
    {"label": "logging", "status": "warn", "detail": "log is large"},
]


def test_snapshot_keeps_doctor_rows_and_counts():
    """Doctor rows survive parsing per check, counts unchanged."""
    snap = parse_snapshot(status_body(MOONRAKER_ROW, doctor=DOCTOR))
    checks = snap.instance.doctor_checks
    assert [(c.label, c.status, c.detail) for c in checks] == [
        ("settings", "ok", "fine"),
        ("orca cli", "fail", "orca not found"),
        ("logging", "warn", "log is large"),
    ]
    assert snap.instance.doctor_failures == 1
    assert snap.instance.doctor_warnings == 1


def test_snapshot_doctor_rows_default_empty_and_tolerant():
    """A snapshot without doctor rows, or with junk rows, parses cleanly."""
    body = status_body(MOONRAKER_ROW)
    del body["doctor"]
    assert parse_snapshot(body).instance.doctor_checks == ()
    body["doctor"] = [{"status": "fail"}, "junk", {"label": "x", "status": "ok"}]
    checks = parse_snapshot(body).instance.doctor_checks
    assert [(c.label, c.status) for c in checks] == [("x", "ok")]


def test_roster_camera_url_is_parsed():
    """A roster row carrying a camera snapshot URL marks the printer."""
    snap = parse_snapshot(status_body(CAMERA_ROW, MOONRAKER_ROW))
    cam = snap.printer("cam-printer")
    plain = snap.printer("voron")
    assert cam is not None and cam.has_camera is True
    assert cam.camera_snapshot_url == "http://cam.local/snapshot"
    assert plain is not None and plain.has_camera is False


def test_printer_status_parses_bambu_faults():
    """hms_errors and print_error_text ride into the parsed status."""
    body = printer_body(
        "x1c",
        state="error",
        hms_errors=[
            {"code": "0300_8000", "description": "Filament runout", "listed": True},
            {"code": "0500_1234", "description": None, "listed": False},
        ],
        print_error_text="Filament runout",
    )
    status = parse_printer_status(body, "x1c")
    assert status.hms_errors is not None
    assert [(e.code, e.description, e.listed) for e in status.hms_errors] == [
        ("0300_8000", "Filament runout", True),
        ("0500_1234", None, False),
    ]
    assert status.print_error_text == "Filament runout"


def test_printer_status_without_fault_keys_has_none():
    """Families that send no fault data keep hms_errors None (no entity)."""
    status = parse_printer_status(printer_body("voron"), "voron")
    assert status.hms_errors is None
    assert status.print_error_text == ""


class _BytesResponse:
    def __init__(self, status, payload, body=None):
        self.status = status
        self._payload = payload
        self._body = body

    async def read(self):
        return self._payload

    async def json(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _BytesSession:
    def __init__(self, response):
        self._response = response
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        return self._response


def test_camera_snapshot_returns_jpeg_bytes():
    """The proxy read returns the JPEG and sends the token header."""
    jpeg = b"\xff\xd8\xff\xe0jpeg-bytes"
    session = _BytesSession(_BytesResponse(200, jpeg))
    client = api.PrintHandyApiClient(
        session=session, base_url="http://ph.local:8080", token="sekrit"
    )
    out = asyncio.run(client.async_get_camera_snapshot("voron"))
    assert out == jpeg
    url, headers = session.calls[0]
    assert url == "http://ph.local:8080/api/printers/voron/camera/snapshot"
    assert headers == {"X-WebUI-Token": "sekrit"}


def test_camera_snapshot_error_carries_server_reason():
    """A refused snapshot raises an API error naming the server's reason."""
    session = _BytesSession(
        _BytesResponse(404, b'{"ok": false, "reason": "no such printer"}')
    )
    client = api.PrintHandyApiClient(session=session, base_url="http://ph.local:8080")
    with pytest.raises(api.PrintHandyApiError) as excinfo:
        asyncio.run(client.async_get_camera_snapshot("nope"))
    assert "no such printer" in str(excinfo.value)
