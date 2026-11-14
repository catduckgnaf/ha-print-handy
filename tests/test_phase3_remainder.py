"""HA-level tests for the issue-#1 phase-3 remainder (0.5.0).

Emergency stop / firmware restart / server restart buttons, the
send_gcode and exclude_object services, and the current_object
sensor. These run under the real pytest-homeassistant-custom-component
harness (CI installs it from requirements-test.txt); without that
package installed — a plain local run — the whole module is skipped
and the HA-free coverage in test_api.py / test_model.py is what runs.
The fake API client below returns recorded responses only: no live
Print Handy is contacted.
"""

from unittest.mock import patch

import pytest
import voluptuous as vol

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

from custom_components.print_handy import (  # noqa: E402
    SERVICE_EXCLUDE_OBJECT_SCHEMA,
    SERVICE_SEND_GCODE_SCHEMA,
    api,
)
from custom_components.print_handy.const import DOMAIN  # noqa: E402
from custom_components.print_handy.model import (  # noqa: E402
    parse_printer_status,
    parse_snapshot,
    unavailable_spools,
)

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

SNAPSHOT_BODY = {
    "version": "2.2.5",
    "host": "print-handy-box",
    "ok": True,
    "printers": [
        {
            "id": "flashforge-ad5x",
            "name": "AD5X",
            "model": "flashforge_ad5x",
            "vendor": "flashforge",
            "connection": "moonraker",
            "default": True,
        }
    ],
    "spoolman": {"ok": True, "url": "http://127.0.0.1:8000", "bundled": True},
    "doctor": [],
}

STATUS_WITH_OBJECTS = {
    "ok": True,
    "id": "flashforge-ad5x",
    "status": {
        "state": "printing",
        "filename": "cubes.gcode",
        "progress": 0.4,
        "hotend_c": 210,
        "bed_c": 60,
        "current_object": "cube_1",
        "objects": ["cube_1", "cube_2"],
        "excluded_objects": ["cube_2"],
    },
}

STATUS_WITHOUT_OBJECTS = {
    "ok": True,
    "id": "flashforge-ad5x",
    "status": {
        "state": "standby",
        "filename": "",
        "progress": None,
        "hotend_c": 25,
        "bed_c": 24,
    },
}

LAST_OBJECT_REASON = "this is the last remaining object; cancel the print instead"


class FakeApiClient:
    """A PrintHandyApiClient stand-in returning recorded responses."""

    instances: list["FakeApiClient"] = []
    status_body = STATUS_WITH_OBJECTS
    control_error: Exception | None = None

    def __init__(self, session=None, base_url="", token=""):
        """Initialize the fake client."""
        self.base_url = "http://ph.local:8080"
        self.calls: list[tuple] = []
        FakeApiClient.instances.append(self)

    async def async_get_status(self):
        """Return the recorded snapshot."""
        return parse_snapshot(SNAPSHOT_BODY)

    async def async_get_roster(self):
        """Return no roster, like a server before Print Handy #1400."""
        return None

    async def async_get_printer_statuses(self, printer_ids):
        """Return the recorded per-printer status."""
        return {
            printer_id: parse_printer_status(FakeApiClient.status_body, printer_id)
            for printer_id in printer_ids
        }

    async def async_get_printer_spool_views(self, printer_ids):
        """No spool view in these tests."""
        return {
            printer_id: unavailable_spools(printer_id, "not in these tests")
            for printer_id in printer_ids
        }

    async def _record(self, action, *args):
        self.calls.append((action, args))
        if FakeApiClient.control_error is not None:
            raise FakeApiClient.control_error

    async def async_printer_control(self, printer_id, action):
        """Record a button control call."""
        await self._record("control", printer_id, action)

    async def async_set_printer_target(self, printer_id, **kwargs):
        """Record a target set."""
        await self._record("target", printer_id)

    async def async_send_gcode(self, printer_id, script):
        """Record a send-G-code call."""
        await self._record("gcode", printer_id, script)

    async def async_exclude_object(self, printer_id, object_name):
        """Record an exclude-object call."""
        await self._record("exclude", printer_id, object_name)


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeApiClient.instances = []
    FakeApiClient.status_body = STATUS_WITH_OBJECTS
    FakeApiClient.control_error = None
    yield


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={"url": "http://ph.local:8080", "token": "sekrit"},
        unique_id="http://ph.local:8080",
    )


async def _setup(hass: HomeAssistant):
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def _button_ids(hass: HomeAssistant) -> list[str]:
    return sorted(state.entity_id for state in hass.states.async_all("button"))


async def _enable_all_buttons(hass: HomeAssistant, entry) -> None:
    """Turn on the buttons that are disabled by default, then reload."""
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if reg_entry.domain == "button" and reg_entry.disabled_by is not None:
            registry.async_update_entity(reg_entry.entity_id, disabled_by=None)
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()


async def test_all_six_buttons_are_created_per_printer(hass: HomeAssistant):
    """Pause/resume/cancel plus the three new buttons, with fixed suffixes."""
    entry = await _setup(hass)
    await _enable_all_buttons(hass, entry)
    ids = _button_ids(hass)
    assert len(ids) == 6
    for suffix in (
        "pause",
        "resume",
        "cancel",
        "emergency_stop",
        "firmware_restart",
        "moonraker_restart",  # key server_restart; named for what it restarts
    ):
        assert any(entity_id.endswith(f"_{suffix}") for entity_id in ids), suffix


async def test_emergency_stop_press_posts_only_to_that_printer(
    hass: HomeAssistant,
):
    """Pressing Emergency stop records the emergency_stop control action."""
    entry = await _setup(hass)
    await _enable_all_buttons(hass, entry)
    entity_id = next(
        entity_id
        for entity_id in _button_ids(hass)
        if entity_id.endswith("_emergency_stop")
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": entity_id}, blocking=True
    )
    client = FakeApiClient.instances[-1]
    assert ("control", ("flashforge-ad5x", "emergency_stop")) in client.calls


async def test_restart_button_press_records_its_action(hass: HomeAssistant):
    """Firmware and server restart presses record their own actions."""
    entry = await _setup(hass)
    await _enable_all_buttons(hass, entry)
    for suffix in ("firmware_restart", "moonraker_restart"):
        entity_id = next(
            entity_id for entity_id in _button_ids(hass) if entity_id.endswith(f"_{suffix}")
        )
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )
    calls = FakeApiClient.instances[-1].calls
    assert ("control", ("flashforge-ad5x", "firmware_restart")) in calls
    assert ("control", ("flashforge-ad5x", "server_restart")) in calls


async def test_button_press_refusal_fails_visibly(hass: HomeAssistant):
    """A refused press raises HomeAssistantError with Print Handy's reason."""
    FakeApiClient.control_error = api.PrintHandyControlRefusedError(
        "Print Handy refused the emergency_stop action for printer "
        "'flashforge-ad5x' (HTTP 403): controls are disabled"
    )
    entry = await _setup(hass)
    await _enable_all_buttons(hass, entry)
    entity_id = next(
        entity_id
        for entity_id in _button_ids(hass)
        if entity_id.endswith("_emergency_stop")
    )
    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )
    assert "controls are disabled" in str(excinfo.value)


async def test_current_object_sensor_created_with_object_data(
    hass: HomeAssistant,
):
    """With objects reported: state is the current object, lists are attributes."""
    await _setup(hass)
    states = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_current_object")
    ]
    assert len(states) == 1
    assert states[0].state == "cube_1"
    assert states[0].attributes["objects"] == ["cube_1", "cube_2"]
    assert states[0].attributes["excluded_objects"] == ["cube_2"]


async def test_current_object_sensor_absent_without_object_data(
    hass: HomeAssistant,
):
    """A printer reporting no objects gains no current-object sensor."""
    FakeApiClient.status_body = STATUS_WITHOUT_OBJECTS
    await _setup(hass)
    states = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_current_object")
    ]
    assert states == []


async def test_current_object_sensor_appears_when_a_print_starts(
    hass: HomeAssistant,
):
    """Object data arriving on a later poll creates the sensor then.

    Object data only exists while a print runs, so setup-time gating
    alone would miss every print started after Home Assistant boots:
    starting idle must gain the sensor when the print's first poll
    reports objects, without an integration reload.
    """
    FakeApiClient.status_body = STATUS_WITHOUT_OBJECTS
    entry = await _setup(hass)
    assert [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_current_object")
    ] == []
    FakeApiClient.status_body = STATUS_WITH_OBJECTS
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    states = [
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_current_object")
    ]
    assert len(states) == 1
    assert states[0].state == "cube_1"
    # A further refresh must not duplicate the sensor.
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len([
        state
        for state in hass.states.async_all("sensor")
        if state.entity_id.endswith("_current_object")
    ]) == 1


async def test_send_gcode_service_reaches_the_client(hass: HomeAssistant):
    """print_handy.send_gcode posts the script for the named printer only."""
    await _setup(hass)
    await hass.services.async_call(
        DOMAIN,
        "send_gcode",
        {"printer_id": "flashforge-ad5x", "script": "M104 S200"},
        blocking=True,
    )
    client = FakeApiClient.instances[-1]
    assert ("gcode", ("flashforge-ad5x", "M104 S200")) in client.calls


async def test_exclude_object_service_reaches_the_client(hass: HomeAssistant):
    """print_handy.exclude_object posts the object for the named printer."""
    await _setup(hass)
    await hass.services.async_call(
        DOMAIN,
        "exclude_object",
        {"printer_id": "flashforge-ad5x", "object": "cube_2"},
        blocking=True,
    )
    client = FakeApiClient.instances[-1]
    assert ("exclude", ("flashforge-ad5x", "cube_2")) in client.calls


async def test_exclude_last_object_reason_reaches_the_user_verbatim(
    hass: HomeAssistant,
):
    """The server's last-object 409 reason surfaces word for word."""
    FakeApiClient.control_error = api.PrintHandyControlRefusedError(
        "Print Handy refused excluding object 'cube_1' on printer "
        f"'flashforge-ad5x' (HTTP 409): {LAST_OBJECT_REASON}"
    )
    await _setup(hass)
    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            DOMAIN,
            "exclude_object",
            {"printer_id": "flashforge-ad5x", "object": "cube_1"},
            blocking=True,
        )
    assert LAST_OBJECT_REASON in str(excinfo.value)


async def test_send_gcode_refusal_raises_with_reason(hass: HomeAssistant):
    """A refused G-code send fails the service call with its reason."""
    FakeApiClient.control_error = api.PrintHandyControlRefusedError(
        "Print Handy refused sending G-code to printer 'flashforge-ad5x' "
        "(HTTP 403): controls are disabled"
    )
    await _setup(hass)
    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            DOMAIN,
            "send_gcode",
            {"printer_id": "flashforge-ad5x", "script": "G28"},
            blocking=True,
        )
    assert "controls are disabled" in str(excinfo.value)


async def test_services_for_an_unknown_printer_raise(hass: HomeAssistant):
    """No default-printer fallback for the new services either."""
    await _setup(hass)
    for service, data in (
        ("send_gcode", {"printer_id": "ghost", "script": "G28"}),
        ("exclude_object", {"printer_id": "ghost", "object": "cube_1"}),
    ):
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(DOMAIN, service, data, blocking=True)
        assert "ghost" in str(excinfo.value)
    assert FakeApiClient.instances[-1].calls == []


def test_service_schemas_require_their_fields():
    """Missing or empty fields fail schema validation before any call."""
    with pytest.raises(vol.Invalid):
        SERVICE_SEND_GCODE_SCHEMA({"printer_id": "flashforge-ad5x"})
    with pytest.raises(vol.Invalid):
        SERVICE_SEND_GCODE_SCHEMA({"printer_id": "flashforge-ad5x", "script": ""})
    with pytest.raises(vol.Invalid):
        SERVICE_EXCLUDE_OBJECT_SCHEMA({"printer_id": "flashforge-ad5x"})
    with pytest.raises(vol.Invalid):
        SERVICE_EXCLUDE_OBJECT_SCHEMA({"printer_id": "flashforge-ad5x", "object": ""})
    ok = SERVICE_SEND_GCODE_SCHEMA(
        {"printer_id": "flashforge-ad5x", "script": "G28"}
    )
    assert ok["script"] == "G28"
