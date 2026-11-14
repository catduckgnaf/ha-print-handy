"""Camera for Print Handy: one still per printer, through the server proxy.

Print Handy serves ``GET /api/printers/<id>/camera/snapshot`` — one
JPEG from the printer's own camera, fetched on the server side
(#708). The entity is created only where the roster carries a
``camera_snapshot_url`` for the printer, the design's condition
(DESIGN.md): the roster is the integration's signal that a camera is
configured there. Adapter-only cameras the roster does not signal
(Bambu's built-in camera, reached by the server through its adapter)
gain no entity until the roster carries a camera field for them —
that is a server-side gap, recorded in issue #14.

The image is fetched through the integration's API client on demand,
never polled by the entity itself; Home Assistant asks when a card or
a snapshot service call needs a still. A failed fetch (no camera,
unreachable, a refused token) returns None, which Home Assistant
shows as no image, instead of raising into the camera platform.
"""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import PrintHandyConfigEntry
from .api import PrintHandyApiError
from .coordinator import PrintHandyDataUpdateCoordinator
from .entity import PrintHandyPrinterEntity, async_add_entities_as_known, printer_unique_id
from .model import PrinterInfo

LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Print Handy cameras from a config entry."""
    coordinator: PrintHandyDataUpdateCoordinator = entry.runtime_data

    def build() -> list[Camera]:
        if coordinator.data is None:
            return []
        return [
            PrintHandyPrinterCamera(coordinator, entry, printer)
            for printer in coordinator.data.printers
            if printer.has_camera
        ]

    async_add_entities_as_known(coordinator, entry, async_add_entities, build)


class PrintHandyPrinterCamera(PrintHandyPrinterEntity, Camera):
    """The still camera of one printer, proxied by Print Handy."""

    _attr_translation_key = "camera"

    def __init__(
        self,
        coordinator: PrintHandyDataUpdateCoordinator,
        entry: PrintHandyConfigEntry,
        printer: PrinterInfo,
    ) -> None:
        """Initialize."""
        super().__init__(coordinator, entry, printer)
        # Camera's own __init__ sets up its WebRTC/stream state; the
        # coordinator-entity chain never reaches it through super().
        Camera.__init__(self)
        self._attr_unique_id = printer_unique_id(
            coordinator, printer.printer_id, "camera"
        )

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return one JPEG from the server's snapshot proxy, or None."""
        try:
            return await self.coordinator.client.async_get_camera_snapshot(
                self._printer_id
            )
        except PrintHandyApiError as err:
            LOGGER.debug(
                "Camera snapshot for printer %s failed: %s", self._printer_id, err
            )
            return None
