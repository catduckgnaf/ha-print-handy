"""HA-level tests for the #789 spool entities and services.

These run under the real pytest-homeassistant-custom-component
harness (CI installs it from requirements-test.txt); without that
package installed — a plain local run — the whole module is skipped
and the HA-free coverage in test_spool_api.py / test_spool_model.py
is what runs. The fake API client below returns recorded responses
only: no live Print Handy is contacted.
"""

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402

from custom_components.print_handy import api  # noqa: E402
from custom_components.print_handy.const import DOMAIN  # noqa: E402
from custom_components.print_handy.model import (  # noqa: E402
    parse_snapshot,
    parse_spools_view,
    unavailable_spools,
    unavailable_status,
)
from tests.test_spool_model import SPOOLS_BODY  # noqa: E402

from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

_CLOCK = "custom_components.print_handy.coordinator._monotonic"

SNAPSHOT_BODY = {
    "version": "2.2.2",
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


class FakeApiClient:
    """A PrintHandyApiClient stand-in returning recorded responses."""

    instances: list["FakeApiClient"] = []
    spools_body = SPOOLS_BODY
    spool_error: Exception | None = None

    def __init__(self, session=None, base_url="", token=""):
        """Initialize the fake client."""
        self.base_url = "http://ph.local:8080"
        self.spool_calls: list[tuple] = []
        FakeApiClient.instances.append(self)

    async def async_get_status(self):
        """Return the recorded snapshot."""
        return parse_snapshot(SNAPSHOT_BODY)

    async def async_get_roster(self):
        """Return no roster, like a server before Print Handy #1400."""
        return None

    async def async_get_printer_statuses(self, printer_ids):
        """Return an unavailable status per printer."""
        return {
            printer_id: unavailable_status(printer_id, "fake")
            for printer_id in printer_ids
        }

    async def async_get_printer_spool_views(self, printer_ids):
        """Return the recorded spools view per printer."""
        return {
            printer_id: parse_spools_view(FakeApiClient.spools_body, printer_id)
            for printer_id in printer_ids
        }

    async def _spool(self, action, *args, **kwargs):
        self.spool_calls.append((action, args, kwargs))
        if FakeApiClient.spool_error is not None:
            raise FakeApiClient.spool_error
        return {"ok": True, "action": action}

    async def async_assign_spool(self, printer_id, slot, spool_id):
        """Record an assign call."""
        return await self._spool("assign", printer_id, slot, spool_id)

    async def async_swap_spools(self, printer_id, slot_a, slot_b):
        """Record a swap call."""
        return await self._spool("swap", printer_id, slot_a, slot_b)

    async def async_unassign_spool(self, printer_id, slot):
        """Record an unassign call."""
        return await self._spool("unassign", printer_id, slot)

    async def async_add_spool(self, printer_id, **kwargs):
        """Record an add call."""
        return await self._spool("add", printer_id, **kwargs)

    async def async_sync_spools(self, printer_id):
        """Record a sync call."""
        return await self._spool("sync", printer_id)


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


def _slot_state(hass: HomeAssistant, slot: int):
    for state in hass.states.async_all("sensor"):
        if state.attributes.get("slot") == slot and "spool" in (
            state.attributes.get("friendly_name") or ""
        ).lower():
            return state
    return None


async def test_slot_sensors_reflect_the_poll(hass: HomeAssistant):
    """One sensor per slot: state is the spool name, detail in attributes."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        slot2 = _slot_state(hass, 2)
        assert slot2 is not None
        assert slot2.state == "Pink PLA"
        assert slot2.attributes["material"] == "PLA"
        assert slot2.attributes["color"] == "Pink"
        assert slot2.attributes["remaining_weight"] == 500.0
        assert slot2.attributes["remaining_percent"] == 50.0
        assert slot2.attributes["spool_id"] == 3
        slot1 = _slot_state(hass, 1)
        assert slot1 is not None
        assert slot1.state == "unknown"  # empty slot: no spool name
        assert slot1.attributes["spool_id"] is None


async def test_slot_sensors_update_after_a_poll_cycle(hass: HomeAssistant):
    """A coordinator refresh with new spool data updates the sensors."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        entry = await _setup(hass)
        assert _slot_state(hass, 1).state == "unknown"
        changed = {
            **SPOOLS_BODY,
            "slots": [
                {"slot": 1, "spool": SPOOLS_BODY["shelf"][0]},
                *SPOOLS_BODY["slots"][1:],
            ],
        }
        FakeApiClient.spools_body = changed
        # Spools are on their own slow cadence: let it come due.
        with patch(_CLOCK, return_value=10_000_000.0):
            await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        slot1 = _slot_state(hass, 1)
        assert slot1.state == "Coffee PLA+"
        assert slot1.attributes["spool_id"] == 7
        assert slot1.attributes["remaining_weight"] == 800.0


async def test_assign_service_posts_through_the_coordinator(hass: HomeAssistant):
    """print_handy.assign_spool reaches the API client with its payload."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        await hass.services.async_call(
            DOMAIN,
            "assign_spool",
            {"printer_id": "flashforge-ad5x", "slot": 1, "spool_id": 7},
            blocking=True,
        )
        client = FakeApiClient.instances[-1]
        assert ("assign", ("flashforge-ad5x", 1, 7), {}) in client.spool_calls


async def test_swap_unassign_add_and_sync_services(hass: HomeAssistant):
    """The other spool services reach the client with their payloads."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        await hass.services.async_call(
            DOMAIN,
            "swap_spools",
            {"printer_id": "flashforge-ad5x", "slot_a": 1, "slot_b": 2},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            "unassign_spool",
            {"printer_id": "flashforge-ad5x", "slot": 2},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            "add_spool",
            {
                "printer_id": "flashforge-ad5x",
                "material": "PLA",
                "color": "White",
                "grams": 1000,
                "brand": "Sunlu",
            },
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            "sync_spools",
            {"printer_id": "flashforge-ad5x"},
            blocking=True,
        )
        calls = FakeApiClient.instances[-1].spool_calls
        assert ("swap", ("flashforge-ad5x", 1, 2), {}) in calls
        assert ("unassign", ("flashforge-ad5x", 2), {}) in calls
        add = next(call for call in calls if call[0] == "add")
        assert add[1] == ("flashforge-ad5x",)
        assert add[2]["material"] == "PLA"
        assert add[2]["color"] == "White"
        assert add[2]["grams"] == 1000.0
        assert ("sync", ("flashforge-ad5x",), {}) in calls


async def test_service_refusal_raises_home_assistant_error_with_reason(
    hass: HomeAssistant,
):
    """A named API refusal surfaces as HomeAssistantError with its reason."""
    FakeApiClient.spool_error = api.PrintHandySpoolRefusedError(
        "Print Handy refused the spool assign action for printer "
        "'flashforge-ad5x' (HTTP 403): controls are disabled"
    )
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(
                DOMAIN,
                "assign_spool",
                {"printer_id": "flashforge-ad5x", "slot": 1, "spool_id": 7},
                blocking=True,
            )
        assert "controls are disabled" in str(excinfo.value)


async def test_service_for_an_unknown_printer_raises(hass: HomeAssistant):
    """No default-printer fallback: an unknown id is a named error."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(
                DOMAIN,
                "sync_spools",
                {"printer_id": "ghost"},
                blocking=True,
            )
        assert "ghost" in str(excinfo.value)


async def test_slot_sensors_go_unavailable_when_the_spool_poll_fails(
    hass: HomeAssistant,
):
    """A failed spool read reads as unavailable, never as cleared slots."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        entry = await _setup(hass)
        assert _slot_state(hass, 1) is not None

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
        # An unavailable entity carries no extra_state_attributes, so the
        # slot-attribute lookup in _slot_state cannot find it; look the
        # slot sensors up by entity id instead.
        slot_states = [
            state
            for state in hass.states.async_all("sensor")
            if state.entity_id.endswith("_slot_1_spool")
        ]
        assert len(slot_states) == 1
        assert slot_states[0].state == "unavailable"


async def test_service_for_a_printer_on_two_instances_is_refused(
    hass: HomeAssistant,
):
    """The same printer id on two instances is ambiguous, never first-match."""
    with patch(
        "custom_components.print_handy.coordinator.PrintHandyApiClient",
        FakeApiClient,
    ):
        await _setup(hass)
        second = MockConfigEntry(
            domain=DOMAIN,
            data={"url": "http://ph2.local:8080", "token": "sekrit"},
            unique_id="http://ph2.local:8080",
        )
        second.add_to_hass(hass)
        await hass.config_entries.async_setup(second.entry_id)
        await hass.async_block_till_done()
        with pytest.raises(HomeAssistantError) as excinfo:
            await hass.services.async_call(
                DOMAIN,
                "assign_spool",
                {"printer_id": "flashforge-ad5x", "slot": 1, "spool_id": 7},
                blocking=True,
            )
        assert "more than one configured" in str(excinfo.value)
        for client in FakeApiClient.instances:
            assert client.spool_calls == []
