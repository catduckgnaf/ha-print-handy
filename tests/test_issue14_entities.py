"""Issue #14 remainder, HA-level part: the five features worth adding.

Camera entity (snapshot proxy), readiness and doctor binary sensors,
Repairs issues for failing doctor checks, spool weight sensors with a
low-filament alert, Bambu fault sensors and the print-finished event.
"""

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.camera import async_get_image  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_capture_events,
)

from custom_components.print_handy.const import DOMAIN, EVENT_PRINT_FINISHED  # noqa: E402
from tests.fake_client import CLOCK_PATH, Clock, World, patched  # noqa: E402
from tests.fixtures import BAMBU_ROW, MOONRAKER_ROW, printer_body, status_body  # noqa: E402
from tests.test_issue14_model_api import CAMERA_ROW, DOCTOR  # noqa: E402
from tests.test_spool_model import SPOOLS_BODY  # noqa: E402

URL = "http://ph.local:8088"
JPEG = b"\xff\xd8\xff\xe0jpeg-bytes"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


async def _setup(hass, world, *, data=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data=data or {"url": URL, "token": "secret-token"},
        unique_id="abc",
    )
    entry.add_to_hass(hass)
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _entity_id(hass, entry, platform, unique_suffix):
    """Return the entity id registered for one unique-id suffix, or None."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.domain == platform and entity.unique_id.endswith(unique_suffix):
            return entity.entity_id
    return None


async def test_camera_entity_only_for_camera_printers(hass: HomeAssistant):
    """A camera entity exists only where the roster carries a snapshot URL."""
    world = World(rows=[CAMERA_ROW, MOONRAKER_ROW])
    world.camera_images = {"cam-printer": JPEG}
    entry = await _setup(hass, world)

    cam = _entity_id(hass, entry, "camera", ":printer:cam-printer:camera")
    assert cam is not None
    assert _entity_id(hass, entry, "camera", ":printer:voron:camera") is None

    image = await async_get_image(hass, cam)
    assert image.content_type == "image/jpeg"
    assert image.content == JPEG


async def test_ready_and_doctor_binary_sensors(hass: HomeAssistant):
    """Readiness mirrors the snapshot ok flag; one sensor per doctor row."""
    world = World()
    world.doctor = DOCTOR
    entry = await _setup(hass, world)

    ready = _entity_id(hass, entry, "binary_sensor", ":instance:ready")
    assert ready is not None
    assert hass.states.get(ready).state == "on"

    failing = _entity_id(hass, entry, "binary_sensor", ":instance:doctor:orca_cli")
    assert failing is not None
    state = hass.states.get(failing)
    assert state.state == "on"
    assert state.attributes["status"] == "fail"
    assert state.attributes["detail"] == "orca not found"

    passing = _entity_id(hass, entry, "binary_sensor", ":instance:doctor:settings")
    assert hass.states.get(passing).state == "off"


async def test_repairs_issue_follows_doctor_failures(hass: HomeAssistant):
    """A failing doctor check raises a Repairs issue; recovery clears it."""
    world = World()
    world.doctor = DOCTOR
    entry = await _setup(hass, world)
    coordinator = entry.runtime_data

    issues = ir.async_get(hass).issues
    doctor_issues = [
        issue
        for (domain, _), issue in issues.items()
        if domain == DOMAIN and "orca_cli" in issue.issue_id
    ]
    assert len(doctor_issues) == 1
    assert doctor_issues[0].severity == ir.IssueSeverity.ERROR
    assert not any("settings" in issue.issue_id for _, issue in issues.items())

    world.doctor = [{"label": "orca cli", "status": "ok", "detail": "found"}]
    clock = Clock()
    clock.advance(301)  # past the hub interval, so the roster is re-read
    with patched(world), patch(CLOCK_PATH, clock):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    issues = ir.async_get(hass).issues
    assert not any("orca_cli" in issue_id for _, issue_id in issues)


async def test_spool_weight_and_low_filament(hass: HomeAssistant):
    """Slot 2 (500 g of 1000 g) gets a weight sensor and a quiet alert."""
    world = World()
    entry = await _setup(hass, world)

    weight = _entity_id(hass, entry, "sensor", ":printer:voron:slot_2_spool_weight")
    assert weight is not None
    state = hass.states.get(weight)
    assert state.state == "500.0"
    assert state.attributes["unit_of_measurement"] == "g"

    low = _entity_id(hass, entry, "binary_sensor", ":printer:voron:slot_2_low_filament")
    assert low is not None
    assert hass.states.get(low).state == "off"


async def test_low_filament_turns_on_when_spool_runs_low(hass: HomeAssistant):
    """A spool at 5 percent remaining raises the low-filament alert."""
    body = {
        **SPOOLS_BODY,
        "slots": [
            {
                "slot": 1,
                "spool": {
                    **SPOOLS_BODY["slots"][1]["spool"],
                    "remaining_weight": 50.0,
                    "remaining_percent": 5.0,
                },
            }
        ],
    }
    world = World(spools_body=body)
    entry = await _setup(hass, world)

    low = _entity_id(hass, entry, "binary_sensor", ":printer:voron:slot_1_low_filament")
    assert low is not None
    assert hass.states.get(low).state == "on"


async def test_bambu_fault_sensor(hass: HomeAssistant):
    """HMS faults become a count sensor with the codes as attributes."""
    world = World(
        rows=[BAMBU_ROW],
        printers={
            "x1c": printer_body(
                "x1c",
                state="error",
                hms_errors=[
                    {
                        "code": "0300_8000",
                        "description": "Filament runout",
                        "listed": True,
                    }
                ],
                print_error_text="Filament runout",
            )
        },
    )
    entry = await _setup(hass, world)

    faults = _entity_id(hass, entry, "sensor", ":printer:x1c:faults")
    assert faults is not None
    state = hass.states.get(faults)
    assert state.state == "1"
    assert state.attributes["codes"] == ["0300_8000"]
    assert state.attributes["descriptions"] == ["Filament runout"]
    assert state.attributes["print_error"] == "Filament runout"


async def test_no_fault_sensor_without_fault_data(hass: HomeAssistant):
    """A printer whose family sends no fault data gains no fault sensor."""
    world = World()
    entry = await _setup(hass, world)
    assert _entity_id(hass, entry, "sensor", ":printer:voron:faults") is None


async def test_print_finished_event_fires_once_on_completion(hass: HomeAssistant):
    """Printing -> complete fires print_handy_print_finished exactly once."""
    world = World(
        printers={"voron": printer_body("voron", state="printing", filename="a.gcode")}
    )
    entry = await _setup(hass, world)
    coordinator = entry.runtime_data
    events = async_capture_events(hass, EVENT_PRINT_FINISHED)

    world.printers["voron"] = printer_body(
        "voron", state="complete", filename="a.gcode"
    )
    with patched(world), patch(CLOCK_PATH, Clock()):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert len(events) == 1
    assert events[0].data["printer_id"] == "voron"
    assert events[0].data["filename"] == "a.gcode"

    with patched(world), patch(CLOCK_PATH, Clock()):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert len(events) == 1  # staying complete does not fire again


async def test_status_body_doctor_fixture_shape():
    """The shared fixture accepts doctor rows (guard against drift)."""
    body = status_body(MOONRAKER_ROW, doctor=DOCTOR)
    assert body["doctor"] == DOCTOR
