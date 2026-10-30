"""Tests for the /api/status snapshot parser (no Home Assistant needed)."""

import pytest

from custom_components.print_handy.model import parse_snapshot

MULTI_PRINTER = {
    "version": "2.2.1",
    "host": "print-handy-box",
    "ok": True,
    "printer": {"model": "flashforge.ad5x", "name": "AD5X", "vendor": "flashforge",
                "connection": "moonraker"},
    "bot": {"platform": "discord", "mode": "standalone", "ok": True,
            "reason": "healthy",
            "heartbeat": {"connected": True, "user": "printy", "mode": "standalone"}},
    "listener": {
        "required": True, "ok": True, "reason": "ad5x: healthy; sv06: healthy",
        "printers": {
            "ad5x": {"required": True, "ok": True, "reason": "healthy"},
            "sv06": {"required": True, "ok": False, "reason": "no heartbeat"},
        },
    },
    "printers": [
        {"id": "ad5x", "name": "AD5X", "model": "flashforge.ad5x",
         "vendor": "flashforge", "connection": "moonraker", "default": True},
        {"id": "sv06", "name": "", "model": "sovol.sv06",
         "vendor": "sovol", "connection": "moonraker", "default": False},
    ],
    "spoolman": {"bundled": True, "url": "http://127.0.0.1:8000", "ok": True,
                 "detail": "ok"},
    "services": {},
    "doctor": [{"label": "settings", "status": "ok", "detail": "fine"},
               {"label": "orca cli", "status": "warn", "detail": "slow"},
               {"label": "core", "status": "fail", "detail": "boom"}],
}

SINGLE_PRINTER = {
    "version": "2.2.1",
    "host": "pi",
    "ok": False,
    "bot": {"platform": "telegram", "mode": "standalone", "ok": False,
            "reason": "stale", "heartbeat": None},
    "listener": {"required": True, "ok": False, "reason": "no heartbeat"},
    "printers": [
        {"id": "ender", "name": "Ender", "model": "creality.ender3v3",
         "vendor": "creality", "connection": "moonraker", "default": True},
    ],
    "spoolman": {"bundled": False, "url": "", "ok": False, "detail": "not configured"},
    "doctor": [],
}


def test_multi_printer_snapshot():
    """Roster rows pick up their own nested listener rows."""
    snap = parse_snapshot(MULTI_PRINTER)
    assert snap.instance.version == "2.2.1"
    assert snap.instance.ok is True
    assert snap.instance.bot_connected is True
    assert snap.instance.spoolman_ok is True
    assert snap.instance.doctor_failures == 1
    assert snap.instance.doctor_warnings == 1

    assert [p.printer_id for p in snap.printers] == ["ad5x", "sv06"]
    ad5x = snap.printer("ad5x")
    assert ad5x is not None and ad5x.is_default and ad5x.listener_ok is True
    sv06 = snap.printer("sv06")
    assert sv06 is not None and sv06.listener_ok is False
    assert sv06.display_name == "sv06"  # blank name falls back to the id


def test_single_printer_snapshot_uses_section_listener():
    """With no nested map, the section row belongs to the default printer."""
    snap = parse_snapshot(SINGLE_PRINTER)
    ender = snap.printer("ender")
    assert ender is not None
    assert ender.listener_required is True
    assert ender.listener_ok is False
    assert snap.instance.bot_connected is None  # no heartbeat record
    assert snap.instance.ok is False


def test_offline_connection_printer_has_no_listener_row():
    """A printer needing no listener reads ok=None (unknown), never False."""
    raw = dict(SINGLE_PRINTER)
    raw["listener"] = {"required": False, "ok": None, "reason": "not required"}
    snap = parse_snapshot(raw)
    ender = snap.printer("ender")
    assert ender is not None and ender.listener_ok is None


def test_bad_snapshots_raise():
    """Non-objects and roster-less bodies are rejected, not guessed at."""
    with pytest.raises(TypeError):
        parse_snapshot(["not", "a", "dict"])
    with pytest.raises(TypeError):
        parse_snapshot({"version": "2.2.1"})
