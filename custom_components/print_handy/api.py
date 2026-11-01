"""Client for Print Handy's Web UI status and control API.

Print Handy's machine-readable read surface on its Web UI is
``GET /api/status`` (instance + roster snapshot) plus, since the
per-printer endpoint landed (Print Handy #664), ``GET
/api/printers/<id>/status`` for one printer's live status. Phase 3 adds
the control surface (Print Handy #708): ``POST
/api/printers/<id>/pause|resume|cancel`` and ``POST
/api/printers/<id>/target``. Auth is the optional ``X-WebUI-Token``
header — the same access token the Web UI login uses. The token lives
only in the config entry, is sent only as that header, and never
appears in a response.

Controls are gated by an operator setting on the Print Handy side and
are OFF by default there: a server with controls disabled refuses the
control POSTs (403/404), and this client raises
:class:`PrintHandyControlRefusedError` so the refusal surfaces as a
visible failure in Home Assistant instead of a silent no-op.

The client duck-types the aiohttp session (anything with ``get()`` /
``post()`` returning an async context manager whose response has
``status`` and ``json()``), which keeps it testable without Home
Assistant installed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

from .const import (
    CONTROL_PATHS,
    PRINTER_STATUS_PATH,
    PRINTER_TARGET_PATH,
    REQUEST_TIMEOUT,
    STATUS_PATH,
    TOKEN_HEADER,
)
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


class PrintHandyControlRefusedError(PrintHandyApiError):
    """Print Handy refused a control action (HTTP 403/404).

    The usual cause is the controls gate on the Print Handy side:
    control endpoints are off by default until the operator enables
    them. A 404 can also mean the printer id is unknown or the Print
    Handy build predates the control API.
    """


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

    async def _async_request_json(
        self, method: str, url: str, json_body: Any = None
    ) -> tuple[int, Any]:
        """Send a request, returning (HTTP status, decoded JSON body if any).

        A 401 always raises PrintHandyAuthError; transport failures raise
        PrintHandyConnectionError. Other statuses are the caller's call.
        """
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                request = getattr(self._session, method)
                kwargs: dict[str, Any] = {"headers": self._headers()}
                if method == "post":
                    kwargs["json"] = json_body
                async with request(url, **kwargs) as response:
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

    async def _async_get_json(self, url: str) -> tuple[int, Any]:
        """GET a URL, returning (HTTP status, decoded JSON body if any)."""
        return await self._async_request_json("get", url)

    async def _async_post_control(
        self, url: str, what: str, json_body: Any = None
    ) -> None:
        """POST a control action, mapping refusals to a dedicated error.

        Any 2xx is success. A 403/404 means Print Handy refused the
        action — controls are disabled there, the printer is unknown,
        or the build predates the control API — and raises
        PrintHandyControlRefusedError carrying the server's reason when
        it sent one. Other statuses raise the generic API error; auth
        and transport failures raise as everywhere else in this client.
        """
        status, body = await self._async_request_json("post", url, json_body)
        if 200 <= status < 300:
            return
        if status in (403, 404):
            reason = ""
            if isinstance(body, dict):
                reason = str(body.get("reason") or "")
            detail = f": {reason}" if reason else ""
            raise PrintHandyControlRefusedError(
                f"Print Handy refused {what} (HTTP {status}){detail}"
            )
        raise PrintHandyApiError(
            f"Print Handy Web UI returned HTTP {status} for {what}"
        )

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

    async def async_printer_control(self, printer_id: str, action: str) -> None:
        """POST a pause / resume / cancel action for one printer.

        Acts only on the named printer. Raises PrintHandyControlRefusedError
        when Print Handy refuses (controls disabled there, unknown
        printer, or a build without the control API), PrintHandyAuthError
        on a rejected token, and PrintHandyConnectionError when the Web
        UI cannot be reached — a control action never fails silently.
        """
        path_template = CONTROL_PATHS.get(action)
        if path_template is None:
            raise ValueError(f"unknown control action: {action!r}")
        path = path_template.format(printer_id=quote(printer_id, safe=""))
        await self._async_post_control(
            f"{self.base_url}{path}", f"the {action} action for printer '{printer_id}'"
        )

    async def async_set_printer_target(
        self,
        printer_id: str,
        *,
        hotend_c: float | None = None,
        bed_c: float | None = None,
    ) -> None:
        """POST new target temperatures for one printer.

        The body always carries both keys, per the control API
        contract; a heater passed as None goes out as JSON null.
        Callers setting one heater pass the other's current target so
        a null is only sent for a heater whose target is unknown.
        Refusals and failures raise exactly as in async_printer_control.
        """
        path = PRINTER_TARGET_PATH.format(printer_id=quote(printer_id, safe=""))
        body = {"hotend_c": hotend_c, "bed_c": bed_c}
        await self._async_post_control(
            f"{self.base_url}{path}",
            f"setting target temperatures for printer '{printer_id}'",
            json_body=body,
        )
