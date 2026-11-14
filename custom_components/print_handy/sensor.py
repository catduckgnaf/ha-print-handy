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
from homeassistant.const import PERCENTAGE, UnitOfMass, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .const import FINISH_TOLERANCE_SECONDS
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import (
    PrintHandyEntity,
    PrintHandyPrinterEntity,
    async_add_entities_as_known,
    instance_unique_id,
    printer_unique_id,
)
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

# Print Handy's normalized print states. The current-state enum sensor
# passes these through. "unknown" is the documented fallback for any state
# string Print Handy sends that is not in this list (the raw value rides
# along as the ``raw_state`` attribute). A printer Print Handy cannot read
# (state "unavailable" in the status endpoint) is reported by Home
# Assistant's own unavailable state, never as a state value.
PRINT_STATES = [
    "standby",
    "printing",
    "paused",
    "complete",
    "error",
    "cancelled",
    "unknown",
]


@dataclass(frozen=True, kw_only=True)
class PrintHandySensorEntityDescription(SensorEntityDescription):
    """Class describing Print Handy instance sensors."""

    value_fn: Any = None  # Callable[[InstanceInfo], str | int | None]


INSTANCE_SENSORS: tuple[PrintHandySensorEntityDescription, ...] = (
    PrintHandySensorEntityDescription(
        key="status",
        translation_key="status",
        value_fn=lambda instance: "ok" if instance.ok else "problem",
    ),
    PrintHandySensorEntityDescription(
        key="version",
        translation_key="version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda instance: instance.version or None,
    ),
    PrintHandySensorEntityDescription(
        key="doctor_failures",
        translation_key="doctor_failures",
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


def stable_finish(
    previous: datetime | None,
    status: PrinterStatus,
    *,
    now: datetime | None = None,
    tolerance_seconds: int = FINISH_TOLERANCE_SECONDS,
) -> datetime | None:
    """Return the projected finish, held steady until it moves by more than the tolerance.

    now + remaining minutes drifts by the poll interval on every poll even
    when the print is on schedule, which would rewrite the state (and the
    recorder) every cycle. The previous projection is kept while the new one
    is within ``tolerance_seconds`` of it.
    """
    if status.remaining_min is None:
        return None
    projected = (now or datetime.now(UTC)) + timedelta(minutes=status.remaining_min)
    if previous is not None and abs((projected - previous).total_seconds()) <= tolerance_seconds:
        return previous
    return projected


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
        translation_key="current_state",
        device_class=SensorDeviceClass.ENUM,
        options=PRINT_STATES,
        value_fn=lambda status: status.state if status.state in PRINT_STATES else "unknown",
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="print_progress",
        translation_key="print_progress",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_percent,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="filename",
        translation_key="filename",
        value_fn=lambda status: status.filename or None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="nozzle_temperature",
        translation_key="nozzle_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.hotend_c,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="bed_temperature",
        translation_key="bed_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.bed_c,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="nozzle_target_temperature",
        translation_key="nozzle_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.hotend_target_c,
        present_fn=lambda status: status.hotend_target_c is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="bed_target_temperature",
        translation_key="bed_target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.bed_target_c,
        present_fn=lambda status: status.bed_target_c is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="estimated_finish_time",
        translation_key="estimated_finish_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_finish_time,
        present_fn=lambda status: status.remaining_min is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="time_remaining",
        translation_key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.remaining_min,
        present_fn=lambda status: status.remaining_min is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="current_layer",
        translation_key="current_layer",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.layer,
        present_fn=lambda status: status.layer is not None,
    ),
    PrintHandyPrinterSensorEntityDescription(
        key="total_layers",
        translation_key="total_layers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda status: status.total_layers,
        present_fn=lambda status: status.total_layers is not None,
    ),
    # Issue-#1 remainder (0.5.0): gated on object data like the
    # targets, but checked on every update, not just the first: object
    # data only exists while a print
    # runs, so a printer whose Print Handy reports no objects (or a
    # build that predates the object fields) gains no dead sensor,
    # and one that starts a print later gains the sensor then.
    PrintHandyPrinterSensorEntityDescription(
        key="current_object",
        translation_key="current_object",
        value_fn=lambda status: status.current_object,
        present_fn=lambda status: bool(status.objects or status.current_object),
    ),
)

async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy sensors from a config entry.

    Entities gated on data a printer only sometimes reports (targets,
    layers, remaining time, objects, an AMS, spools) are added the first
    time an update carries that data, not just at setup, so a printer that
    was offline at startup, a print that starts later or a printer added to
    the roster gains them without a reload.
    """
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data

    def build() -> list[SensorEntity]:
        entities: list[SensorEntity] = [
            PrintHandyInstanceSensor(coordinator, entry, description)
            for description in INSTANCE_SENSORS
        ]
        if coordinator.data is None:
            return entities
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
            if status.hms_errors is not None or status.print_error_text:
                entities.append(
                    PrintHandyPrinterFaultSensor(coordinator, entry, printer)
                )
            # One spool sensor per slot (#789), created once the poll
            # carries a spools view for this printer - a Print Handy
            # without the spools endpoint (or with Spoolman unreachable
            # there) yields the unavailable view and gains no dead slot
            # entities.
            view = coordinator.data.spool_view(printer.printer_id)
            if view is not None and view.available:
                entities.extend(
                    PrintHandyPrinterSlotSpoolSensor(
                        coordinator, entry, printer, row.slot
                    )
                    for row in view.slots
                )
                entities.extend(
                    PrintHandyPrinterSlotWeightSensor(
                        coordinator, entry, printer, row.slot
                    )
                    for row in view.slots
                    if row.spool is not None
                    and row.spool.remaining_weight is not None
                )
        return entities

    async_add_entities_as_known(coordinator, entry, async_add_entities, build)


class PrintHandyPrinterStatusSensor(PrintHandyPrinterEntity, SensorEntity):
    """One sensor reading a field of this printer's live status."""

    entity_description: PrintHandyPrinterSensorEntityDescription
    _requires_live_status = True

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
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, description.key
        )
        self._finish: datetime | None = None

    @property
    def native_value(self) -> Any:
        """Return the status field."""
        status = self.status
        if status is None:
            return None
        if self.entity_description.key == "estimated_finish_time":
            self._finish = stable_finish(self._finish, status)
            return self._finish
        return self.entity_description.value_fn(status)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Grouped attributes for the sensors that carry them.

        The current-state sensor carries filename and any detail; the
        current-object sensor carries the full object list and the
        excluded list, so cards and the exclude_object service's
        callers can see every object's name, not just the current one.
        """
        status = self.status
        if status is None:
            return {}
        if self.entity_description.key == "current_object":
            return {
                "objects": list(status.objects),
                "excluded_objects": list(status.excluded_objects),
            }
        if self.entity_description.key != "current_state":
            return {}
        attrs: dict[str, Any] = {"filename": status.filename}
        if status.state not in PRINT_STATES:
            attrs["raw_state"] = status.state
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

    _attr_translation_key = "filament"
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
            coordinator, printer.printer_id, "filament"
        )

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


class PrintHandyPrinterFaultSensor(PrintHandyPrinterEntity, SensorEntity):
    """Bambu faults (HMS) as a count sensor (issue #14).

    The shape DESIGN.md's card survey prescribes for an error sensor:
    the state is the fault count and the codes and descriptions ride
    as attributes, with the printer's own error text alongside. Only
    printers whose live status carries fault data gain one — the
    Bambu adapter always sends ``hms_errors``; other families omit
    the key and the model keeps it None.
    """

    _attr_translation_key = "faults"
    _attr_state_class = SensorStateClass.MEASUREMENT
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
            coordinator, printer.printer_id, "faults"
        )

    @property
    def native_value(self) -> int | None:
        """The number of active HMS faults; None before the first poll."""
        status = self.status
        if status is None or status.hms_errors is None:
            return None
        return len(status.hms_errors)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Fault codes, their descriptions, and the printer's error text."""
        status = self.status
        if status is None or status.hms_errors is None:
            return {}
        return {
            "codes": [error.code for error in status.hms_errors],
            "descriptions": [error.description for error in status.hms_errors],
            "print_error": status.print_error_text or None,
        }


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
        self._attr_translation_key = "slot_spool"
        self._attr_translation_placeholders = {"slot": str(slot)}
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, f"slot_{slot}_spool"
        )

    @property
    def available(self) -> bool:
        """Unavailable when the spools poll itself failed for this printer.

        The coordinator substitutes an explicit unavailable view when a
        spool read fails; without this override the sensor would report
        the same empty state as a genuinely unassigned slot, and a
        Spoolman outage would look like every slot was cleared.
        """
        view = self.spool_view
        return super().available and view is not None and view.available

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


class PrintHandyPrinterSlotWeightSensor(PrintHandyPrinterEntity, SensorEntity):
    """The remaining weight of the spool in one slot, in grams (#14).

    A metric companion to the slot spool sensor (whose state is the
    spool's name): this one exists so automations and history can use
    the number itself. Created only for a slot whose spool records a
    remaining weight; the value is Spoolman's recorded number, never
    estimated.
    """

    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_translation_key = "slot_spool_weight"

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
        self._attr_translation_placeholders = {"slot": str(slot)}
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, f"slot_{slot}_spool_weight"
        )

    @property
    def available(self) -> bool:
        """Unavailable when the spools poll itself failed for this printer."""
        view = self.spool_view
        return super().available and view is not None and view.available

    @property
    def native_value(self) -> float | None:
        """The assigned spool's recorded remaining weight, or None."""
        view = self.spool_view
        row = view.slot(self._slot) if view is not None else None
        if row is None or row.spool is None:
            return None
        return row.spool.remaining_weight


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
        self._attr_unique_id = instance_unique_id(coordinator, description.key)

    @property
    def native_value(self) -> str | int | None:
        """Return the snapshot field."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data.instance)


class PrintHandyPrinterConnectionSensor(PrintHandyPrinterEntity, SensorEntity):
    """Diagnostic sensor: how Print Handy reaches this printer."""

    _attr_translation_key = "connection"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, "connection"
        )

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
