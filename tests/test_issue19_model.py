"""Model tests for issue #19: hardware facts and spool nicknames."""

from custom_components.print_handy.model import (
    parse_hardware,
    parse_printer_status,
    parse_spools_view,
    shelf_attributes,
    slot_spool_attributes,
)
from tests.fixtures import printer_body
from tests.test_spool_model import SPOOLS_BODY

HARDWARE = {
    "source": "klipper",
    "diameter_mm": 0.4,
    "type": "hardened_steel",
    "type_note": "abrasive ready",
    "installed": "2026-11-01",
    "z_offset_mm": -0.12,
    "z_offset_verified": True,
    "temp_offset_c": 5.0,
    "max_c": 320.0,
    "pending": ["bed_mesh", "z_offset"],
    "mesh_stale": True,
    "materials": [
        {"family": "PLA", "nozzle_c": 210.0, "base_nozzle_c": 205.0, "bed_c": 60.0},
        {"family": "PETG", "nozzle_c": 240.0, "base_nozzle_c": 235.0, "bed_c": 80.0},
    ],
}


def test_parse_printer_status_with_hardware_block():
    """A hardware sibling yields PrinterHardware with materials."""
    body = printer_body("voron")
    body["hardware"] = HARDWARE
    status = parse_printer_status(body, "voron")
    assert status.hardware is not None
    hw = status.hardware
    assert hw.source == "klipper"
    assert hw.diameter_mm == 0.4
    assert hw.nozzle_type == "hardened_steel"
    assert hw.type_note == "abrasive ready"
    assert hw.installed == "2026-11-01"
    assert hw.z_offset_mm == -0.12
    assert hw.z_offset_verified is True
    assert hw.temp_offset_c == 5.0
    assert hw.max_c == 320.0
    assert hw.calibration_pending == ("bed_mesh", "z_offset")
    assert hw.bed_mesh_stale is True
    assert len(hw.materials) == 2
    pla = hw.materials[0]
    assert pla.family == "PLA"
    assert pla.nozzle_c == 210.0
    assert pla.bed_c == 60.0
    assert pla.base_nozzle_c == 205.0
    assert pla.slug == "pla"


def test_parse_printer_status_without_hardware_is_none():
    """Without the hardware key, hardware stays None."""
    status = parse_printer_status(printer_body("voron"), "voron")
    assert status.hardware is None
    assert parse_hardware(None) is None
    assert parse_hardware("junk") is None


def test_parse_hardware_tolerates_missing_fields():
    """A sparse hardware block parses with defaults."""
    hw = parse_hardware({"diameter_mm": 0.6})
    assert hw is not None
    assert hw.diameter_mm == 0.6
    assert hw.nozzle_type == ""
    assert hw.materials == ()
    assert hw.calibration_pending == ()


def test_nickname_parses_into_spool_summary():
    """A spool nickname rides into SpoolSummary and the attributes."""
    import copy

    body = copy.deepcopy(SPOOLS_BODY)
    body["slots"][1]["spool"]["nickname"] = "Pinkie"
    body["shelf"][0]["nickname"] = "Coffee time"
    # Keep the combined spools list consistent for shelf parsing.
    view = parse_spools_view(body)
    slot2 = view.slot(2)
    assert slot2 is not None and slot2.spool is not None
    assert slot2.spool.nickname == "Pinkie"
    attrs = slot_spool_attributes(view, 2)
    assert attrs["nickname"] == "Pinkie"
    shelf = shelf_attributes(view)
    coffee = next(row for row in shelf["spools"] if row["id"] == 7)
    assert coffee["nickname"] == "Coffee time"
    black = next(row for row in shelf["spools"] if row["id"] == 9)
    assert "nickname" not in black


def test_spool_without_nickname_has_empty_string():
    """Spools that send no nickname keep the empty default."""
    view = parse_spools_view(SPOOLS_BODY)
    slot2 = view.slot(2)
    assert slot2 is not None and slot2.spool is not None
    assert slot2.spool.nickname == ""
    assert "nickname" not in slot_spool_attributes(view, 2)
