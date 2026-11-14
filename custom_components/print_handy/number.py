"""Numbers for Print Handy: writable target temperatures (phase 3).

One number per heater whose target the printer's adapter reports —
the same creation gating as the phase-2 target sensors: a printer
whose status carries no ``hotend_target_c`` / ``bed_target_c`` (the
Moonraker/ZMod family, among others) gains no number for that heater,
so no permanently-dead control is ever created.

The phase-2 read-only target sensors are unchanged and coexist with
these numbers: the sensors keep the measurement history cards bind
to, and the numbers are the writable control. Both end in the same
``nozzle_target_temperature`` / ``bed_target_temperature`` suffix
under their own domains (``sensor.`` / ``number.``), so the fixed
suffix vocabulary in DESIGN.md is untouched.

Setting a value posts both heaters' targets to Print Handy's control
API (Print Handy #708): the heater being set carries the new value,
the other heater carries its current target, so a JSON null is only
sent for a heater whose target is genuinely unknown. The maximum is
the server's own per-heater ceiling where it sends one (#1400), else
a wide fallback; Print Handy and the printer remain the authority and
refuse out-of-range values, which surfaces as a visible failure on
the set call. The numbers are unavailable while the server reports
its controls off.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
)
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .const import DEFAULT_HEATER_MAX
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import (
    PrintHandyPrinterEntity,
    async_add_entities_as_known,
    printer_unique_id,
)
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)

# The server's name for each heater in ``temperature_limits_c``.
_SERVER_HEATER = {"hotend": "nozzle", "bed": "bed"}


@dataclass(frozen=True, kw_only=True)
class PrintHandyNumberEntityDescription(NumberEntityDescription):
    """Class describing Print Handy target-temperature numbers.

    ``heater`` is "hotend" or "bed" and selects the status field the
    number reads (``<heater>_target_c``) and the target it writes.
    ``present_fn`` gates creation at setup, mirroring the phase-2
    sensors: only when the first poll actually carries the target.
    """

    heater: str = ""
    present_fn: Any = None  # Callable[[PrinterStatus], bool]


PRINTER_NUMBERS: tuple[PrintHandyNumberEntityDescription, ...] = (
    PrintHandyNumberEntityDescription(
        key="nozzle_target_temperature",
        translation_key="nozzle_target_temperature",
        heater="hotend",
        present_fn=lambda status: status.hotend_target_c is not None,
    ),
    PrintHandyNumberEntityDescription(
        key="bed_target_temperature",
        translation_key="bed_target_temperature",
        heater="bed",
        present_fn=lambda status: status.bed_target_c is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy numbers from a config entry."""
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data

    def build() -> list[NumberEntity]:
        entities: list[NumberEntity] = []
        if coordinator.data is not None:
            for printer in coordinator.data.printers:
                status = coordinator.data.status(printer.printer_id)
                if status is None:
                    continue
                entities.extend(
                    PrintHandyPrinterTargetNumber(
                        coordinator, entry, printer, description
                    )
                    for description in PRINTER_NUMBERS
                    if description.present_fn is None or description.present_fn(status)
                )
        return entities

    async_add_entities_as_known(coordinator, entry, async_add_entities, build)


class PrintHandyPrinterTargetNumber(PrintHandyPrinterEntity, NumberEntity):
    """A writable target temperature for one heater of one printer."""

    entity_description: PrintHandyNumberEntityDescription

    _requires_live_status = True
    _requires_controls = True
    _attr_native_min_value = 0.0
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
        description: PrintHandyNumberEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self.entity_description = description
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, f"number:{description.key}"
        )

    @property
    def native_max_value(self) -> float:
        """The server's own ceiling for this heater, else a wide fallback.

        Print Handy (#1400) sends the per-heater ceiling its control
        routes refuse above, so the slider stops exactly where the
        server would. An older server sends none; the fallback is wider
        than any current printer and an over-range set fails visibly.
        """
        heater = _SERVER_HEATER[self.entity_description.heater]
        printer = self.printer
        if printer is not None and heater in printer.temperature_limits:
            return printer.temperature_limits[heater]
        return DEFAULT_HEATER_MAX[heater]

    @property
    def native_value(self) -> float | None:
        """Return this heater's current target, as last reported."""
        status = self.status
        if status is None:
            return None
        return getattr(status, f"{self.entity_description.heater}_target_c")

    async def async_set_native_value(self, value: float) -> None:
        """Set this heater's target on this printer only.

        The other heater's current target rides along so the API's
        null slot is only used for a heater whose target is unknown.
        Failures (controls refused, Web UI unreachable, bad token)
        propagate as HomeAssistantError from the coordinator, so the
        set fails visibly rather than appearing to succeed.
        """
        status = self.status
        hotend_c = status.hotend_target_c if status is not None else None
        bed_c = status.bed_target_c if status is not None else None
        if self.entity_description.heater == "hotend":
            hotend_c = float(value)
        else:
            bed_c = float(value)
        await self.coordinator.async_set_printer_target(
            self._printer_id, hotend_c=hotend_c, bed_c=bed_c
        )
