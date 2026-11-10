"""Entity-level tests: identity migration, buttons, availability, services."""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.diagnostics import REDACTED  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.print_handy import (  # noqa: E402
    async_remove_config_entry_device,
)
from custom_components.print_handy.const import DOMAIN  # noqa: E402
from custom_components.print_handy.entity import find_device  # noqa: E402
from custom_components.print_handy.diagnostics import (  # noqa: E402
    async_get_config_entry_diagnostics,
)
from custom_components.print_handy.model import PrinterStatus  # noqa: E402
from custom_components.print_handy.sensor import stable_finish  # noqa: E402
from tests.fake_client import CLOCK_PATH, Clock, World, patched  # noqa: E402
from tests.fixtures import BAMBU_ROW, MOONRAKER_ROW, printer_body  # noqa: E402

URL = "http://ph.local:8088"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


async def _setup(hass, world, *, version=2, data=None, unique_id="abc"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=version,
        data=data or {"url": URL, "token": "secret-token"},
        unique_id=unique_id,
    )
    entry.add_to_hass(hass)
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# -- migration --------------------------------------------------------------


async def test_migration_keeps_entities_and_devices(hass: HomeAssistant):
    """A 0.5.x entry keeps its entity ids and devices across the id change."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=1, data={"url": URL, "token": ""}, unique_id=URL
    )
    entry.add_to_hass(hass)
    ents, devs = er.async_get(hass), dr.async_get(hass)
    old = {
        "sensor": f"{URL}:printer:voron:current_state",
        "number": f"{URL}:printer:voron:number:nozzle_target_temperature",
        "button": f"{URL}:printer:voron:pause",
    }
    for platform, unique_id in old.items():
        ents.async_get_or_create(
            platform,
            DOMAIN,
            unique_id,
            config_entry=entry,
            suggested_object_id=f"voron_{unique_id.rsplit(':', 1)[1]}",
        )
    ents.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{URL}:instance:version",
        config_entry=entry,
        suggested_object_id="print_handy_version",
    )
    hub = devs.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, f"instance:{URL}")}
    )
    printer = devs.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "printer:voron")}
    )
    world = World()
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.version == 2
    assert entry.unique_id == entry.entry_id == entry.data["instance_id"]
    key = entry.entry_id
    assert ents.async_get("sensor.voron_current_state").unique_id == f"{key}:printer:voron:current_state"
    assert ents.async_get("sensor.print_handy_version").unique_id == f"{key}:instance:version"
    assert ents.async_get("number.voron_nozzle_target_temperature").unique_id.startswith(f"{key}:")
    assert ents.async_get("button.voron_pause").unique_id == f"{key}:printer:voron:pause"
    # no duplicate entities were created next to the migrated ones
    assert len(er.async_entries_for_config_entry(ents, entry.entry_id)) == len(
        {e.unique_id for e in er.async_entries_for_config_entry(ents, entry.entry_id)}
    )
    assert devs.async_get(printer.id).identifiers == {(DOMAIN, f"{key}:printer:voron")}
    assert devs.async_get(hub.id).identifiers == {(DOMAIN, f"{key}:instance")}
    assert hass.states.get("sensor.voron_current_state").state == "standby"


async def test_newer_entry_version_is_refused(hass: HomeAssistant):
    """Newer entry version is refused."""
    from custom_components.print_handy import async_migrate_entry

    entry = MockConfigEntry(domain=DOMAIN, version=3, data={"url": URL})
    entry.add_to_hass(hass)
    assert not await async_migrate_entry(hass, entry)


async def test_ids_do_not_depend_on_the_url(hass: HomeAssistant):
    """Ids do not depend on the url."""
    entry = await _setup(hass, World(), data={"url": URL, "token": "", "instance_id": "inst1"})
    ents = er.async_get(hass)
    assert ents.async_get("sensor.voron_current_state").unique_id == "inst1:printer:voron:current_state"
    with patched(World()), patch(CLOCK_PATH, Clock()):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "url": "https://192.0.2.10"}
        )
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert ents.async_get("sensor.voron_current_state").unique_id == "inst1:printer:voron:current_state"


async def test_two_instances_with_the_same_printer_id_get_separate_devices(
    hass: HomeAssistant,
):
    """Two instances with the same printer id get separate devices."""
    first = await _setup(hass, World(), data={"url": URL, "token": "", "instance_id": "one"}, unique_id="one")
    second = await _setup(hass, World(), data={"url": "http://b.local:8088", "token": "", "instance_id": "two"}, unique_id="two")
    devs = dr.async_get(hass)
    one = find_device(devs, (DOMAIN, "one:printer:voron"), first.entry_id)
    two = find_device(devs, (DOMAIN, "two:printer:voron"), second.entry_id)
    assert one is not None and two is not None
    assert one.id != two.id


# -- buttons ----------------------------------------------------------------


def _button_keys(hass, entry, printer_id):
    ents = er.async_get(hass)
    prefix = f"{entry.entry_id}:printer:{printer_id}:"
    return {
        e.unique_id[len(prefix):]: e
        for e in er.async_entries_for_config_entry(ents, entry.entry_id)
        if e.domain == "button" and e.unique_id.startswith(prefix)
    }


async def test_moonraker_only_buttons_follow_the_connection(hass: HomeAssistant):
    """Moonraker only buttons follow the connection."""
    entry = await _setup(hass, World(rows=[MOONRAKER_ROW, BAMBU_ROW]))
    voron = _button_keys(hass, entry, "voron")
    bambu = _button_keys(hass, entry, "x1c")
    assert set(voron) == {
        "pause", "resume", "cancel", "emergency_stop", "firmware_restart", "server_restart"
    }
    assert set(bambu) == {"pause", "resume", "cancel"}


async def test_dangerous_buttons_are_disabled_by_default(hass: HomeAssistant):
    """Dangerous buttons are disabled by default."""
    entry = await _setup(hass, World())
    buttons = _button_keys(hass, entry, "voron")
    for key in ("cancel", "emergency_stop", "firmware_restart", "server_restart"):
        assert buttons[key].disabled_by is not None, key
    for key in ("pause", "resume"):
        assert buttons[key].disabled_by is None, key
    assert buttons["server_restart"].entity_category.value == "config"
    assert "moonraker" in (buttons["server_restart"].original_name or "").lower()


async def test_user_enabled_button_stays_enabled(hass: HomeAssistant):
    """User enabled button stays enabled."""
    entry = await _setup(hass, World())
    ents = er.async_get(hass)
    stop = _button_keys(hass, entry, "voron")["emergency_stop"]
    ents.async_update_entity(stop.entity_id, disabled_by=None)
    with patched(World()), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert ents.async_get(stop.entity_id).disabled_by is None
    assert hass.states.get(stop.entity_id) is not None


async def test_dead_buttons_from_0_5_are_removed_for_other_families(hass: HomeAssistant):
    """Dead buttons from 0 5 are removed for other families."""
    entry = MockConfigEntry(domain=DOMAIN, version=1, data={"url": URL, "token": ""})
    entry.add_to_hass(hass)
    ents = er.async_get(hass)
    ents.async_get_or_create(
        "button", DOMAIN, f"{URL}:printer:x1c:emergency_stop", config_entry=entry
    )
    ents.async_get_or_create(
        "button", DOMAIN, f"{URL}:printer:x1c:pause", config_entry=entry
    )
    with patched(World(rows=[BAMBU_ROW])), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    keys = _button_keys(hass, entry, "x1c")
    assert "emergency_stop" not in keys
    assert "pause" in keys


async def test_control_failure_message_reaches_the_user(hass: HomeAssistant):
    """Control failure message reaches the user."""
    from custom_components.print_handy import api

    world = World()
    entry = await _setup(hass, world)
    ents = er.async_get(hass)
    stop = _button_keys(hass, entry, "voron")["emergency_stop"]
    ents.async_update_entity(stop.entity_id, disabled_by=None)
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        world.control_error = api.PrintHandyApiError(
            "Print Handy Web UI returned HTTP 502 for x: printer unreachable. Nothing sent."
        )
        with pytest.raises(HomeAssistantError, match="Nothing sent"):
            await hass.services.async_call(
                "button", "press", {"entity_id": stop.entity_id}, blocking=True
            )


# -- sensors that used to lie -----------------------------------------------


async def test_spoolman_not_configured_is_unknown_not_disconnected(hass: HomeAssistant):
    """Spoolman not configured is unknown not disconnected."""
    await _setup(hass, World(spoolman_ok=None))
    assert hass.states.get("binary_sensor.print_handy_spoolman_connected").state == "unknown"


async def test_spoolman_down_is_off(hass: HomeAssistant):
    """Spoolman down is off."""
    await _setup(hass, World(spoolman_ok=False))
    assert hass.states.get("binary_sensor.print_handy_spoolman_connected").state == "off"


async def test_unavailable_printer_makes_live_entities_unavailable(hass: HomeAssistant):
    """Unavailable printer makes live entities unavailable."""
    world = World(printers={"voron": printer_body("voron", "unavailable", detail="down")})
    await _setup(hass, world)
    for entity_id in (
        "sensor.voron_current_state",
        "sensor.voron_nozzle_temperature",
        "binary_sensor.voron_printing",
        "binary_sensor.voron_printing_error",
    ):
        assert hass.states.get(entity_id).state == "unavailable", entity_id
    # not live-status entities: the roster-level ones stay
    assert hass.states.get("sensor.voron_connection").state == "moonraker"


async def test_state_sensor_passes_known_and_falls_back_for_unlisted(hass: HomeAssistant):
    """State sensor passes known and falls back for unlisted."""
    world = World(printers={"voron": printer_body("voron", "printing")})
    await _setup(hass, world)
    assert hass.states.get("sensor.voron_current_state").state == "printing"
    world2 = World(printers={"voron": printer_body("voron", "heating_bed")})
    await _setup(hass, world2, data={"url": "http://b.local:8088", "token": "", "instance_id": "z"}, unique_id="z")
    state = [
        s for s in hass.states.async_all("sensor")
        if s.attributes.get("raw_state") == "heating_bed"
    ]
    assert state and state[0].state == "unknown"


async def test_targets_appear_when_printer_comes_online_later(hass: HomeAssistant):
    """Targets appear when printer comes online later."""
    world = World(printers={"voron": printer_body("voron", "unavailable")})
    entry = await _setup(hass, world)
    assert hass.states.get("number.voron_nozzle_target_temperature") is None
    world.printers = {"voron": printer_body("voron", "standby", hotend_target_c=0.0)}
    with patched(world), patch(CLOCK_PATH, Clock()):
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get("number.voron_nozzle_target_temperature") is not None


def test_estimated_finish_holds_steady_within_tolerance():
    """Estimated finish holds steady within tolerance."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    status = PrinterStatus(printer_id="p", state="printing", remaining_min=60.0)
    first = stable_finish(None, status, now=now)
    # 30 s later the printer reports 59.5 min left: same projection
    later = stable_finish(first, PrinterStatus("p", "printing", remaining_min=59.5), now=now + timedelta(seconds=30))
    assert later == first
    # a real slip of ten minutes moves it
    slipped = stable_finish(first, PrinterStatus("p", "printing", remaining_min=69.5), now=now + timedelta(seconds=30))
    assert slipped != first
    assert stable_finish(first, PrinterStatus("p", "printing"), now=now) is None


# -- services, diagnostics, device removal ------------------------------------


async def test_service_accepts_a_device_target(hass: HomeAssistant):
    """Service accepts a device target."""
    world = World()
    entry = await _setup(hass, world)
    device = find_device(
        dr.async_get(hass), (DOMAIN, f"{entry.entry_id}:printer:voron"), entry.entry_id
    )
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.services.async_call(
            DOMAIN, "send_gcode", {"device_id": device.id, "script": "G28"}, blocking=True
        )
    assert ("voron", "gcode", "G28") in world.controls


async def test_service_still_accepts_the_free_text_printer_id(hass: HomeAssistant):
    """Service still accepts the free text printer id."""
    world = World()
    await _setup(hass, world)
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.services.async_call(
            DOMAIN, "send_gcode", {"printer_id": "voron", "script": "G28"}, blocking=True
        )
    assert ("voron", "gcode", "G28") in world.controls


async def test_service_needs_a_target(hass: HomeAssistant):
    """Service needs a target."""
    await _setup(hass, World())
    with pytest.raises(Exception, match="printer_id|device_id"):
        await hass.services.async_call(DOMAIN, "send_gcode", {"script": "G28"}, blocking=True)


async def test_services_are_registered_without_any_entry(hass: HomeAssistant):
    """Services are registered without any entry."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, "send_gcode")


async def test_diagnostics_redact_addresses_and_token(hass: HomeAssistant):
    """Diagnostics redact addresses and token."""
    world = World()
    entry = await _setup(hass, world)
    world.hub_error = None
    data = await async_get_config_entry_diagnostics(hass, entry)
    text = str(data)
    assert "secret-token" not in text
    assert "ph.local" not in text
    assert "print-handy-box" not in text  # the server host name
    assert data["entry"]["data"]["url"] == REDACTED
    assert data["snapshot"]["printers"]


async def test_device_removal_only_once_the_printer_left_the_roster(hass: HomeAssistant):
    """Device removal only once the printer left the roster."""
    entry = await _setup(hass, World(rows=[MOONRAKER_ROW, BAMBU_ROW]))
    devs = dr.async_get(hass)
    hub = find_device(devs, (DOMAIN, f"{entry.entry_id}:instance"), entry.entry_id)
    voron = find_device(devs, (DOMAIN, f"{entry.entry_id}:printer:voron"), entry.entry_id)
    assert not await async_remove_config_entry_device(hass, entry, hub)
    assert not await async_remove_config_entry_device(hass, entry, voron)
    ghost = devs.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}:printer:gone")},
    )
    assert await async_remove_config_entry_device(hass, entry, ghost)
