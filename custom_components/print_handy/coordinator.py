"""DataUpdateCoordinator for Print Handy."""

from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    PrintHandyApiClient,
    PrintHandyApiError,
    PrintHandyAuthError,
    PrintHandyConnectionError,
)
from .const import CONF_SCAN_INTERVAL, CONF_TOKEN, DEFAULT_SCAN_INTERVAL, DOMAIN
from .model import Snapshot

LOGGER = logging.getLogger(__name__)


class PrintHandyDataUpdateCoordinator(DataUpdateCoordinator[Snapshot]):
    """Poll Print Handy's status API for the whole instance.

    One coordinator per config entry: /api/status covers the instance
    and roster, and each roster printer's live status is then polled
    from /api/printers/<id>/status into the same snapshot, so all
    devices read from this single update. A printer whose status poll
    fails carries its unavailable status instead of failing the update.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.config_entry = entry
        interval = int(
            entry.options.get(CONF_SCAN_INTERVAL)
            or entry.data.get(CONF_SCAN_INTERVAL)
            or DEFAULT_SCAN_INTERVAL
        )
        session = async_get_clientsession(hass)
        self.client = PrintHandyApiClient(
            session=session,
            base_url=str(entry.data.get(CONF_URL, "")),
            token=str(entry.data.get(CONF_TOKEN, "")),
        )
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN} ({self.client.base_url})",
            update_interval=timedelta(seconds=interval),
        )

    async def _async_update_data(self) -> Snapshot:
        """Fetch the latest snapshot plus every printer's live status."""
        try:
            snapshot = await self.client.async_get_status()
            statuses = await self.client.async_get_printer_statuses(
                [printer.printer_id for printer in snapshot.printers]
            )
        except PrintHandyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PrintHandyConnectionError as err:
            raise UpdateFailed(str(err)) from err
        return dataclasses.replace(snapshot, statuses=statuses)

    async def async_control_printer(self, printer_id: str, action: str) -> None:
        """Send a pause / resume / cancel action to one printer.

        Any failure — controls refused on the Print Handy side, an
        unreachable Web UI, a rejected token — is raised as a
        HomeAssistantError so the button press fails visibly in the UI
        and in automations, never silently. On success the coordinator
        refreshes so state sensors reflect the action promptly.
        """
        try:
            await self.client.async_printer_control(printer_id, action)
        except PrintHandyApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()

    async def async_set_printer_target(
        self,
        printer_id: str,
        *,
        hotend_c: float | None = None,
        bed_c: float | None = None,
    ) -> None:
        """Set target temperatures on one printer, failing visibly.

        Same failure contract as async_control_printer: refusals and
        transport failures become a HomeAssistantError on the number's
        set-value call; success triggers a refresh.
        """
        try:
            await self.client.async_set_printer_target(
                printer_id, hotend_c=hotend_c, bed_c=bed_c
            )
        except PrintHandyApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()
