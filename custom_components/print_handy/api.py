"""Client for Print Handy's Web UI status API.

Print Handy's machine-readable read surface on its Web UI is
``GET /api/status`` (instance + roster snapshot) plus, since the
per-printer endpoint landed (Print Handy #664), ``GET
/api/printers/<id>/status`` for one printer's live status. Auth is the
optional ``X-WebUI-Token`` header — the same access token the Web UI
login uses. The token lives only in the config entry, is sent only as
that header, and never appears in a response.

The client duck-types the aiohttp session (anything with ``get()``
returning an async context manager whose response has ``status`` and
``json()``), which keeps it testable without Home Assistant installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from .const import PRINTER_STATUS_PATH, REQUEST_TIMEOUT, STATUS_PATH, TOKEN_HEADER
from .model import (
    PrinterStatus,
    Snapshot,
    parse_printer_status,
    parse_snapshot,
    unavailable_status,
)

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

    async def _async_get_json(self, url: str) -> tuple[int, Any]:
        """GET a URL, returning (HTTP status, decoded JSON body if any).

        A 401 always raises PrintHandyAuthError; transport failures raise
        PrintHandyConnectionError. Other statuses are the caller's call.
        """
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                async with self._session.get(url, headers=self._headers()) as response:
                    if response.status == 401:
                        raise PrintHandyAuthError(
                            "Print Handy Web UI rejected the access token"
                        )
                    try:
                        body = await response.json()
                    except Exception:  # a body we cannot decode is no body
                        body = None
                    return response.status, body
        except PrintHandyApiError:
            raise
        except Exception as err:  # transport failure of any flavor
            raise PrintHandyConnectionError(
                f"Could not reach the Print Handy Web UI at {self.base_url}: {err}"
            ) from err

    async def async_get_status(self) -> Snapshot:
        """Fetch and parse GET /api/status."""
        status, body = await self._async_get_json(f"{self.base_url}{STATUS_PATH}")
        if status != 200:
            raise PrintHandyApiError(f"Print Handy Web UI returned HTTP {status}")
        return parse_snapshot(body)

    async def async_get_printer_status(self, printer_id: str) -> PrinterStatus:
        """Fetch and parse GET /api/printers/<id>/status for one printer.

        A 404 (unknown id, or a Print Handy too old to have the endpoint)
        is data, not an error: it yields the explicit unavailable status,
        matching the endpoint's own treatment of an unreadable printer.
        """
        path = PRINTER_STATUS_PATH.format(printer_id=quote(printer_id, safe=""))
        status, body = await self._async_get_json(f"{self.base_url}{path}")
        if status == 404:
            reason = ""
            if isinstance(body, dict):
                reason = str(body.get("reason") or "")
            return unavailable_status(
                printer_id, reason or "Print Handy reports no such printer"
            )
        if status != 200:
            raise PrintHandyApiError(f"Print Handy Web UI returned HTTP {status}")
        return parse_printer_status(body, printer_id)

    async def async_get_printer_statuses(
        self, printer_ids: list[str]
    ) -> dict[str, PrinterStatus]:
        """Fetch every roster printer's live status, tolerating failures.

        One printer failing (HTTP error, transport error, unparsable
        body) yields that printer's unavailable status and never fails
        the batch. Only an auth rejection (401) propagates, because the
        whole entry's credential is wrong, not one printer.
        """

        async def _one(printer_id: str) -> PrinterStatus:
            try:
                return await self.async_get_printer_status(printer_id)
            except PrintHandyAuthError:
                raise
            except PrintHandyApiError as err:
                LOGGER.debug("Status for printer %s failed: %s", printer_id, err)
                return unavailable_status(printer_id, str(err))
            except Exception as err:  # parse/transport failure for this printer
                LOGGER.debug("Status for printer %s failed: %s", printer_id, err)
                return unavailable_status(printer_id, f"{type(err).__name__}: {err}")

        results = await asyncio.gather(
            *(_one(printer_id) for printer_id in printer_ids),
            return_exceptions=True,
        )
        statuses: dict[str, PrinterStatus] = {}
        for printer_id, result in zip(printer_ids, results, strict=True):
            if isinstance(result, PrintHandyAuthError):
                raise result
            if isinstance(result, PrinterStatus):
                statuses[printer_id] = result
            else:  # pragma: no cover - _one already converts these
                statuses[printer_id] = unavailable_status(
                    printer_id, f"{type(result).__name__}: {result}"
                )
        return statuses
