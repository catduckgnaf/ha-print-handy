"""Coordinator tests: split cadence and error mapping."""

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import ConfigEntryAuthFailed  # noqa: E402
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.print_handy import api  # noqa: E402
from custom_components.print_handy.const import DOMAIN  # noqa: E402
from tests.fake_client import CLOCK_PATH, Clock, World, patched  # noqa: E402
from tests.fixtures import BAMBU_ROW, MOONRAKER_ROW  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


async def _setup(hass: HomeAssistant, world: World, clock: Clock):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"url": "http://ph.local:8088", "token": "t"},
        unique_id="abc",
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


async def test_hub_is_slow_printers_are_fast(hass: HomeAssistant):
    """/api/status runs at startup and every hub interval; statuses every tick."""
    world, clock = World(), Clock()
    entry = await _setup(hass, world, clock)
    assert (world.hub_calls, world.status_calls, world.spool_calls) == (1, 1, 1)
    for _ in range(5):
        await _tick(hass, entry, world, clock, 30)
    assert world.hub_calls == 1  # 150 s: still inside the 300 s hub interval
    assert world.status_calls == 6
    assert world.spool_calls == 2  # the 120 s spool cadence came due once
    await _tick(hass, entry, world, clock, 200)
    assert world.hub_calls == 2


async def test_failed_hub_refresh_keeps_printers_available(hass: HomeAssistant):
    """A slow or failing hub never flips printer entities unavailable."""
    world, clock = World(), Clock()
    entry = await _setup(hass, world, clock)
    world.hub_error = api.PrintHandyServerError("HTTP 500")
    await _tick(hass, entry, world, clock, 301)
    coordinator = entry.runtime_data
    assert coordinator.last_update_success
    assert coordinator.hub_error == "HTTP 500"
    assert coordinator.data.printers  # roster kept
    state = hass.states.get("sensor.voron_current_state")
    assert state is not None and state.state == "standby"
    # instance-level entities do go unavailable
    assert hass.states.get("sensor.print_handy_version").state == "unavailable"
    # retried after the short retry interval, and recovery clears it
    world.hub_error = None
    await _tick(hass, entry, world, clock, 61)
    assert coordinator.hub_error is None
    assert hass.states.get("sensor.print_handy_version").state == "2.2.7"


async def test_first_refresh_hub_failure_retries_setup(hass: HomeAssistant):
    """First refresh hub failure retries setup."""
    world, clock = World(hub_error=api.PrintHandyServerError("HTTP 500")), Clock()
    entry = await _setup(hass, world, clock)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_starts_reauth(hass: HomeAssistant):
    """Auth failure starts reauth."""
    world, clock = World(hub_error=api.PrintHandyAuthError("no")), Clock()
    entry = await _setup(hass, world, clock)
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(hass.config_entries.flow.async_progress_by_handler(DOMAIN))


async def test_rate_limit_backs_off_without_hammering(hass: HomeAssistant):
    """Rate limit backs off without hammering."""
    world, clock = World(), Clock()
    entry = await _setup(hass, world, clock)
    world.status_error = api.PrintHandyRateLimitedError("slow down", 120)
    await _tick(hass, entry, world, clock, 30)
    assert not entry.runtime_data.last_update_success
    calls = world.status_calls
    await _tick(hass, entry, world, clock, 30)  # inside the 120 s wait
    assert world.status_calls == calls
    world.status_error = None
    await _tick(hass, entry, world, clock, 120)
    assert entry.runtime_data.last_update_success


@pytest.mark.parametrize(
    ("error", "needle"),
    [
        (api.PrintHandyHostRefusedError(api.HOST_REFUSED_MESSAGE), "IP address"),
        (api.PrintHandyConnectionError("down"), "down"),
        (api.PrintHandyApiError("odd"), "odd"),
    ],
)
async def test_errors_map_to_update_failed(hass: HomeAssistant, error, needle):
    """Errors map to update failed."""
    world, clock = World(), Clock()
    entry = await _setup(hass, world, clock)
    world.status_error = error
    coordinator = entry.runtime_data
    with patched(world), patch(CLOCK_PATH, clock), pytest.raises(UpdateFailed) as err:
        await coordinator._async_update_data()
    assert needle in str(err.value)


async def test_auth_error_on_poll_raises_auth_failed(hass: HomeAssistant):
    """Auth error on poll raises auth failed."""
    world, clock = World(), Clock()
    entry = await _setup(hass, world, clock)
    world.status_error = api.PrintHandyAuthError("no")
    with patched(world), patch(CLOCK_PATH, clock), pytest.raises(ConfigEntryAuthFailed):
        await entry.runtime_data._async_update_data()


async def test_spool_service_rereads_spools_immediately(hass: HomeAssistant):
    """Spool service rereads spools immediately."""
    world, clock = World(), Clock()
    await _setup(hass, world, clock)
    before = world.spool_calls
    with patched(world), patch(CLOCK_PATH, clock):
        await hass.services.async_call(
            DOMAIN,
            "assign_spool",
            {"printer_id": "voron", "slot": 1, "spool_id": 7},
            blocking=True,
        )
        await hass.async_block_till_done()
    assert world.spool_calls == before + 1


async def test_new_roster_printer_gets_entities_without_reload(hass: HomeAssistant):
    """New roster printer gets entities without reload."""
    world, clock = World(), Clock()
    entry = await _setup(hass, world, clock)
    assert hass.states.get("sensor.x1c_current_state") is None
    world.rows = [MOONRAKER_ROW, BAMBU_ROW]
    await _tick(hass, entry, world, clock, 301)
    assert hass.states.get("sensor.x1c_current_state") is not None
