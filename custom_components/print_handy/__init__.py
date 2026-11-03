"""Print Handy integration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import (
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    SERVICE_ADD_SPOOL,
    SERVICE_ASSIGN_SPOOL,
    SERVICE_SWAP_SPOOLS,
    SERVICE_SYNC_SPOOLS,
    SERVICE_UNASSIGN_SPOOL,
    SPOOL_SERVICES,
    VERSION,
)
from .coordinator import PrintHandyDataUpdateCoordinator

LOGGER = logging.getLogger(__name__)

if TYPE_CHECKING:
    PrintHandyConfigEntry = ConfigEntry[PrintHandyDataUpdateCoordinator]
else:
    # ConfigEntry became runtime-subscriptable in Home Assistant 2024.6.
    PrintHandyConfigEntry = ConfigEntry

_SLOT = vol.All(vol.Coerce(int), vol.Range(min=1, max=4))
_SPOOL_ID = vol.All(vol.Coerce(int), vol.Range(min=1))

SERVICE_ASSIGN_SCHEMA = vol.Schema(
    {
        vol.Required("printer_id"): cv.string,
        vol.Required("slot"): _SLOT,
        vol.Required("spool_id"): _SPOOL_ID,
    }
)
SERVICE_SWAP_SCHEMA = vol.Schema(
    {
        vol.Required("printer_id"): cv.string,
        vol.Required("slot_a"): _SLOT,
        vol.Required("slot_b"): _SLOT,
    }
)
SERVICE_UNASSIGN_SCHEMA = vol.Schema(
    {
        vol.Required("printer_id"): cv.string,
        vol.Required("slot"): _SLOT,
    }
)
SERVICE_ADD_SCHEMA = vol.Schema(
    {
        vol.Required("printer_id"): cv.string,
        vol.Required("material"): cv.string,
        vol.Required("color"): cv.string,
        vol.Required("grams"): vol.All(
            vol.Coerce(float), vol.Range(min=0, min_included=False)
        ),
        vol.Optional("brand"): cv.string,
        vol.Optional("hex"): cv.string,
    }
)
SERVICE_SYNC_SCHEMA = vol.Schema({vol.Required("printer_id"): cv.string})

_SERVICE_SCHEMAS = {
    SERVICE_ASSIGN_SPOOL: SERVICE_ASSIGN_SCHEMA,
    SERVICE_SWAP_SPOOLS: SERVICE_SWAP_SCHEMA,
    SERVICE_UNASSIGN_SPOOL: SERVICE_UNASSIGN_SCHEMA,
    SERVICE_ADD_SPOOL: SERVICE_ADD_SCHEMA,
    SERVICE_SYNC_SPOOLS: SERVICE_SYNC_SCHEMA,
}


def _coordinator_for_printer(
    hass: HomeAssistant, printer_id: str
) -> PrintHandyDataUpdateCoordinator:
    """Find the loaded coordinator whose roster carries ``printer_id``.

    Falls back to the only loaded coordinator when there is exactly
    one and it has no snapshot yet to check the roster against. An
    unknown printer on a loaded entry is a named HomeAssistantError,
    mirroring the spool API's own 404 — there is no default-printer
    fallback for a service call either.
    """
    coordinators: list[PrintHandyDataUpdateCoordinator] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        if isinstance(runtime_data, PrintHandyDataUpdateCoordinator):
            coordinators.append(runtime_data)
    matches = [
        coordinator for coordinator in coordinators
        if coordinator.data is not None
        and coordinator.data.printer(printer_id) is not None
    ]
    if len(matches) > 1:
        # Printer ids are only unique within one Print Handy instance
        # (entities disambiguate by base URL for the same reason): with
        # the same id on two configured instances there is no honest
        # first match, so refuse by name instead of silently acting on
        # one instance's spools.
        raise HomeAssistantError(
            f"Printer id '{printer_id}' exists on more than one configured "
            "Print Handy instance; the spool services cannot tell which one "
            "is meant, so nothing was changed. Rename the printer on one "
            "instance, or use that instance's Web UI."
        )
    if matches:
        return matches[0]
    if len(coordinators) == 1 and (
        coordinators[0].data is None or not coordinators[0].data.printers
    ):
        return coordinators[0]
    raise HomeAssistantError(
        f"No Print Handy printer with id '{printer_id}' is configured"
    )


def _async_register_spool_services(hass: HomeAssistant) -> None:
    """Register the #789 spool services, once per Home Assistant instance."""
    if hass.services.has_service(DOMAIN, SERVICE_ASSIGN_SPOOL):
        return

    async def _assign(call: ServiceCall) -> None:
        coordinator = _coordinator_for_printer(hass, call.data["printer_id"])
        await coordinator.async_assign_spool(
            call.data["printer_id"], call.data["slot"], call.data["spool_id"]
        )

    async def _swap(call: ServiceCall) -> None:
        coordinator = _coordinator_for_printer(hass, call.data["printer_id"])
        await coordinator.async_swap_spools(
            call.data["printer_id"], call.data["slot_a"], call.data["slot_b"]
        )

    async def _unassign(call: ServiceCall) -> None:
        coordinator = _coordinator_for_printer(hass, call.data["printer_id"])
        await coordinator.async_unassign_spool(
            call.data["printer_id"], call.data["slot"]
        )

    async def _add(call: ServiceCall) -> None:
        coordinator = _coordinator_for_printer(hass, call.data["printer_id"])
        await coordinator.async_add_spool(
            call.data["printer_id"],
            material=call.data["material"],
            color=call.data["color"],
            grams=call.data["grams"],
            brand=call.data.get("brand"),
            hex=call.data.get("hex"),
        )

    async def _sync(call: ServiceCall) -> None:
        coordinator = _coordinator_for_printer(hass, call.data["printer_id"])
        await coordinator.async_sync_spools(call.data["printer_id"])

    handlers: dict[str, Any] = {
        SERVICE_ASSIGN_SPOOL: _assign,
        SERVICE_SWAP_SPOOLS: _swap,
        SERVICE_UNASSIGN_SPOOL: _unassign,
        SERVICE_ADD_SPOOL: _add,
        SERVICE_SYNC_SPOOLS: _sync,
    }
    for service, handler in handlers.items():
        hass.services.async_register(
            DOMAIN, service, handler, schema=_SERVICE_SCHEMAS[service]
        )


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

    _async_register_spool_services(hass)

    return True


async def async_reload_entry(hass: HomeAssistant, entry: PrintHandyConfigEntry) -> None:
    """Reload config entry on options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: PrintHandyConfigEntry) -> bool:
    """Unload a config entry."""
    LOGGER.debug("Unloading Print Handy integration for entry: %s", entry.entry_id)
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    remaining = [
        other
        for other in hass.config_entries.async_loaded_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]
    if unloaded and not remaining:
        for service in SPOOL_SERVICES:
            hass.services.async_remove(DOMAIN, service)
    return unloaded
