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
    The same poll also fetches each printer's spools view from
    /api/printers/<id>/spools (#789) into the snapshot — no second
    poller, and no spool state is stored in the integration beyond
    this poll.
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
            printer_ids = [printer.printer_id for printer in snapshot.printers]
            statuses = await self.client.async_get_printer_statuses(printer_ids)
            spools = await self.client.async_get_printer_spool_views(printer_ids)
        except PrintHandyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PrintHandyConnectionError as err:
            raise UpdateFailed(str(err)) from err
        return dataclasses.replace(snapshot, statuses=statuses, spools=spools)

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

    async def _async_spool_call(self, printer_id: str, coro) -> dict:
        """Run one spool action, failing visibly, then refresh.

        Same failure contract as the control methods: any API failure
        — a named spool refusal (controls off, unknown spool or slot,
        a conflict), an unreachable Web UI, a rejected token — becomes
        a HomeAssistantError carrying the API's reason, so a service
        call never appears to succeed when Print Handy refused it.
        """
        try:
            result = await coro
        except PrintHandyApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()
        return result if isinstance(result, dict) else {}

    async def async_assign_spool(
        self, printer_id: str, slot: int, spool_id: int
    ) -> dict:
        """Assign one spool to one slot of one printer (#789)."""
        return await self._async_spool_call(
            printer_id, self.client.async_assign_spool(printer_id, slot, spool_id)
        )

    async def async_swap_spools(
        self, printer_id: str, slot_a: int, slot_b: int
    ) -> dict:
        """Swap the spools in two slots of one printer (#789)."""
        return await self._async_spool_call(
            printer_id, self.client.async_swap_spools(printer_id, slot_a, slot_b)
        )

    async def async_unassign_spool(self, printer_id: str, slot: int) -> dict:
        """Unassign one slot of one printer; the spool goes on the shelf."""
        return await self._async_spool_call(
            printer_id, self.client.async_unassign_spool(printer_id, slot)
        )

    async def async_add_spool(
        self,
        printer_id: str,
        *,
        material: str,
        color: str,
        grams: float,
        brand: str | None = None,
        hex: str | None = None,  # noqa: A002 - the API's own field name
    ) -> dict:
        """Create one spool on the shelf through the API's add action."""
        return await self._async_spool_call(
            printer_id,
            self.client.async_add_spool(
                printer_id,
                material=material,
                color=color,
                grams=grams,
                brand=brand,
                hex=hex,
            ),
        )

    async def async_sync_spools(self, printer_id: str) -> dict:
        """Reconcile one printer's slot state with Spoolman (#789)."""
        return await self._async_spool_call(
            printer_id, self.client.async_sync_spools(printer_id)
        )
