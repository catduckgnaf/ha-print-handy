"""Sensors for Print Handy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import PrintHandyEntity, PrintHandyPrinterEntity
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PrintHandySensorEntityDescription(SensorEntityDescription):
    """Class describing Print Handy instance sensors."""

    value_fn: Any = None  # Callable[[InstanceInfo], str | int | None]


INSTANCE_SENSORS: tuple[PrintHandySensorEntityDescription, ...] = (
    PrintHandySensorEntityDescription(
        key="status",
        name="Status",
        icon="mdi:printer-3d-nozzle",
        value_fn=lambda instance: "ok" if instance.ok else "problem",
    ),
    PrintHandySensorEntityDescription(
        key="version",
        name="Version",
        icon="mdi:tag",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda instance: instance.version or None,
    ),
    PrintHandySensorEntityDescription(
        key="doctor_failures",
        name="Doctor failures",
        icon="mdi:stethoscope",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda instance: instance.doctor_failures,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy sensors from a config entry."""
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        PrintHandyInstanceSensor(coordinator, entry, description)
        for description in INSTANCE_SENSORS
    ]
    if coordinator.data is not None:
        entities.extend(
            PrintHandyPrinterConnectionSensor(coordinator, entry, printer)
            for printer in coordinator.data.printers
        )
    async_add_entities(entities)


class PrintHandyInstanceSensor(PrintHandyEntity, SensorEntity):
    """One sensor reading a field of the instance snapshot."""

    entity_description: PrintHandySensorEntityDescription

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        description: PrintHandySensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.client.base_url}:instance:{description.key}"
        )

    @property
    def native_value(self) -> str | int | None:
        """Return the snapshot field."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data.instance)


class PrintHandyPrinterConnectionSensor(PrintHandyPrinterEntity, SensorEntity):
    """Diagnostic sensor: how Print Handy reaches this printer."""

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_name = "Connection"
        self._attr_icon = "mdi:connection"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC
        self._attr_unique_id = f"{coordinator.client.base_url}:printer:{printer.printer_id}:connection"

    @property
    def native_value(self) -> str | None:
        """Return the connection kind (moonraker, octoprint, ...)."""
        printer = self.printer
        return printer.connection if printer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Card-friendly attributes: model, vendor and listener health."""
        printer = self.printer
        if printer is None:
            return {}
        return {
            "model": printer.model,
            "vendor": printer.vendor,
            "default": printer.is_default,
            "listener_required": printer.listener_required,
            "listener_reason": printer.listener_reason,
        }
