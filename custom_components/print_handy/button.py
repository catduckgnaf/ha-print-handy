"""Buttons for Print Handy: per-printer print controls (phase 3).

One Pause / Resume / Cancel button per roster printer, posting to
Print Handy's control API (Print Handy #708) through the coordinator.
The buttons exist for every printer — pause / resume / cancel exist
for every printer family on the Print Handy side — but the control
endpoints are gated by an operator setting there and are off by
default: a press against a server with controls disabled fails
visibly with Print Handy's refusal, never silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import PrintHandyPrinterEntity
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PrintHandyButtonEntityDescription(ButtonEntityDescription):
    """Class describing Print Handy printer control buttons.

    ``key`` is both the fixed entity-id suffix (``pause``, ``resume``,
    ``cancel`` — documented in DESIGN.md) and the control action the
    client posts.
    """


# The phase-3 button suffix vocabulary is fixed and documented in
# DESIGN.md, alongside the phase-2 sensor suffixes.
PRINTER_BUTTONS: tuple[PrintHandyButtonEntityDescription, ...] = (
    PrintHandyButtonEntityDescription(
        key="pause",
        name="Pause",
        icon="mdi:pause",
    ),
    PrintHandyButtonEntityDescription(
        key="resume",
        name="Resume",
        icon="mdi:play",
    ),
    PrintHandyButtonEntityDescription(
        key="cancel",
        name="Cancel",
        icon="mdi:cancel",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy buttons from a config entry."""
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = []
    if coordinator.data is not None:
        for printer in coordinator.data.printers:
            entities.extend(
                PrintHandyPrinterButton(coordinator, entry, printer, description)
                for description in PRINTER_BUTTONS
            )
    async_add_entities(entities)


class PrintHandyPrinterButton(PrintHandyPrinterEntity, ButtonEntity):
    """One print-control button for one printer."""

    entity_description: PrintHandyButtonEntityDescription

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
        description: PrintHandyButtonEntityDescription,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.client.base_url}:printer:{printer.printer_id}:{description.key}"
        )

    async def async_press(self) -> None:
        """Send this button's control action to this printer only.

        Failures (controls refused, Web UI unreachable, bad token)
        propagate as HomeAssistantError from the coordinator, so the
        press fails visibly rather than appearing to succeed.
        """
        await self.coordinator.async_control_printer(
            self._printer_id, self.entity_description.key
        )
