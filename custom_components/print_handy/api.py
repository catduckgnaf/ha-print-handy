"""Client for Print Handy's Web UI status API.

Print Handy exposes exactly one machine-readable read surface today:
``GET /api/status`` on its Web UI (see DESIGN.md). Auth is the optional
``X-WebUI-Token`` header — the same access token the Web UI login uses.
The token lives only in the config entry, is sent only as that header,
and never appears in a response.

The client duck-types the aiohttp session (anything with ``get()``
returning an async context manager whose response has ``status`` and
``json()``), which keeps it testable without Home Assistant installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .const import REQUEST_TIMEOUT, STATUS_PATH, TOKEN_HEADER
from .model import Snapshot, parse_snapshot

LOGGER = logging.getLogger(__name__)


class PrintHandyApiError(Exception):
    """Base error for the Print Handy API client."""


class PrintHandyAuthError(PrintHandyApiError):
    """The Web UI rejected the token (HTTP 401)."""


class PrintHandyConnectionError(PrintHandyApiError):
    """The Web UI could not be reached at all."""


def normalize_base_url(url: str) -> str:
    """Normalize a user-entered Web UI URL: scheme assumed http, no trailing slash."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("URL is empty")
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned


class PrintHandyApiClient:
    """Minimal client for the Print Handy status API."""

    def __init__(self, session: Any, base_url: str, token: str = "") -> None:
        """Initialize with an aiohttp-compatible session."""
        self._session = session
        self.base_url = normalize_base_url(base_url)
        self._token = token or ""

    def _headers(self) -> dict[str, str]:
        return {TOKEN_HEADER: self._token} if self._token else {}

    async def async_get_status(self) -> Snapshot:
        """Fetch and parse GET /api/status."""
        url = f"{self.base_url}{STATUS_PATH}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url, headers=self._headers()) as response:
                    if response.status == 401:
                        raise PrintHandyAuthError(
                            "Print Handy Web UI rejected the access token"
                        )
                    if response.status != 200:
                        raise PrintHandyApiError(
                            f"Print Handy Web UI returned HTTP {response.status}"
                        )
                    body = await response.json()
        except PrintHandyApiError:
            raise
        except Exception as err:  # transport failure of any flavor
            raise PrintHandyConnectionError(
                f"Could not reach the Print Handy Web UI at {self.base_url}: {err}"
            ) from err
        return parse_snapshot(body)
