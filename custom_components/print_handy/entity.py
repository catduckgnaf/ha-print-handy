"""Shared entity base for Print Handy."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, NAME
from .coordinator import PrintHandyDataUpdateCoordinator
from .model import PrinterInfo, PrinterSpools, PrinterStatus

if TYPE_CHECKING:
    from . import PrintHandyConfigEntry


def find_device(
    registry: dr.DeviceRegistry, identifier: tuple[str, str], entry_id: str
) -> dr.DeviceEntry | None:
    """Look a device up by identifier within one config entry.

    Newer Home Assistant scopes identifiers to a config entry and
    deprecates the global lookup; older versions only have the global one.
    """
    lookup = getattr(registry, "async_get_device_by_identifier", None)
    if lookup is not None:
        return lookup(identifier, entry_id)
    return registry.async_get_device(identifiers={identifier})


def instance_identifier(instance_key: str) -> str:
    """Return the device identifier of the instance (hub) device."""
    return f"{instance_key}:instance"


def printer_identifier(instance_key: str, printer_id: str) -> str:
    """Return the device identifier of one printer, scoped to its instance."""
    return f"{instance_key}:printer:{printer_id}"


def instance_unique_id(coordinator: PrintHandyDataUpdateCoordinator, key: str) -> str:
    """Return the unique id of an instance-level entity."""
    return f"{coordinator.instance_key}:instance:{key}"


def printer_unique_id(
    coordinator: PrintHandyDataUpdateCoordinator, printer_id: str, key: str
) -> str:
    """Return the unique id of a printer-level entity (``key`` may carry its own colons)."""
    return f"{coordinator.instance_key}:printer:{printer_id}:{key}"


@callback
def async_add_entities_as_known(
    coordinator: PrintHandyDataUpdateCoordinator,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
    build: Callable[[], Iterable[Entity]],
) -> None:
    """Add a platform's entities now and as the coordinator learns of more.

    ``build`` returns every entity the current data calls for. Each one is
    added once, keyed by its unique id, so a printer that joins the roster
    later (or whose gated fields only show up once it is online) gains its
    entities on the next update without a reload.
    """
    added: set[str] = set()

    @callback
    def _add_new() -> None:
        new: list[Entity] = []
        for entity in build():
            unique_id = entity.unique_id
            if unique_id is None or unique_id in added:
                continue
            added.add(unique_id)
            new.append(entity)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def instance_device_info(coordinator: PrintHandyDataUpdateCoordinator) -> DeviceInfo:
    """Device info for the Print Handy instance (the hub device)."""
    instance = coordinator.data.instance if coordinator.data else None
    return DeviceInfo(
        identifiers={(DOMAIN, instance_identifier(coordinator.instance_key))},
        name=NAME,
        manufacturer=NAME,
        model="Print Handy instance",
        sw_version=instance.version if instance else None,
        configuration_url=coordinator.client.base_url,
    )


@callback
def async_ensure_instance_device(
    coordinator: PrintHandyDataUpdateCoordinator,
) -> dr.DeviceEntry:
    """Register the instance (hub) device so printer devices can point at it."""
    return dr.async_get(coordinator.hass).async_get_or_create(
        config_entry_id=coordinator.config_entry.entry_id,
        **instance_device_info(coordinator),
    )


def printer_device_info(
    coordinator: PrintHandyDataUpdateCoordinator, printer: PrinterInfo
) -> DeviceInfo:
    """Device info for one printer on the roster."""
    info = DeviceInfo(
        identifiers={
            (DOMAIN, printer_identifier(coordinator.instance_key, printer.printer_id))
        },
        name=printer.display_name,
        manufacturer=printer.vendor or NAME,
        model=printer.model or None,
    )
    hub = find_device(
        dr.async_get(coordinator.hass),
        (DOMAIN, instance_identifier(coordinator.instance_key)),
        coordinator.config_entry.entry_id,
    )
    if hub is not None and "via_device_id" in DeviceInfo.__annotations__:
        # Newer Home Assistant takes the parent's registry id; the
        # identifier tuple form is deprecated there.
        info["via_device_id"] = hub.id
    else:
        info["via_device"] = (DOMAIN, instance_identifier(coordinator.instance_key))
    return info


class PrintHandyEntity(CoordinatorEntity[PrintHandyDataUpdateCoordinator]):
    """Base entity bound to the instance snapshot."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by Print Handy"

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = instance_device_info(coordinator)

    @property
    def available(self) -> bool:
        """Unavailable while the slow hub refresh is failing.

        Only the instance-level entities read the hub snapshot's health;
        the printer entities stay available while their own polls succeed.
        """
        return super().available and self.coordinator.hub_available


class PrintHandyPrinterEntity(CoordinatorEntity[PrintHandyDataUpdateCoordinator]):
    """Base entity bound to one printer on the roster."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by Print Handy"
    # Entities that read the printer's live status are unavailable while
    # Print Handy reports the printer itself as unavailable.
    _requires_live_status = False
    # Control entities are unavailable while the server says its printer
    # controls are off ([webui] controls_enabled = false): every press or
    # set would only be refused. Servers before #1400 do not say, and
    # their controls stay available (a refusal still fails visibly).
    _requires_controls = False

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator)
        self._entry = entry
        self._printer_id = printer.printer_id
        self._attr_device_info = printer_device_info(coordinator, printer)

    @property
    def printer(self) -> PrinterInfo | None:
        """The current roster row for this printer, or None if it left the roster."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.printer(self._printer_id)

    @property
    def status(self) -> PrinterStatus | None:
        """The current live status for this printer, or None if not polled."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.status(self._printer_id)

    @property
    def spool_view(self) -> PrinterSpools | None:
        """The current spools view for this printer, or None if not polled."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.spool_view(self._printer_id)

    @property
    def available(self) -> bool:
        """Available while the coordinator has data and the printer is on the roster.

        Live-status entities are also unavailable while the printer's own
        status says it cannot be read, instead of reporting a made-up value.
        """
        printer = self.printer
        if not (super().available and printer is not None):
            return False
        if self._requires_controls and printer.controls_enabled is False:
            return False
        if self._requires_live_status:
            status = self.status
            return status is not None and not status.unavailable
        return True
