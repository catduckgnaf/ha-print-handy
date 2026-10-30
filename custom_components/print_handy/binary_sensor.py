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
from .entity import PrintHandyEntity, PrintHandyPrinterEntity
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PrintHandyBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Class describing Print Handy instance binary sensors."""

    value_fn: Any = None  # Callable[[InstanceInfo], bool | None]


INSTANCE_BINARY_SENSORS: tuple[PrintHandyBinarySensorEntityDescription, ...] = (
    PrintHandyBinarySensorEntityDescription(
        key="bot_connected",
        name="Bot connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda instance: instance.bot_connected,
    ),
    PrintHandyBinarySensorEntityDescription(
        key="spoolman_connected",
        name="Spoolman connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
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
    entities: list[BinarySensorEntity] = [
        PrintHandyInstanceBinarySensor(coordinator, entry, description)
        for description in INSTANCE_BINARY_SENSORS
    ]
    if coordinator.data is not None:
        entities.extend(
            PrintHandyPrinterOnlineBinarySensor(coordinator, entry, printer)
            for printer in coordinator.data.printers
        )
    async_add_entities(entities)


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
        self._attr_unique_id = (
            f"{coordinator.client.base_url}:instance:{description.key}"
        )

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
    _attr_name = "Online"

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_unique_id = f"{coordinator.client.base_url}:printer:{printer.printer_id}:online"

    @property
    def is_on(self) -> bool | None:
        """Return the listener health for this printer."""
        printer = self.printer
        return printer.listener_ok if printer else None
