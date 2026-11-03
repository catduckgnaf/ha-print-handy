"""Sensors for Print Handy."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import PrintHandyEntity, PrintHandyPrinterEntity
from .model import (
    PrinterInfo,
    PrinterStatus,
    SlotSpool,
    active_ams_tray,
    ams_trays,
    has_ams_filament,
    slot_spool_attributes,
)

LOGGER = logging.getLogger(__name__)

# Print Handy's normalized print states (adapters + the status endpoint's
# own "unavailable"). The current-state enum sensor guards to this list.
PRINT_STATES = [
    "standby",
    "printing",
    "paused",
    "complete",
    "error",
    "cancelled",
    "unknown",
    "unavailable",
]


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


def _percent(status: PrinterStatus) -> float | None:
    """Progress as a percentage (the endpoint sends a 0..1 fraction)."""
    if status.progress is None:
        return None
    return round(status.progress * 100, 1)


def _finish_time(status: PrinterStatus) -> datetime | None:
    """Projected finish: now + the printer's own remaining-minutes."""
    if status.remaining_min is None:
        return None
    return datetime.now(UTC) + timedelta(minutes=status.remaining_min)


@dataclass(frozen=True, kw_only=True)
class PrintHandyPrinterSensorEntityDescription(SensorEntityDescription):
    """Class describing Print Handy per-printer live-status sensors.

    ``present_fn`` gates creation at setup: entities for data a printer's
    adapter never reports (targets, layers, remaining time) are only
    created when the first poll actually carries the field, so simple
    printers do not collect permanently-empty entities.
    """

    value_fn: Any = None  # Callable[[PrinterStatus], Any]
    present_fn: Any = None  # Callable[[PrinterStatus], bool], None = always


# The phase-2 suffix vocabulary (entity ids end in these keys) is fixed
# and documented in DESIGN.md; cards hard-code these suffixes.
PRINTER_SENSORS: tuple[PrintHandyPrinterSensorEntityDescription, ...] = (
    PrintHandyPrinterSensorEntityDescription(
        key="current_state",
        name="Current state",
        icon="mdi:printer-3d",
        device_class=SensorDeviceClass.ENUM,
        options=PRINT_STATES,
        value_fn=lambda status: status.state if status.state in PRINT_STATES else "unknown",
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="print_progress",
        name="Print progress",
        icon="mdi:progress-clock",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_percent,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="filename",
        name="Filename",
        icon="mdi:file",
        value_fn=lambda status: status.filename or None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="nozzle_temperature",
        name="Nozzle temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.hotend_c,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="bed_temperature",
        name="Bed temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.bed_c,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="nozzle_target_temperature",
        name="Nozzle target temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.hotend_target_c,
        present_fn=lambda status: status.hotend_target_c is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="bed_target_temperature",
        name="Bed target temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.bed_target_c,
        present_fn=lambda status: status.bed_target_c is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="estimated_finish_time",
        name="Estimated finish time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_finish_time,
        present_fn=lambda status: status.remaining_min is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="time_remaining",
        name="Time remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.remaining_min,
        present_fn=lambda status: status.remaining_min is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="current_layer",
        name="Current layer",
        icon="mdi:layers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.layer,
        present_fn=lambda status: status.layer is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="total_layers",
        name="Total layers",
        icon="mdi:layers-triple",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.total_layers,
        present_fn=lambda status: status.total_layers is not None,
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
        for printer in coordinator.data.printers:
            entities.append(
                PrintHandyPrinterConnectionSensor(coordinator, entry, printer)
            )
            status = coordinator.data.status(printer.printer_id)
            if status is None:
                continue
            entities.extend(
                PrintHandyPrinterStatusSensor(coordinator, entry, printer, description)
                for description in PRINTER_SENSORS
                if description.present_fn is None or description.present_fn(status)
            )
            if has_ams_filament(status):
                entities.append(
                    PrintHandyPrinterFilamentSensor(coordinator, entry, printer)
                )
            # One spool sensor per slot (#789), created only when the
            # first poll actually carries a spools view for this
            # printer — a Print Handy without the spools endpoint (or
            # with Spoolman unreachable there) yields the unavailable
            # view and gains no dead slot entities.
            view = coordinator.data.spool_view(printer.printer_id)
            if view is not None and view.available:
                entities.extend(
                    PrintHandyPrinterSlotSpoolSensor(
                        coordinator, entry, printer, row.slot
                    )
                    for row in view.slots
                )
    async_add_entities(entities)


class PrintHandyPrinterStatusSensor(PrintHandyPrinterEntity, SensorEntity):
    """One sensor reading a field of this printer's live status."""

    entity_description: PrintHandyPrinterSensorEntityDescription

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
        description: PrintHandyPrinterSensorEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.client.base_url}:printer:{printer.printer_id}:{description.key}"
        )

    @property
    def native_value(self) -> Any:
        """Return the status field."""
        status = self.status
        if status is None:
            return None
        return self.entity_description.value_fn(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The current-state sensor also carries filename and any detail."""
        if self.entity_description.key != "current_state":
            return {}
        status = self.status
        if status is None:
            return {}
        attrs: dict[str, Any] = {"filename": status.filename}
        if status.detail:
            attrs["detail"] = status.detail
        return attrs


class PrintHandyPrinterFilamentSensor(PrintHandyPrinterEntity, SensorEntity):
    """The active filament, from the printer's own AMS report (Bambu).

    Follows the grouped-data pattern from DESIGN.md's card survey: the
    state is the active tray's filament name, and the tray detail
    (color, material, remaining) rides as attributes. This is the
    printer-reported AMS state the status endpoint carries — Print
    Handy sends no Spoolman assignment over the endpoint, so no spool
    weight or Spoolman id is claimed here.
    """

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_name = "Filament"
        self._attr_icon = "mdi:printer-3d-nozzle"
        self._attr_unique_id = f"{coordinator.client.base_url}:printer:{printer.printer_id}:filament"

    @property
    def native_value(self) -> str | None:
        """The active tray's filament name, falling back to its material."""
        status = self.status
        if status is None:
            return None
        tray = active_ams_tray(status)
        if tray is None:
            return None
        return str(tray.get("name") or tray.get("material") or "") or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Active-tray detail plus the full tray list as grouped data."""
        status = self.status
        if status is None or status.ams is None:
            return {}
        attrs: dict[str, Any] = {}
        tray = active_ams_tray(status)
        if tray is not None:
            attrs.update(
                {
                    "material": tray.get("material"),
                    "color": tray.get("color"),
                    "remaining_pct": tray.get("remaining_pct"),
                    "slot": tray.get("slot"),
                    "unit": tray.get("unit"),
                }
            )
        attrs["trays"] = [
            {
                "unit": row.get("unit"),
                "slot": row.get("slot"),
                "present": row.get("present"),
                "material": row.get("material"),
                "name": row.get("name"),
                "color": row.get("color"),
                "remaining_pct": row.get("remaining_pct"),
                "active": row.get("active"),
            }
            for row in ams_trays(status)
        ]
        external = status.ams.get("external")
        attrs["external_spool"] = external if isinstance(external, dict) else None
        attrs["filament_backup"] = status.ams.get("filament_backup")
        return attrs


class PrintHandyPrinterSlotSpoolSensor(PrintHandyPrinterEntity, SensorEntity):
    """The spool assigned to one slot of one printer (#789).

    State is the assigned spool's name (no state when the slot is
    empty); material, color and remaining weight ride as attributes,
    following the grouped-data pattern of the AMS filament sensor.
    The data is the coordinator poll's spools view — Spoolman owns
    the spool records and Print Handy owns the slot assignment; this
    sensor stores nothing of its own.
    """

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
        slot: int,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._slot = slot
        self._attr_name = f"Slot {slot} spool"
        self._attr_icon = "mdi:tray-full"
        self._attr_unique_id = (
            f"{coordinator.client.base_url}:printer:{printer.printer_id}"
            f":slot_{slot}_spool"
        )

    @property
    def slot_row(self) -> SlotSpool | None:
        """The current slot row from the poll, or None if not reported."""
        view = self.spool_view
        return view.slot(self._slot) if view is not None else None

    @property
    def native_value(self) -> str | None:
        """The assigned spool's name, or None when the slot is empty."""
        row = self.slot_row
        if row is None or row.spool is None:
            return None
        return row.spool.name or None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Slot, spool id, material, color and remaining weight."""
        return slot_spool_attributes(self.spool_view, self._slot)


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
