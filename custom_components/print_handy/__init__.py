"""Print Handy integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import ISSUE_URL, PLATFORMS, VERSION
from .coordinator import PrintHandyDataUpdateCoordinator

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    PrintHandyConfigEntry = ConfigEntry[PrintHandyDataUpdateCoordinator]
else:
    # ConfigEntry became runtime-subscriptable in Home Assistant 2024.6.
    PrintHandyConfigEntry = ConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: PrintHandyConfigEntry) -> bool:
    """Set up Print Handy from a config entry."""
    LOGGER.info(
        "Print Handy integration version %s starting. Issue tracker: %s",
        VERSION,
        ISSUE_URL,
    )

    coordinator = PrintHandyDataUpdateCoordinator(hass, entry=entry)
    entry.runtime_data = coordinator

    await coordinator.async_config_entry_first_refresh()

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: PrintHandyConfigEntry) -> None:
    """Reload config entry on options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PrintHandyConfigEntry) -> bool:
    """Unload a config entry."""
    LOGGER.debug("Unloading Print Handy integration for entry: %s", entry.entry_id)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
