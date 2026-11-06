"""Tests for the /api/status snapshot parser (no Home Assistant needed)."""

import pytest

from custom_components.print_handy.model import (
    active_ams_tray,
    ams_trays,
    has_ams_filament,
    parse_printer_status,
    parse_snapshot,
)

BAMBU_STATUS_BODY = {
    "ok": True,
    "id": "a1",
    "name": "A1",
    "model": "bambulab.a1",
    "vendor": "bambulab",
    "connection": "bambulab_lan",
    "status": {
        "state": "printing",
        "filename": "benchy.gcode",
        "progress": 0.42,
        "hotend_c": 220.5,
        "bed_c": 65,
        "hotend_target_c": 220,
        "bed_target_c": 65,
        "remaining_min": 83,
        "layer": 120,
        "total_layer": 640,
        "ams": {
            "units": [
                {
                    "id": 0,
                    "kind": "ams",
                    "trays": [
                        {"slot": 0, "present": True, "material": "PLA",
                         "name": "Galaxy Black", "color": "000000",
                         "remaining_pct": 62, "active": True},
                        {"slot": 1, "present": False, "material": None,
                         "name": None, "color": None,
                         "remaining_pct": None, "active": False},
                    ],
                }
            ],
            "external": None,
            "active_tray": 0,
            "filament_backup": None,
        },
    },
}

ELEGOO_STATUS_BODY = {
    "ok": True,
    "id": "centauri",
    "status": {
        "state": "printing",
        "filename": "cube.gcode",
        "progress": 0.25,
        "hotend_c": 200,
        "bed_c": 60,
        "hotend_target_c": 200,
        "bed_target_c": 60,
        "layer": 30,
        "total_layers": 120,  # Elegoo SDCP spells it plural
        "remaining_min": 45,
        "status_code": 13,
    },
}

UNAVAILABLE_STATUS_BODY = {
    "ok": True,
    "id": "ad5x",
    "status": {
        "state": "unavailable",
        "filename": "",
        "progress": None,
        "hotend_c": None,
        "bed_c": None,
        "detail": "ValueError: no Moonraker URL is configured for this printer",
    },
}

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


def test_bambu_printer_status_full_shape():
    """Every phase-2 field parses, including the singular total_layer."""
    status = parse_printer_status(BAMBU_STATUS_BODY)
    assert status.printer_id == "a1"
    assert status.state == "printing"
    assert status.filename == "benchy.gcode"
    assert status.progress == 0.42
    assert status.hotend_c == 220.5
    assert status.bed_c == 65.0
    assert status.hotend_target_c == 220.0
    assert status.bed_target_c == 65.0
    assert status.remaining_min == 83.0
    assert status.layer == 120
    assert status.total_layers == 640
    assert status.unavailable is False


def test_elegoo_printer_status_plural_total_layers():
    """The SDCP spelling total_layers lands in the same field."""
    status = parse_printer_status(ELEGOO_STATUS_BODY, "centauri")
    assert status.total_layers == 120
    assert status.layer == 30
    assert status.remaining_min == 45.0
    assert status.ams is None
    assert has_ams_filament(status) is False


def test_unavailable_printer_status_body():
    """A known-but-unreadable printer parses as state unavailable + detail."""
    status = parse_printer_status(UNAVAILABLE_STATUS_BODY)
    assert status.state == "unavailable"
    assert status.unavailable is True
    assert status.progress is None
    assert status.hotend_c is None
    assert "no Moonraker URL" in status.detail


def test_printer_status_defaults_and_type_guards():
    """Missing fields stay None; bools/strings are not readings."""
    status = parse_printer_status(
        {"id": "p", "status": {"state": "standby", "progress": True,
                               "hotend_c": "hot", "layer": 2.0}}
    )
    assert status.state == "standby"
    assert status.progress is None  # bool is not a reading
    assert status.hotend_c is None  # string is not a reading
    assert status.layer == 2
    assert status.filename == ""
    assert status.remaining_min is None


def test_bad_printer_status_bodies_raise():
    """Non-objects and status-less bodies are rejected."""
    with pytest.raises(TypeError):
        parse_printer_status("nope")
    with pytest.raises(TypeError):
        parse_printer_status({"ok": True, "id": "p"})


def test_printer_status_parses_object_fields():
    """Current object, object list and excluded list all parse (issue #1)."""
    body = {
        "id": "ad5x",
        "status": {
            "state": "printing",
            "current_object": "cube_1",
            "objects": ["cube_1", "cube_2"],
            "excluded_objects": ["cube_2"],
        },
    }
    status = parse_printer_status(body)
    assert status.current_object == "cube_1"
    assert status.objects == ("cube_1", "cube_2")
    assert status.excluded_objects == ("cube_2",)


def test_printer_status_without_object_keys_degrades():
    """A status body with no object keys (older Print Handy) stays empty."""
    status = parse_printer_status(BAMBU_STATUS_BODY)
    assert status.current_object is None
    assert status.objects == ()
    assert status.excluded_objects == ()


def test_printer_status_object_fields_tolerate_bad_shapes():
    """Null / wrong-typed object fields degrade, never raise."""
    body = {
        "id": "p",
        "status": {
            "state": "printing",
            "current_object": None,
            "objects": "not-a-list",
            "excluded_objects": [42, None, "cube_9", ""],
        },
    }
    status = parse_printer_status(body)
    assert status.current_object is None
    assert status.objects == ()
    assert status.excluded_objects == ("cube_9",)
    body["status"]["current_object"] = 7
    assert parse_printer_status(body).current_object is None


def test_ams_helpers_find_active_tray():
    """The active, present tray is the filament; trays carry their unit."""
    status = parse_printer_status(BAMBU_STATUS_BODY)
    assert has_ams_filament(status) is True
    tray = active_ams_tray(status)
    assert tray is not None
    assert tray["name"] == "Galaxy Black"
    assert tray["material"] == "PLA"
    assert tray["remaining_pct"] == 62
    assert tray["unit"] == 0
    assert len(ams_trays(status)) == 2


def test_ams_external_spool_can_be_active():
    """The external spool counts when it is the active, present tray."""
    body = {
        "id": "x1c",
        "status": {
            "state": "printing",
            "ams": {
                "units": [],
                "external": {"slot": 254, "present": True, "material": "PETG",
                             "name": "Sunlu PETG", "color": "FF0000",
                             "remaining_pct": None, "active": True},
                "active_tray": 254,
                "filament_backup": False,
            },
        },
    }
    status = parse_printer_status(body)
    tray = active_ams_tray(status)
    assert tray is not None and tray["material"] == "PETG"
    assert tray["unit"] == "external"
