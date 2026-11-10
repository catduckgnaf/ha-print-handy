"""DataUpdateCoordinator for Print Handy."""

from __future__ import annotations

import dataclasses
import logging
import time
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
    PrintHandyHostRefusedError,
    PrintHandyRateLimitedError,
)
from .const import (
    CONF_HUB_SCAN_INTERVAL,
    CONF_INSTANCE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_HUB_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SPOOL_INTERVAL,
    DOMAIN,
    EVENT_PRINT_FINISHED,
    HUB_RETRY_INTERVAL,
)
from .model import PrinterSpools, Snapshot

LOGGER = logging.getLogger(__name__)


def _monotonic() -> float:
    """Return the clock the cadences run on (patched in tests)."""
    return time.monotonic()


def _option(entry: ConfigEntry, key: str, default: int) -> int:
    """One integer option, falling back to the entry data and then the default."""
    return int(entry.options.get(key) or entry.data.get(key) or default)


class PrintHandyDataUpdateCoordinator(DataUpdateCoordinator[Snapshot]):
    """Poll Print Handy for the whole instance, each part at its own cadence.

    One coordinator per config entry, ticking on the fast per-printer
    interval. ``GET /api/status`` (roster, versions, service health and the
    doctor rows) makes the server run its whole self-diagnosis, so it is
    fetched on a slow interval (and once at startup and on reload) and the
    last good copy is reused between fetches. Each roster printer's live
    status from ``/api/printers/<id>/status`` is cheap and is read on every
    tick. The spools views (a Spoolman read per printer) are read on their
    own slow interval and right after a spool service call.

    A failed hub refresh never fails the update while the per-printer
    reads succeed: the previous roster stays, ``hub_error`` records why and
    only the instance-level entities go unavailable. A printer whose status
    read fails carries its unavailable status instead of failing the
    update. A 429 backs the whole entry off for the server's Retry-After.
    """

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        interval = _option(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self.hub_interval = _option(
            entry, CONF_HUB_SCAN_INTERVAL, DEFAULT_HUB_SCAN_INTERVAL
        )
        self.instance_key = str(entry.data.get(CONF_INSTANCE_ID) or entry.entry_id)
        session = async_get_clientsession(hass)
        self.client = PrintHandyApiClient(
            session=session,
            base_url=str(entry.data.get(CONF_URL, "")),
            token=str(entry.data.get(CONF_TOKEN, "")),
        )
        self.hub_error: str | None = None
        self._hub_fetched: float | None = None
        self._spools_fetched: float | None = None
        self._force_spools = False
        self._backoff_until = 0.0
        super().__init__(
            hass,
            LOGGER,
            name=f"{DOMAIN} ({self.client.base_url})",
            config_entry=entry,
            update_interval=timedelta(seconds=interval),
        )

    @property
    def hub_available(self) -> bool:
        """True while the last hub refresh succeeded."""
        return self.hub_error is None

    async def _async_update_data(self) -> Snapshot:
        """Fetch what is due: hub, every printer's live status, spools."""
        now = _monotonic()
        if now < self._backoff_until:
            raise UpdateFailed(
                "Print Handy is rate-limiting this client after failed access "
                f"attempts; waiting {int(self._backoff_until - now) + 1} more "
                "seconds before asking again"
            )
        try:
            hub = await self._async_hub(now)
            printer_ids = [printer.printer_id for printer in hub.printers]
            statuses = await self.client.async_get_printer_statuses(printer_ids)
            spools = await self._async_spools(now, printer_ids)
        except PrintHandyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PrintHandyRateLimitedError as err:
            self._backoff_until = now + err.retry_after
            raise UpdateFailed(str(err)) from err
        except PrintHandyHostRefusedError as err:
            raise UpdateFailed(str(err)) from err
        except PrintHandyApiError as err:
            raise UpdateFailed(str(err)) from err
        snapshot = dataclasses.replace(hub, statuses=statuses, spools=spools)
        self._fire_print_finished_events(snapshot)
        return snapshot

    def _fire_print_finished_events(self, snapshot: Snapshot) -> None:
        """Fire EVENT_PRINT_FINISHED for prints that just completed.

        A completion is a live-state move from an active print state
        ("printing" or "paused") to "complete" between two polls; the
        first poll never fires (there is no previous state to move
        from), and staying "complete" fires only on the transition.
        The event is the automation hook issue #14 asked for: printer
        id and display name, the finished file, and this entry's id.
        """
        previous = self.data
        if previous is None:
            return
        for printer_id, status in snapshot.statuses.items():
            if status.state != "complete":
                continue
            before = previous.statuses.get(printer_id)
            if before is None or before.state not in ("printing", "paused"):
                continue
            printer = snapshot.printer(printer_id)
            self.hass.bus.async_fire(
                EVENT_PRINT_FINISHED,
                {
                    "entry_id": self.config_entry.entry_id,
                    "printer_id": printer_id,
                    "printer_name": printer.display_name if printer else printer_id,
                    "filename": status.filename,
                },
            )

    async def _async_hub(self, now: float) -> Snapshot:
        """Return the hub snapshot: fresh when due, else the last good one.

        A failed refresh keeps the previous roster and retries after
        HUB_RETRY_INTERVAL; with no previous roster there is nothing to
        keep, so the failure propagates. Auth, rate-limit and refused-host
        errors always propagate: they concern every request.
        """
        previous = self.data
        due = (
            previous is None
            or self._hub_fetched is None
            or now - self._hub_fetched >= self.hub_interval
        )
        if not due:
            return previous
        try:
            snapshot = await self.client.async_get_status()
        except (
            PrintHandyAuthError,
            PrintHandyRateLimitedError,
            PrintHandyHostRefusedError,
        ):
            raise
        except PrintHandyApiError as err:
            if previous is None:
                raise
            if self.hub_error is None:
                LOGGER.warning(
                    "Print Handy status refresh failed, keeping the last roster "
                    "and retrying shortly: %s",
                    err,
                )
            self.hub_error = str(err)
            self._hub_fetched = now - self.hub_interval + HUB_RETRY_INTERVAL
            return previous
        if self.hub_error is not None:
            LOGGER.info("Print Handy status refresh recovered")
        self.hub_error = None
        self._hub_fetched = now
        return snapshot

    async def _async_spools(
        self, now: float, printer_ids: list[str]
    ) -> dict[str, PrinterSpools]:
        """Return the spools views: re-read when due or forced, else the last ones."""
        previous = self.data.spools if self.data is not None else {}
        due = (
            self._force_spools
            or self._spools_fetched is None
            or now - self._spools_fetched >= DEFAULT_SPOOL_INTERVAL
            or any(printer_id not in previous for printer_id in printer_ids)
        )
        if not due:
            return {
                printer_id: previous[printer_id]
                for printer_id in printer_ids
                if printer_id in previous
            }
        views = await self.client.async_get_printer_spool_views(printer_ids)
        self._force_spools = False
        self._spools_fetched = now
        return views

    async def async_control_printer(self, printer_id: str, action: str) -> None:
        """Send a button control action to one printer.

        Pause / resume / cancel, plus the 0.5.0 emergency stop and
        firmware / server restart buttons — the client owns the
        per-action path and confirm body.

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

    async def async_send_gcode(self, printer_id: str, script: str) -> None:
        """Send a G-code script to one printer, failing visibly.

        Same failure contract as async_control_printer: a refusal from
        Print Handy (controls disabled, unsupported family, a rejected
        script), an unreachable Web UI or a rejected token becomes a
        HomeAssistantError on the service call carrying the server's
        reason; success triggers a refresh.
        """
        try:
            await self.client.async_send_gcode(printer_id, script)
        except PrintHandyApiError as err:
            raise HomeAssistantError(str(err)) from err
        await self.async_request_refresh()

    async def async_exclude_object(self, printer_id: str, object_name: str) -> None:
        """Exclude one object on one printer, failing visibly.

        Same failure contract as async_control_printer — in
        particular the server's named 409 reasons (unknown object,
        and the last remaining object, where the reason says to use
        cancel instead) reach the service caller verbatim in the
        HomeAssistantError; success triggers a refresh.
        """
        try:
            await self.client.async_exclude_object(printer_id, object_name)
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
        # A spool write changes what the spools view says: re-read it now
        # instead of waiting for the slow spools cadence.
        self._force_spools = True
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
