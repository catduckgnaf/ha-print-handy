"""Buttons for Print Handy: per-printer print controls.

Pause, Resume and Cancel exist for every printer family on the Print
Handy side, so every roster printer gets them. Emergency stop, Firmware
restart and Moonraker restart are supported by Print Handy only for
printers it reaches through Moonraker (every other family answers 501),
so they are created only for printers whose connection is Moonraker.

Home Assistant buttons have no confirmation step, so the buttons that can
cost a print or interrupt the printer (Cancel, Emergency stop and the two
restarts) are disabled in the entity registry until the user turns them
on; the restarts also sit in the configuration category. Entities a user
already enabled stay enabled: the default only applies when an entity is
first registered.

All of them post to Print Handy's control API (Print Handy #708), which
is gated by an operator setting on the Print Handy side and off by
default: a press against a server with controls disabled fails visibly
with Print Handy's refusal, never silently.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .const import DOMAIN, MOONRAKER_CONNECTION, MOONRAKER_ONLY_ACTIONS
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import (
    PrintHandyPrinterEntity,
    async_add_entities_as_known,
    printer_unique_id,
)
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class PrintHandyButtonEntityDescription(ButtonEntityDescription):
    """Class describing Print Handy printer control buttons.

    ``key`` is both the entity-id suffix (``pause``, ``resume``,
    ``cancel`` - documented in DESIGN.md) and the control action the
    client posts.
    """


# The button suffix vocabulary is documented in DESIGN.md, alongside the
# sensor suffixes. ``server_restart`` keeps its key (and so its unique id)
# although the button restarts Moonraker, not Print Handy.
PRINTER_BUTTONS: tuple[PrintHandyButtonEntityDescription, ...] = (
    PrintHandyButtonEntityDescription(key="pause", translation_key="pause"),
    PrintHandyButtonEntityDescription(key="resume", translation_key="resume"),
    PrintHandyButtonEntityDescription(
        key="cancel",
        translation_key="cancel",
        entity_registry_enabled_default=False,
    ),
    PrintHandyButtonEntityDescription(
        key="emergency_stop",
        translation_key="emergency_stop",
        entity_registry_enabled_default=False,
    ),
    PrintHandyButtonEntityDescription(
        key="firmware_restart",
        translation_key="firmware_restart",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    PrintHandyButtonEntityDescription(
        key="server_restart",
        translation_key="server_restart",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


def buttons_for(printer: PrinterInfo) -> tuple[PrintHandyButtonEntityDescription, ...]:
    """Return the buttons Print Handy supports for this printer's connection type."""
    if printer.connection == MOONRAKER_CONNECTION:
        return PRINTER_BUTTONS
    return tuple(
        description
        for description in PRINTER_BUTTONS
        if description.key not in MOONRAKER_ONLY_ACTIONS
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy buttons from a config entry."""
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data
    _remove_unsupported_buttons(hass, coordinator)

    def build() -> list[ButtonEntity]:
        entities: list[ButtonEntity] = []
        if coordinator.data is not None:
            for printer in coordinator.data.printers:
                entities.extend(
                    PrintHandyPrinterButton(coordinator, entry, printer, description)
                    for description in buttons_for(printer)
                )
        return entities

    async_add_entities_as_known(coordinator, entry, async_add_entities, build)


def _remove_unsupported_buttons(
    hass: HomeAssistant, coordinator: PrintHandyDataUpdateCoordinator
) -> None:
    """Drop Moonraker-only buttons 0.5.x created for other printer families.

    Those buttons could only ever fail with a 501, so leaving them in the
    registry would keep dead controls on the dashboard.
    """
    if coordinator.data is None:
        return
    registry = er.async_get(hass)
    for printer in coordinator.data.printers:
        if printer.connection == MOONRAKER_CONNECTION or not printer.connection:
            continue
        for action in MOONRAKER_ONLY_ACTIONS:
            entity_id = registry.async_get_entity_id(
                "button",
                DOMAIN,
                printer_unique_id(coordinator, printer.printer_id, action),
            )
            if entity_id is not None:
                registry.async_remove(entity_id)


class PrintHandyPrinterButton(PrintHandyPrinterEntity, ButtonEntity):
    """One print-control button for one printer."""

    entity_description: PrintHandyButtonEntityDescription

    _requires_controls = True

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
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, description.key
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
