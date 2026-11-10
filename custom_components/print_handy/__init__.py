"""Print Handy integration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType

from .api import normalize_base_url
from .const import (
    CONF_INSTANCE_ID,
    DOMAIN,
    ISSUE_URL,
    PLATFORMS,
    SERVICE_ADD_SPOOL,
    SERVICE_ASSIGN_SPOOL,
    SERVICE_EXCLUDE_OBJECT,
    SERVICE_SEND_GCODE,
    SERVICE_SWAP_SPOOLS,
    SERVICE_SYNC_SPOOLS,
    SERVICE_UNASSIGN_SPOOL,
    VERSION,
)
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import async_ensure_instance_device
from .repairs import async_sync_doctor_repairs

LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

if TYPE_CHECKING:
    PrintHandyConfigEntry = ConfigEntry[PrintHandyDataUpdateCoordinator]
else:
    # ConfigEntry became runtime-subscriptable in Home Assistant 2024.6.
    PrintHandyConfigEntry = ConfigEntry

# Config entry version 2 keys entities and devices on a stable instance id
# instead of the typed URL (see async_migrate_entry).
ENTRY_VERSION = 2

_SLOT = vol.All(vol.Coerce(int), vol.Range(min=1, max=4))
_SPOOL_ID = vol.All(vol.Coerce(int), vol.Range(min=1))
_NON_EMPTY = vol.All(cv.string, vol.Length(min=1))

# Every service names its printer either by the free-text Print Handy
# printer id (the original field, still accepted) or by picking the
# printer's device. At least one is required.
_TARGET_FIELDS: dict[Any, Any] = {
    vol.Optional("printer_id"): cv.string,
    vol.Optional("device_id"): vol.All(cv.ensure_list, [cv.string]),
}


def _service_schema(fields: dict[Any, Any]) -> vol.Schema:
    return vol.Schema(
        vol.All(
            vol.Schema({**_TARGET_FIELDS, **fields}),
            cv.has_at_least_one_key("printer_id", "device_id"),
        )
    )


SERVICE_ASSIGN_SCHEMA = _service_schema(
    {
        vol.Required("slot"): _SLOT,
        vol.Required("spool_id"): _SPOOL_ID,
    }
)
SERVICE_SWAP_SCHEMA = _service_schema(
    {
        vol.Required("slot_a"): _SLOT,
        vol.Required("slot_b"): _SLOT,
    }
)
SERVICE_UNASSIGN_SCHEMA = _service_schema({vol.Required("slot"): _SLOT})
SERVICE_ADD_SCHEMA = _service_schema(
    {
        vol.Required("material"): cv.string,
        vol.Required("color"): cv.string,
        vol.Required("grams"): vol.All(
            vol.Coerce(float), vol.Range(min=0, min_included=False)
        ),
        vol.Optional("brand"): cv.string,
        vol.Optional("hex"): cv.string,
    }
)
SERVICE_SYNC_SCHEMA = _service_schema({})
SERVICE_SEND_GCODE_SCHEMA = _service_schema({vol.Required("script"): _NON_EMPTY})
SERVICE_EXCLUDE_OBJECT_SCHEMA = _service_schema({vol.Required("object"): _NON_EMPTY})


def _loaded_coordinators(hass: HomeAssistant) -> list[PrintHandyDataUpdateCoordinator]:
    coordinators: list[PrintHandyDataUpdateCoordinator] = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runtime_data = getattr(entry, "runtime_data", None)
        if isinstance(runtime_data, PrintHandyDataUpdateCoordinator):
            coordinators.append(runtime_data)
    return coordinators


def _coordinator_for_printer(
    hass: HomeAssistant, printer_id: str
) -> PrintHandyDataUpdateCoordinator:
    """Find the loaded coordinator whose roster carries ``printer_id``.

    Falls back to the only loaded coordinator when there is exactly
    one and it has no snapshot yet to check the roster against. An
    unknown printer on a loaded entry is a named HomeAssistantError,
    mirroring the spool API's own 404 - there is no default-printer
    fallback for a service call either.
    """
    coordinators = _loaded_coordinators(hass)
    matches = [
        coordinator for coordinator in coordinators
        if coordinator.data is not None
        and coordinator.data.printer(printer_id) is not None
    ]
    if len(matches) > 1:
        # Printer ids are only unique within one Print Handy instance:
        # with the same id on two configured instances there is no honest
        # first match, so refuse by name instead of silently acting on
        # one instance's printer. Picking the printer's device removes
        # the ambiguity.
        raise HomeAssistantError(
            f"Printer id '{printer_id}' exists on more than one configured "
            "Print Handy instance; the free-text printer id cannot tell "
            "which one is meant, so nothing was changed. Pick the printer's "
            "device as the target instead."
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


def _target_for_device(
    hass: HomeAssistant, device_id: str
) -> tuple[PrintHandyDataUpdateCoordinator, str]:
    """Resolve a picked device to its coordinator and Print Handy printer id."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise HomeAssistantError(f"Device '{device_id}' does not exist")
    for domain, identifier in device.identifiers:
        if domain != DOMAIN:
            continue
        for coordinator in _loaded_coordinators(hass):
            prefix = f"{coordinator.instance_key}:printer:"
            if identifier.startswith(prefix):
                return coordinator, identifier[len(prefix):]
    raise HomeAssistantError(
        f"Device '{device.name_by_user or device.name}' is not a loaded "
        "Print Handy printer"
    )


def _targets(
    hass: HomeAssistant, call: ServiceCall
) -> list[tuple[PrintHandyDataUpdateCoordinator, str]]:
    """Every (coordinator, printer id) the call names, without duplicates."""
    targets: list[tuple[PrintHandyDataUpdateCoordinator, str]] = []
    printer_id = call.data.get("printer_id")
    if printer_id:
        targets.append((_coordinator_for_printer(hass, printer_id), printer_id))
    for device_id in call.data.get("device_id", []):
        target = _target_for_device(hass, device_id)
        if target not in targets:
            targets.append(target)
    return targets


def _service(
    hass: HomeAssistant,
    action: Callable[[PrintHandyDataUpdateCoordinator, str, ServiceCall], Awaitable[Any]],
) -> Callable[[ServiceCall], Awaitable[None]]:
    """Wrap one per-printer action into a service handler over all targets."""

    async def _handler(call: ServiceCall) -> None:
        for coordinator, printer_id in _targets(hass, call):
            await action(coordinator, printer_id, call)

    return _handler


def _async_register_services(hass: HomeAssistant) -> None:
    """Register every service. Called from async_setup, once per instance.

    Each call acts only on the printers it names (a printer id and/or
    picked devices), resolved through the roster, so an unknown or
    ambiguous id is a named error, never a default-printer fallback. Any
    Print Handy refusal propagates as a HomeAssistantError carrying the
    server's reason.
    """
    handlers: dict[str, tuple[Any, vol.Schema]] = {
        SERVICE_ASSIGN_SPOOL: (
            lambda c, p, call: c.async_assign_spool(
                p, call.data["slot"], call.data["spool_id"]
            ),
            SERVICE_ASSIGN_SCHEMA,
        ),
        SERVICE_SWAP_SPOOLS: (
            lambda c, p, call: c.async_swap_spools(
                p, call.data["slot_a"], call.data["slot_b"]
            ),
            SERVICE_SWAP_SCHEMA,
        ),
        SERVICE_UNASSIGN_SPOOL: (
            lambda c, p, call: c.async_unassign_spool(p, call.data["slot"]),
            SERVICE_UNASSIGN_SCHEMA,
        ),
        SERVICE_ADD_SPOOL: (
            lambda c, p, call: c.async_add_spool(
                p,
                material=call.data["material"],
                color=call.data["color"],
                grams=call.data["grams"],
                brand=call.data.get("brand"),
                hex=call.data.get("hex"),
            ),
            SERVICE_ADD_SCHEMA,
        ),
        SERVICE_SYNC_SPOOLS: (
            lambda c, p, call: c.async_sync_spools(p),
            SERVICE_SYNC_SCHEMA,
        ),
        SERVICE_SEND_GCODE: (
            lambda c, p, call: c.async_send_gcode(p, call.data["script"]),
            SERVICE_SEND_GCODE_SCHEMA,
        ),
        SERVICE_EXCLUDE_OBJECT: (
            lambda c, p, call: c.async_exclude_object(p, call.data["object"]),
            SERVICE_EXCLUDE_OBJECT_SCHEMA,
        ),
    }
    for service, (action, schema) in handlers.items():
        hass.services.async_register(
            DOMAIN, service, _service(hass, action), schema=schema
        )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the services once; they work on whatever entries are loaded."""
    _async_register_services(hass)
    return True


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
    async_ensure_instance_device(coordinator)

    @callback
    def _sync_repairs() -> None:
        async_sync_doctor_repairs(hass, entry, coordinator)

    _sync_repairs()
    entry.async_on_unload(coordinator.async_add_listener(_sync_repairs))

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


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PrintHandyConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a printer's device only once it has left the roster.

    The instance (hub) device and any printer Print Handy still lists
    cannot be removed: the integration would recreate them on the next
    update.
    """
    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, PrintHandyDataUpdateCoordinator):
        return False
    if coordinator.data is None:
        return False
    prefix = f"{coordinator.instance_key}:printer:"
    for domain, identifier in device_entry.identifiers:
        if domain != DOMAIN:
            continue
        if not identifier.startswith(prefix):
            return False
        if coordinator.data.printer(identifier[len(prefix):]) is not None:
            return False
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate a 0.5.x entry to the stable-identity layout.

    Version 1 used the typed base URL as the entry unique id and inside
    every entity unique id, and named devices ``instance:<url>`` and
    ``printer:<id>`` (not scoped to the entry). Version 2 uses the entry
    id as the instance id: entities become ``<instance>:...``, devices
    ``<instance>:instance`` and ``<instance>:printer:<id>``. Entity and
    device registry rows are rewritten in place, so entity ids, history,
    areas, customizations and automations survive.
    """
    if entry.version > ENTRY_VERSION:
        # Written by a newer release; do not guess at its layout.
        return False
    if entry.version == 1:
        instance_id = entry.entry_id
        try:
            old_url = normalize_base_url(str(entry.data.get(CONF_URL, "")))
        except ValueError:
            old_url = str(entry.data.get(CONF_URL, ""))
        await _migrate_entity_registry(hass, entry, old_url, instance_id)
        _migrate_device_registry(hass, entry, old_url, instance_id)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_INSTANCE_ID: instance_id},
            unique_id=instance_id,
            version=2,
        )
        LOGGER.info("Migrated Print Handy entry %s to version 2", entry.entry_id)
    return True


async def _migrate_entity_registry(
    hass: HomeAssistant, entry: ConfigEntry, old_url: str, instance_id: str
) -> None:
    """Rewrite ``<url>:...`` unique ids to ``<instance>:...``."""
    prefix = f"{old_url}:"

    def _new_unique_id(registry_entry: er.RegistryEntry) -> dict[str, Any] | None:
        unique_id = registry_entry.unique_id
        if not unique_id.startswith(prefix):
            return None
        return {"new_unique_id": f"{instance_id}:{unique_id[len(prefix):]}"}

    await er.async_migrate_entries(hass, entry.entry_id, _new_unique_id)


def _migrate_device_registry(
    hass: HomeAssistant, entry: ConfigEntry, old_url: str, instance_id: str
) -> None:
    """Rewrite the instance and printer device identifiers for this entry.

    A 0.5.x printer identifier was not scoped to an entry, so two
    instances sharing a printer id shared one device. While another
    entry that has not migrated yet is attached to such a device, the old
    identifier is kept so that entry still finds it; the last one to
    migrate drops it.
    """
    registry = dr.async_get(hass)
    old_instance = f"instance:{old_url}"
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        shared_with_unmigrated = any(
            other_id != entry.entry_id
            and (other := hass.config_entries.async_get_entry(other_id)) is not None
            and other.domain == DOMAIN
            and other.version < 2
            for other_id in device.config_entries
        )
        identifiers: set[tuple[str, str]] = set()
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                identifiers.add((domain, identifier))
            elif identifier == old_instance:
                identifiers.add((DOMAIN, f"{instance_id}:instance"))
            elif identifier.startswith("printer:"):
                identifiers.add(
                    (DOMAIN, f"{instance_id}:printer:{identifier[len('printer:'):]}")
                )
                if shared_with_unmigrated:
                    identifiers.add((domain, identifier))
            else:
                identifiers.add((domain, identifier))
        if identifiers != device.identifiers:
            registry.async_update_device(device.id, new_identifiers=identifiers)
