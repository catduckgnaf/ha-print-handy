"""Tests for the #789 spools view parser (no Home Assistant needed).

The body shape is the one Print Handy's webui/spools.py sends and its
tests/test_webui_spools.py pins: slots 1-4 with a spool summary or
null, the shelf, every spool, and named conflicts.
"""

import pytest

from custom_components.print_handy.model import (
    parse_spools_view,
    slot_spool_attributes,
    unavailable_spools,
)

SPOOLS_BODY = {
    "ok": True,
    "id": "flashforge-ad5x",
    "name": "AD5X",
    "model": "flashforge_ad5x",
    "slots": [
        {"slot": 1, "spool": None},
        {
            "slot": 2,
            "spool": {
                "id": 3,
                "name": "Pink PLA",
                "color": "Pink",
                "material": "PLA",
                "brand": "Sunlu",
                "color_hex": "F95D73",
                "remaining_weight": 500.0,
                "initial_weight": 1000.0,
                "remaining_percent": 50.0,
                "archived": False,
                "location": "IFS Slot 2",
            },
        },
        {"slot": 3, "spool": None},
        {"slot": 4, "spool": None},
    ],
    "shelf": [
        {
            "id": 7,
            "name": "Coffee PLA+",
            "color": "Coffee",
            "material": "PLA+",
            "brand": "eSun",
            "color_hex": "6F4E37",
            "remaining_weight": 800.0,
            "initial_weight": 1000.0,
            "remaining_percent": 80.0,
            "archived": False,
            "location": "Shelf",
        },
        {
            "id": 9,
            "name": "Black PLA",
            "color": "Black",
            "material": "PLA",
            "brand": "Sunlu",
            "color_hex": "161616",
            "remaining_weight": 250.0,
            "initial_weight": None,
            "remaining_percent": None,
            "archived": False,
            "location": "Shelf",
        },
    ],
    "spools": [],
    "conflicts": [],
}
SPOOLS_BODY["spools"] = [
    SPOOLS_BODY["slots"][1]["spool"],
    *SPOOLS_BODY["shelf"],
]


def test_spools_view_parses_slots_shelf_and_percent():
    """Slots, shelf and the percent-where-known rule parse as sent."""
    view = parse_spools_view(SPOOLS_BODY)
    assert view.printer_id == "flashforge-ad5x"
    assert view.available is True
    assert [row.slot for row in view.slots] == [1, 2, 3, 4]
    slot2 = view.slot(2)
    assert slot2 is not None and slot2.spool is not None
    assert slot2.spool.spool_id == 3
    assert slot2.spool.material == "PLA"
    assert slot2.spool.color == "Pink"
    assert slot2.spool.remaining_weight == 500.0
    assert slot2.spool.remaining_percent == 50.0
    assert view.slot(1) is not None and view.slot(1).spool is None
    assert [spool.spool_id for spool in view.shelf] == [7, 9]
    # No starting weight recorded: percent stays None, never estimated.
    assert view.shelf[1].remaining_percent is None
    assert view.conflicts == ()


def test_spools_view_keeps_named_conflicts():
    """Duplicate or archived claims arrive as named conflicts."""
    body = dict(SPOOLS_BODY)
    body["conflicts"] = ["Slot 2: spools #3 and #9 both claim this slot"]
    view = parse_spools_view(body)
    assert view.conflicts == ("Slot 2: spools #3 and #9 both claim this slot",)


def test_spools_view_type_guards():
    """Bools are not readings; bad rows are skipped, not guessed at."""
    body = {
        "id": "p",
        "slots": [
            {"slot": True, "spool": None},
            "junk",
            {"slot": 1, "spool": {"id": 5, "remaining_weight": True}},
        ],
    }
    view = parse_spools_view(body)
    assert [row.slot for row in view.slots] == [1]
    spool = view.slot(1).spool
    assert spool is not None and spool.remaining_weight is None
    with pytest.raises(TypeError):
        parse_spools_view(["not", "a", "dict"])
    with pytest.raises(TypeError):
        parse_spools_view({"id": "p"})


def test_unavailable_spools_view():
    """The unavailable view is explicit: not available, with a reason."""
    view = unavailable_spools("ghost", "no printer with id 'ghost'")
    assert view.available is False
    assert view.slots == ()
    assert "ghost" in view.detail


def test_slot_spool_attributes_carry_material_color_and_weight():
    """The slot sensor's grouped attributes come from the poll view."""
    view = parse_spools_view(SPOOLS_BODY)
    attrs = slot_spool_attributes(view, 2)
    assert attrs["slot"] == 2
    assert attrs["spool_id"] == 3
    assert attrs["material"] == "PLA"
    assert attrs["color"] == "Pink"
    assert attrs["remaining_weight"] == 500.0
    assert attrs["remaining_percent"] == 50.0
    empty = slot_spool_attributes(view, 1)
    assert empty == {"slot": 1, "spool_id": None}
    assert slot_spool_attributes(None, 3) == {"slot": 3, "spool_id": None}
