"""Tests for the 0.6.3 dashboard data: spool shelf sensor, bot attributes.

The model half runs anywhere; the Home Assistant half reuses the
recorded-response fake client from test_spools.py and is skipped
without the pytest-homeassistant-custom-component harness.
"""

import json
from unittest.mock import patch

import pytest

from custom_components.print_handy.model import (
    SHELF_ATTRIBUTE_LIMIT,
    InstanceInfo,
    bot_attributes,
    parse_snapshot,
    parse_spools_view,
    shelf_attributes,
    shelf_spools,
    unavailable_spools,
)
from tests.fixtures import MOONRAKER_ROW, status_body
from tests.test_spool_model import SPOOLS_BODY

BOT_HEARTBEAT = {
    "connected": True,
    "user": "PrintHandy#4242",
    "handlers": 12,
    "mode": "standalone",
    "platform": "discord",
    "heartbeat_at": 1_800_000_000,
}


# -- model -----------------------------------------------------------------


def test_shelf_attributes_list_the_shelf_spools():
    """Each shelf spool carries the fields a dashboard needs."""
    view = parse_spools_view(SPOOLS_BODY)
    attrs = shelf_attributes(view)
    assert [row["id"] for row in attrs["spools"]] == [7, 9]
    assert attrs["spools"][0] == {
        "id": 7,
        "name": "Coffee PLA+",
        "material": "PLA+",
        "color": "Coffee",
        "color_hex": "6F4E37",
        "brand": "eSun",
        "remaining_weight": 800.0,
        "initial_weight": 1000.0,
        "remaining_percent": 80.0,
        "location": "Shelf",
    }
    # No starting weight recorded: percent stays None, never estimated.
    assert attrs["spools"][1]["remaining_percent"] is None
    assert attrs["total_remaining_weight"] == 1050.0
    assert attrs["truncated"] is False
    assert "conflicts" not in attrs


def test_shelf_leaves_out_archived_spools_and_names_conflicts():
    """Archived rows never count; the view's conflicts ride along."""
    archived = {**SPOOLS_BODY["shelf"][0], "id": 99, "archived": True}
    body = {
        **SPOOLS_BODY,
        "shelf": [*SPOOLS_BODY["shelf"], archived],
        "conflicts": ["Slot 2: spools #3 and #9 both claim this slot"],
    }
    view = parse_spools_view(body)
    assert [spool.spool_id for spool in shelf_spools(view)] == [7, 9]
    attrs = shelf_attributes(view)
    assert [row["id"] for row in attrs["spools"]] == [7, 9]
    assert attrs["conflicts"] == ["Slot 2: spools #3 and #9 both claim this slot"]


def test_shelf_attributes_are_capped_and_stay_recordable():
    """A big shelf is truncated well under the recorder's 16 KB limit."""
    row = {
        **SPOOLS_BODY["shelf"][0],
        "name": "A rather long filament name for a big shelf " * 2,
        "brand": "Some Manufacturer With A Long Name",
        "location": "Dry box on the second shelf, left side",
    }
    body = {
        **SPOOLS_BODY,
        "shelf": [{**row, "id": n} for n in range(SHELF_ATTRIBUTE_LIMIT + 25)],
    }
    view = parse_spools_view(body)
    attrs = shelf_attributes(view)
    assert len(shelf_spools(view)) == SHELF_ATTRIBUTE_LIMIT + 25
    assert len(attrs["spools"]) == SHELF_ATTRIBUTE_LIMIT
    assert attrs["truncated"] is True
    # The total still covers every shelf spool, not just the listed ones.
    assert attrs["total_remaining_weight"] == 800.0 * (SHELF_ATTRIBUTE_LIMIT + 25)
    assert len(json.dumps(attrs).encode()) < 16_384


def test_shelf_of_an_unavailable_view_is_empty():
    """A failed spool read yields no shelf rows (the entity goes unavailable)."""
    view = unavailable_spools("ad5x", "Spoolman down")
    assert shelf_spools(view) == ()
    assert shelf_spools(None) == ()
    assert shelf_attributes(None) == {
        "spools": [],
        "truncated": False,
        "total_remaining_weight": None,
    }


def test_snapshot_parses_bot_platform_and_user():
    """bot.platform and bot.heartbeat.user reach the instance."""
    body = status_body(MOONRAKER_ROW)
    body["bot"]["heartbeat"] = dict(BOT_HEARTBEAT)
    instance = parse_snapshot(body).instance
    assert instance.bot_platform == "discord"
    assert instance.bot_user == "PrintHandy#4242"
    assert instance.bot_connected is True
    assert bot_attributes(instance) == {
        "platform": "discord",
        "user": "PrintHandy#4242",
        "mode": "off",
    }


def test_bot_platform_falls_back_to_the_heartbeat():
    """With no platform in the bot section, the heartbeat's is used."""
    body = status_body(MOONRAKER_ROW)
    body["bot"]["platform"] = ""
    body["bot"]["mode"] = ""
    body["bot"]["heartbeat"] = dict(BOT_HEARTBEAT, platform="telegram")
    instance = parse_snapshot(body).instance
    assert instance.bot_platform == "telegram"
    assert instance.bot_mode == "standalone"


def test_bot_attributes_are_none_without_a_heartbeat():
    """No heartbeat: no user; an unset bot names nothing at all."""
    instance = parse_snapshot(status_body(MOONRAKER_ROW)).instance
    assert instance.bot_user == ""
    assert bot_attributes(instance)["user"] is None
    assert bot_attributes(InstanceInfo()) == {
        "platform": None,
        "user": None,
        "mode": None,
    }


# -- Home Assistant ----------------------------------------------------------

try:
    import pytest_homeassistant_custom_component  # noqa: F401
except ImportError:  # pragma: no cover - plain local run
    _HA = False
else:
    _HA = True

needs_ha = pytest.mark.skipif(not _HA, reason="needs the HA test harness")

if _HA:
    from homeassistant.core import HomeAssistant

    from tests.test_spools import _CLOCK, SNAPSHOT_BODY, FakeApiClient, _setup

    @pytest.fixture(autouse=True)
    def _reset_fake():
        FakeApiClient.instances = []
        FakeApiClient.spools_body = SPOOLS_BODY
        FakeApiClient.spool_error = None
        yield

    @pytest.fixture(autouse=True)
    def auto_enable_custom_integrations(enable_custom_integrations):
        """Enable custom integrations for these tests."""
        yield


def _one(hass, domain, suffix):
    states = [
        state
        for state in hass.states.async_all(domain)
        if state.entity_id.endswith(suffix)
    ]
    assert len(states) == 1, [state.entity_id for state in states]
    return states[0]


@needs_ha
async def test_shelf_sensor_reflects_the_poll(hass: "HomeAssistant"):
    """One shelf sensor per printer: state is the count, rows in attributes."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        shelf = _one(hass, "sensor", "_spool_shelf")
        assert shelf.state == "2"
        assert shelf.attributes["unit_of_measurement"] == "spools"
        assert [row["id"] for row in shelf.attributes["spools"]] == [7, 9]
        assert shelf.attributes["spools"][0]["color_hex"] == "6F4E37"
        assert shelf.attributes["total_remaining_weight"] == 1050.0


@needs_ha
async def test_shelf_sensor_goes_unavailable_when_the_spool_poll_fails(
    hass: "HomeAssistant",
):
    """A Spoolman outage reads as unavailable, never as an empty shelf."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        entry = await _setup(hass)

        async def _down(self, printer_ids):
            return {
                printer_id: unavailable_spools(printer_id, "fake down")
                for printer_id in printer_ids
            }

        with (
            patch.object(FakeApiClient, "async_get_printer_spool_views", _down),
            patch(_CLOCK, return_value=10_000_000.0),
        ):
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
        assert _one(hass, "sensor", "_spool_shelf").state == "unavailable"


@needs_ha
async def test_bot_connected_carries_platform_and_user(hass: "HomeAssistant"):
    """The bot connectivity sensor names the platform and the bot account."""
    body = {
        **SNAPSHOT_BODY,
        "bot": {
            "platform": "discord",
            "mode": "standalone",
            "ok": True,
            "heartbeat": dict(BOT_HEARTBEAT),
        },
    }

    async def _status(self):
        return parse_snapshot(body)

    with (
        patch(
            "custom_components.print_handy.coordinator.PrintHandyApiClient",
            FakeApiClient,
        ),
        patch.object(FakeApiClient, "async_get_status", _status),
    ):
        await _setup(hass)
        bot = _one(hass, "binary_sensor", "_bot_connected")
        assert bot.state == "on"
        assert bot.attributes["platform"] == "discord"
        assert bot.attributes["user"] == "PrintHandy#4242"
        assert bot.attributes["mode"] == "standalone"
