"""Issue #14 server-side items, now that the server sends them (Print Handy #1400).

The cheap roster (GET /api/printers), the server's own instance id, the
controls-enabled flag and the per-heater temperature ceilings. Every
path also has to keep working against a server that predates them.
"""

import asyncio
import copy
from unittest.mock import patch

import pytest

from custom_components.print_handy import api
from custom_components.print_handy.model import (
    merge_roster,
    parse_roster,
    parse_snapshot,
)
from tests.fixtures import BAMBU_ROW, MOONRAKER_ROW, status_body
from tests.test_api_errors import BASE, _client, _Response

SERVER_ID = "0123456789abcdef0123456789abcdef"
LIMITS = {"nozzle": 300.0, "bed": 110.0, "chamber": 60.0}


def _row(row, controls=True, limits=None):
    return {**copy.deepcopy(row), "controls_enabled": controls,
            "temperature_limits_c": limits if limits is not None else dict(LIMITS)}


def _roster_body(*rows, controls=True):
    return {"ok": True, "version": "2.4.0", "instance_id": SERVER_ID,
            "controls_enabled": controls, "printers": list(rows)}


# --- model -----------------------------------------------------------------


def test_roster_parses_ids_controls_and_limits():
    """Roster parses ids controls and limits."""
    bad_limits = {"nozzle": 280, "bed": "hot", "chamber": None, "x": -5}
    roster = parse_roster(
        _roster_body(_row(MOONRAKER_ROW), _row(BAMBU_ROW, controls=False, limits=bad_limits),
                     {"name": "no id"}, "junk")
    )
    assert roster.instance_id == SERVER_ID
    assert roster.controls_enabled is True
    assert [p.printer_id for p in roster.printers] == ["voron", "x1c"]
    voron, x1c = roster.printers
    assert voron.temperature_limits == LIMITS
    assert voron.controls_enabled is True
    assert x1c.controls_enabled is False
    assert x1c.temperature_limits == {"nozzle": 280.0}


def test_roster_rejects_a_body_that_is_not_one():
    """Roster rejects a body that is not one."""
    with pytest.raises(TypeError):
        parse_roster({"ok": True})
    with pytest.raises(TypeError):
        parse_roster(["printers"])


def test_snapshot_without_the_new_fields_reads_as_unknown():
    """An older server's snapshot leaves the new fields unknown, never False."""
    snap = parse_snapshot(status_body(MOONRAKER_ROW))
    assert snap.instance.instance_id == ""
    assert snap.instance.controls_enabled is None
    assert snap.printers[0].controls_enabled is None
    assert snap.printers[0].temperature_limits == {}


def test_snapshot_carries_the_new_fields():
    """Snapshot carries the new fields."""
    body = status_body(_row(MOONRAKER_ROW, controls=False))
    body["instance_id"] = SERVER_ID
    body["controls_enabled"] = False
    snap = parse_snapshot(body)
    assert snap.instance.instance_id == SERVER_ID
    assert snap.instance.controls_enabled is False
    assert snap.printers[0].temperature_limits == LIMITS


def test_merge_keeps_listener_health_and_follows_the_roster():
    """The roster decides the printers; listener health stays from the full read."""
    body = status_body(MOONRAKER_ROW)
    body["listener"] = {"required": True, "ok": False, "reason": "heartbeat stale"}
    snap = parse_snapshot(body)
    merged = merge_roster(
        snap, parse_roster(_roster_body(_row(MOONRAKER_ROW, controls=False), _row(BAMBU_ROW)))
    )
    voron = merged.printer("voron")
    assert voron.listener_required is True
    assert voron.listener_ok is False
    assert voron.listener_reason == "heartbeat stale"
    assert voron.controls_enabled is False
    assert merged.printer("x1c") is not None
    assert merged.instance.instance_id == SERVER_ID
    assert merged.instance.doctor_checks == snap.instance.doctor_checks
    gone = merge_roster(merged, parse_roster(_roster_body(_row(BAMBU_ROW))))
    assert gone.printer("voron") is None


# --- API client ------------------------------------------------------------


def test_client_reads_the_roster():
    """Client reads the roster."""
    routes = {("get", f"{BASE}/api/printers"): _Response(200, _roster_body(_row(MOONRAKER_ROW)))}
    roster = asyncio.run(_client(routes).async_get_roster())
    assert roster.instance_id == SERVER_ID
    assert roster.printers[0].printer_id == "voron"


@pytest.mark.parametrize(
    "response",
    [_Response(404, {"error": "not found"}), _Response(200, {"unexpected": True})],
)
def test_client_reads_an_old_server_as_no_roster(response):
    """A server without the endpoint is None, not an error."""
    routes = {("get", f"{BASE}/api/printers"): response}
    assert asyncio.run(_client(routes).async_get_roster()) is None


def test_client_roster_server_error_raises():
    """Client roster server error raises."""
    routes = {("get", f"{BASE}/api/printers"): _Response(500, {"error": "boom"})}
    with pytest.raises(api.PrintHandyApiError):
        asyncio.run(_client(routes).async_get_roster())


# --- Home Assistant --------------------------------------------------------

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.const import STATE_UNAVAILABLE  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.print_handy.const import DOMAIN  # noqa: E402
from tests.fake_client import CLOCK_PATH, Clock, World, patched  # noqa: E402
from tests.fixtures import printer_body  # noqa: E402

GET_STATUS = "custom_components.print_handy.config_flow.PrintHandyApiClient.async_get_status"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


def _new_world(**kwargs) -> World:
    world = World(
        rows=[_row(MOONRAKER_ROW)],
        printers={"voron": printer_body("voron", hotend_target_c=0.0, bed_target_c=0.0)},
        roster_supported=True,
        instance_id=SERVER_ID,
    )
    for key, value in kwargs.items():
        setattr(world, key, value)
    return world


async def _setup(hass, world, clock, unique_id="abc", data=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=data or {"url": "http://ph.local:8088", "token": "t", "instance_id": "key1"},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    with patched(world), patch(CLOCK_PATH, clock):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def _tick(hass, entry, world, clock, seconds=30):
    clock.advance(seconds)
    with patched(world), patch(CLOCK_PATH, clock):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()


async def test_roster_runs_between_full_reads_only(hass: HomeAssistant):
    """One roster-or-status request per tick, plus the per-printer reads."""
    world, clock = _new_world(), Clock()
    entry = await _setup(hass, world, clock)
    assert (world.hub_calls, world.roster_calls) == (1, 0)
    for _ in range(5):
        await _tick(hass, entry, world, clock, 30)
    assert (world.hub_calls, world.roster_calls) == (1, 5)
    await _tick(hass, entry, world, clock, 200)  # the full read comes due
    assert (world.hub_calls, world.roster_calls) == (2, 5)
    assert world.status_calls == 7


async def test_old_server_is_asked_for_the_roster_once(hass: HomeAssistant):
    """Old server is asked for the roster once."""
    world, clock = _new_world(roster_supported=False), Clock()
    entry = await _setup(hass, world, clock)
    for _ in range(4):
        await _tick(hass, entry, world, clock, 30)
    assert world.roster_calls == 1
    assert entry.runtime_data.roster_supported is False


async def test_roster_brings_new_printers_before_the_full_read(hass: HomeAssistant):
    """Roster brings new printers before the full read."""
    world, clock = _new_world(), Clock()
    entry = await _setup(hass, world, clock)
    assert hass.states.get("sensor.x1c_current_state") is None
    world.rows = [_row(MOONRAKER_ROW), _row(BAMBU_ROW)]
    await _tick(hass, entry, world, clock, 30)
    assert world.hub_calls == 1
    assert hass.states.get("sensor.x1c_current_state") is not None


async def test_controls_off_makes_control_entities_unavailable(hass: HomeAssistant):
    """Controls off makes control entities unavailable, and back."""
    world, clock = _new_world(rows=[_row(MOONRAKER_ROW, controls=False)]), Clock()
    entry = await _setup(hass, world, clock)
    assert hass.states.get("button.voron_pause").state == STATE_UNAVAILABLE
    assert hass.states.get("number.voron_nozzle_target_temperature").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.voron_current_state").state != STATE_UNAVAILABLE
    world.rows = [_row(MOONRAKER_ROW, controls=True)]
    await _tick(hass, entry, world, clock, 30)
    assert hass.states.get("button.voron_pause").state != STATE_UNAVAILABLE
    assert hass.states.get("number.voron_nozzle_target_temperature").state != STATE_UNAVAILABLE


async def test_old_server_keeps_controls_available(hass: HomeAssistant):
    """A server that does not say keeps its controls available."""
    world = _new_world(rows=[MOONRAKER_ROW], roster_supported=False, instance_id="")
    await _setup(hass, world, Clock())
    assert hass.states.get("button.voron_pause").state != STATE_UNAVAILABLE


async def test_number_bounds_follow_the_server_ceilings(hass: HomeAssistant):
    """Number bounds follow the server ceilings, with the old fallback."""
    world, clock = _new_world(), Clock()
    entry = await _setup(hass, world, clock)
    nozzle = hass.states.get("number.voron_nozzle_target_temperature")
    bed = hass.states.get("number.voron_bed_target_temperature")
    assert (nozzle.attributes["min"], nozzle.attributes["max"]) == (0.0, 300.0)
    assert bed.attributes["max"] == 110.0
    world.rows = [_row(MOONRAKER_ROW, limits={})]
    await _tick(hass, entry, world, clock, 30)
    nozzle = hass.states.get("number.voron_nozzle_target_temperature")
    bed = hass.states.get("number.voron_bed_target_temperature")
    assert (nozzle.attributes["max"], bed.attributes["max"]) == (350.0, 150.0)


async def test_setup_adopts_the_server_id(hass: HomeAssistant):
    """An existing entry takes the server's id as its unique id; entity keys stay."""
    world = _new_world()
    entry = await _setup(hass, world, Clock())
    assert entry.unique_id == SERVER_ID
    assert entry.data["server_id"] == SERVER_ID
    assert entry.data["instance_id"] == "key1"
    assert entry.runtime_data.instance_key == "key1"


async def test_setup_leaves_a_duplicate_entry_alone(hass: HomeAssistant):
    """The same server added twice: the second entry is not renamed onto the first."""
    MockConfigEntry(domain=DOMAIN, version=2, unique_id=SERVER_ID,
                    data={"url": "http://other:8088", "instance_id": "k0"}).add_to_hass(hass)
    entry = await _setup(hass, _new_world(), Clock())
    assert entry.unique_id == "abc"
    assert "server_id" not in entry.data


async def test_adding_the_same_server_at_a_new_address_moves_the_entry(hass: HomeAssistant):
    """Adding the same server at a new address moves the entry, no duplicate."""
    existing = MockConfigEntry(
        domain=DOMAIN, version=2, unique_id=SERVER_ID,
        data={"url": "http://print-handy.local:8088", "token": "", "instance_id": "k1",
              "server_id": SERVER_ID},
    )
    existing.add_to_hass(hass)
    body = status_body(MOONRAKER_ROW)
    body["instance_id"] = SERVER_ID
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(GET_STATUS, return_value=parse_snapshot(body)), patch(
        "custom_components.print_handy.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "192.0.2.10:8088", "token": "pw"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert existing.data["url"] == "http://192.0.2.10:8088"
    assert existing.data["token"] == "pw"
    assert existing.data["instance_id"] == "k1"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_new_entry_records_the_server_id(hass: HomeAssistant):
    """New entry records the server id."""
    body = status_body(MOONRAKER_ROW)
    body["instance_id"] = SERVER_ID
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(GET_STATUS, return_value=parse_snapshot(body)), patch(
        "custom_components.print_handy.async_setup_entry", return_value=True
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "192.0.2.10:8088", "token": ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = result["result"]
    assert entry.unique_id == SERVER_ID
    assert entry.data["server_id"] == SERVER_ID
    assert entry.data["instance_id"] and entry.data["instance_id"] != SERVER_ID


async def test_reconfigure_refuses_a_different_server(hass: HomeAssistant):
    """Reconfigure refuses a different server and keeps the entry as it was."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, unique_id=SERVER_ID,
        data={"url": "http://print-handy.local:8088", "token": "", "instance_id": "k1",
              "server_id": SERVER_ID},
    )
    entry.add_to_hass(hass)
    body = status_body(MOONRAKER_ROW)
    body["instance_id"] = "f" * 32
    result = await entry.start_reconfigure_flow(hass)
    with patch(GET_STATUS, return_value=parse_snapshot(body)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "http://192.0.2.99:8088", "token": ""}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_server"}
    assert entry.data["url"] == "http://print-handy.local:8088"
