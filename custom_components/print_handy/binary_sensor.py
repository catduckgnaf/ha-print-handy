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
from .const import LOW_FILAMENT_GRAMS, LOW_FILAMENT_PERCENT
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import (
    PrintHandyEntity,
    PrintHandyPrinterEntity,
    async_add_entities_as_known,
    instance_unique_id,
    printer_unique_id,
)
from .model import DoctorCheck, PrinterInfo

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PrintHandyBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Class describing Print Handy instance binary sensors."""

    value_fn: Any = None  # Callable[[InstanceInfo], bool | None]


INSTANCE_BINARY_SENSORS: tuple[PrintHandyBinarySensorEntityDescription, ...] = (
    PrintHandyBinarySensorEntityDescription(
        key="ready",
        translation_key="ready",
        value_fn=lambda instance: instance.ok,
    ),
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
            for check in coordinator.data.instance.doctor_checks:
                entities.append(
                    PrintHandyDoctorCheckBinarySensor(coordinator, entry, check)
                )
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
                view = coordinator.data.spool_view(printer.printer_id)
                if view is not None and view.available:
                    entities.extend(
                        PrintHandyPrinterLowFilamentBinarySensor(
                            coordinator, entry, printer, row.slot
                        )
                        for row in view.slots
                        if row.spool is not None
                        and (
                            row.spool.remaining_percent is not None
                            or row.spool.remaining_weight is not None
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


class PrintHandyDoctorCheckBinarySensor(PrintHandyEntity, BinarySensorEntity):
    """One doctor check as a problem sensor: on while the check fails.

    The doctor rows ride in the hub snapshot, one sensor per check
    label; a warning is not "on" (the problem class is for failures)
    and stays visible in the status attribute next to the detail.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "doctor_check"

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        check: DoctorCheck,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry)
        self._slug = check.slug
        self._attr_translation_placeholders = {"check": check.label}
        self._attr_unique_id = instance_unique_id(
            coordinator, f"doctor:{self._slug}"
        )

    @property
    def check(self) -> DoctorCheck | None:
        """The current row for this check, or None if it left the snapshot."""
        if self.coordinator.data is None:
            return None
        for check in self.coordinator.data.instance.doctor_checks:
            if check.slug == self._slug:
                return check
        return None

    @property
    def is_on(self) -> bool | None:
        """True while this check fails; None before the first snapshot."""
        check = self.check
        return check.status == "fail" if check else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The check's raw status and the server's detail text."""
        check = self.check
        if check is None:
            return {}
        return {"status": check.status, "detail": check.detail}


class PrintHandyPrinterLowFilamentBinarySensor(
    PrintHandyPrinterEntity, BinarySensorEntity
):
    """On while one slot's spool is running low (issue #14).

    Low means the recorded remaining percent is at or below
    LOW_FILAMENT_PERCENT when the spool's starting weight is known;
    where only grams are recorded, at or below LOW_FILAMENT_GRAMS.
    Percent is never estimated from grams here either — the two
    readings are kept separate, as in the spool model.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_translation_key = "low_filament"

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
            coordinator, printer.printer_id, f"slot_{slot}_low_filament"
        )

    @property
    def available(self) -> bool:
        """Unavailable when the spools poll itself failed for this printer."""
        view = self.spool_view
        return super().available and view is not None and view.available

    @property
    def is_on(self) -> bool | None:
        """True while the slot's spool is at or below the low bound."""
        view = self.spool_view
        row = view.slot(self._slot) if view is not None else None
        if row is None or row.spool is None:
            return None
        spool = row.spool
        if spool.remaining_percent is not None:
            return spool.remaining_percent <= LOW_FILAMENT_PERCENT
        if spool.remaining_weight is not None:
            return spool.remaining_weight <= LOW_FILAMENT_GRAMS
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The spool's recorded remaining percent and weight."""
        view = self.spool_view
        row = view.slot(self._slot) if view is not None else None
        if row is None or row.spool is None:
            return {}
        return {
            "spool": row.spool.name or None,
            "remaining_percent": row.spool.remaining_percent,
            "remaining_weight": row.spool.remaining_weight,
        }


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
