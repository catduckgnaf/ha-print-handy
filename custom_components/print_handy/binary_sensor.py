"""Binary sensors for Print Handy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import (
    PrintHandyEntity,
    PrintHandyPrinterEntity,
    async_add_entities_as_known,
    instance_unique_id,
    printer_unique_id,
)
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PrintHandyBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Class describing Print Handy instance binary sensors."""

    value_fn: Any = None  # Callable[[InstanceInfo], bool | None]


INSTANCE_BINARY_SENSORS: tuple[PrintHandyBinarySensorEntityDescription, ...] = (
    PrintHandyBinarySensorEntityDescription(
        key="bot_connected",
        translation_key="bot_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda instance: instance.bot_connected,
    ),
    PrintHandyBinarySensorEntityDescription(
        key="spoolman_connected",
        translation_key="spoolman_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # None (shown as unknown) when Spoolman is not configured.
        value_fn=lambda instance: instance.spoolman_ok,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy binary sensors from a config entry."""
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data

    def build() -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = [
            PrintHandyInstanceBinarySensor(coordinator, entry, description)
            for description in INSTANCE_BINARY_SENSORS
        ]
        if coordinator.data is not None:
            for printer in coordinator.data.printers:
                entities.append(
                    PrintHandyPrinterOnlineBinarySensor(coordinator, entry, printer)
                )
                entities.append(
                    PrintHandyPrinterPrintingBinarySensor(coordinator, entry, printer)
                )
                entities.append(
                    PrintHandyPrinterPrintingErrorBinarySensor(
                        coordinator, entry, printer
                    )
                )
        return entities

    async_add_entities_as_known(coordinator, entry, async_add_entities, build)


class PrintHandyInstanceBinarySensor(PrintHandyEntity, BinarySensorEntity):
    """One binary sensor reading a field of the instance snapshot."""

    entity_description: PrintHandyBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        description: PrintHandyBinarySensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = instance_unique_id(coordinator, description.key)

    @property
    def is_on(self) -> bool | None:
        """Return the snapshot field."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data.instance)


class PrintHandyPrinterOnlineBinarySensor(PrintHandyPrinterEntity, BinarySensorEntity):
    """On while Print Handy's listener reports this printer healthy.

    Off when the listener reports a problem; unknown (None) when the
    printer's connection needs no listener or has not been checked.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_unique_id = printer_unique_id(coordinator, printer.printer_id, "online")

    @property
    def is_on(self) -> bool | None:
        """Return the listener health for this printer."""
        printer = self.printer
        return printer.listener_ok if printer else None


class PrintHandyPrinterPrintingBinarySensor(PrintHandyPrinterEntity, BinarySensorEntity):
    """On while this printer's live status is "printing"."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_translation_key = "printing"
    _requires_live_status = True

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_unique_id = printer_unique_id(coordinator, printer.printer_id, "printing")

    @property
    def is_on(self) -> bool | None:
        """True while printing; None before the first status poll."""
        status = self.status
        return status.state == "printing" if status else None


class PrintHandyPrinterPrintingErrorBinarySensor(
    PrintHandyPrinterEntity, BinarySensorEntity
):
    """On while this printer's live status is "error" (problem class)."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "printing_error"
    _requires_live_status = True

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, "printing_error"
        )

    @property
    def is_on(self) -> bool | None:
        """True while in the error state; None before the first status poll."""
        status = self.status
        return status.state == "error" if status else None
