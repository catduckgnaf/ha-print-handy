"""DataUpdateCoordinator for Print Handy."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PrintHandyApiClient, PrintHandyAuthError, PrintHandyConnectionError
from .const import CONF_SCAN_INTERVAL, CONF_TOKEN, DEFAULT_SCAN_INTERVAL, DOMAIN
from .model import Snapshot

LOGGER = logging.getLogger(__name__)


class PrintHandyDataUpdateCoordinator(DataUpdateCoordinator[Snapshot]):
    """Poll Print Handy's /api/status for the whole instance.

    One coordinator per config entry: the snapshot covers every printer
    on the roster, so all printer devices read from this single poll.
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
        """Fetch the latest snapshot."""
        try:
            return await self.client.async_get_status()
        except PrintHandyAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except PrintHandyConnectionError as err:
            raise UpdateFailed(str(err)) from err
