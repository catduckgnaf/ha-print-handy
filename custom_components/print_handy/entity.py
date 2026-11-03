"""Shared entity base for Print Handy."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import PrintHandyConfigEntry
from .const import DOMAIN, NAME
from .coordinator import PrintHandyDataUpdateCoordinator
from .model import PrinterInfo, PrinterSpools, PrinterStatus


def instance_device_info(coordinator: PrintHandyDataUpdateCoordinator) -> DeviceInfo:
    """Device info for the Print Handy instance (the hub device)."""
    instance = coordinator.data.instance if coordinator.data else None
    return DeviceInfo(
        identifiers={(DOMAIN, f"instance:{coordinator.client.base_url}")},
        name=NAME,
        manufacturer=NAME,
        model="Print Handy instance",
        sw_version=instance.version if instance else None,
        configuration_url=coordinator.client.base_url,
    )


def printer_device_info(
    coordinator: PrintHandyDataUpdateCoordinator, printer: PrinterInfo
) -> DeviceInfo:
    """Device info for one printer on the roster."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"printer:{printer.printer_id}")},
        name=printer.display_name,
        manufacturer=printer.vendor or NAME,
        model=printer.model or None,
        via_device=(DOMAIN, f"instance:{coordinator.client.base_url}"),
    )


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


class PrintHandyPrinterEntity(CoordinatorEntity[PrintHandyDataUpdateCoordinator]):
    """Base entity bound to one printer on the roster."""

    _attr_has_entity_name = True
    _attr_attribution = "Data provided by Print Handy"

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
        """Available while the coordinator has data and the printer is on the roster."""
        return super().available and self.printer is not None
