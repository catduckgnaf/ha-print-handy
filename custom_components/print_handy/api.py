"""Client for Print Handy's Web UI status and control API.

Print Handy's machine-readable read surface on its Web UI is
``GET /api/status`` (instance + roster snapshot) plus, since the
per-printer endpoint landed (Print Handy #664), ``GET
/api/printers/<id>/status`` for one printer's live status. Phase 3 adds
the control surface (Print Handy #708): ``POST
/api/printers/<id>/pause|resume|cancel`` and ``POST
/api/printers/<id>/target``. Auth is the optional ``X-WebUI-Token``
header — the same access token the Web UI login uses; the server also
accepts the Web UI password there. The token lives
only in the config entry, is sent only as that header, and never
appears in a response.

Controls are gated by an operator setting on the Print Handy side and
are OFF by default there: a server with controls disabled refuses the
control POSTs (403/404), and this client raises
:class:`PrintHandyControlRefusedError` so the refusal surfaces as a
visible failure in Home Assistant instead of a silent no-op.

The issue-#1 remainder (0.5.0) extends the same control surface:
``emergency_stop`` / ``firmware_restart`` / ``server_restart`` join
the button actions in :data:`CONTROL_PATHS` (each requiring the
``{"confirm": true}`` body, like cancel), and two service actions
join on their own paths — ``POST /api/printers/<id>/gcode`` with
``{"script": ...}`` and ``POST /api/printers/<id>/exclude`` with
``{"object": ..., "confirm": true}``. These are control actions
behind the same gate, not spool actions, so their refusals raise
:class:`PrintHandyControlRefusedError` (not the spool error) —
including the exclude endpoint's named 409 refusals (unknown object,
and the last remaining object, where the server's reason says to use
cancel instead); that reason reaches Home Assistant verbatim.

The spool surface (Print Handy #789) follows the same posture:
``GET /api/printers/<id>/spools`` is read in the same poll as the
status endpoints, and ``POST /api/printers/<id>/spools/{assign,swap,
unassign,add,sync}`` sits behind the same controls gate. A refused
spool action (controls off, unknown spool or slot, a reconcile
conflict, a bad body) raises :class:`PrintHandySpoolRefusedError`
carrying the server's named reason.

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
    CONFIRM_ACTIONS,
    CONTROL_PATHS,
    PRINTER_EXCLUDE_PATH,
    PRINTER_GCODE_PATH,
    PRINTER_SPOOL_ACTION_PATH,
    PRINTER_SPOOLS_PATH,
    PRINTER_STATUS_PATH,
    PRINTER_TARGET_PATH,
    REQUEST_TIMEOUT,
    SPOOL_ACTIONS,
    STATUS_PATH,
    TOKEN_HEADER,
)
from .model import (
    PrinterSpools,
    PrinterStatus,
    Snapshot,
    parse_printer_status,
    parse_snapshot,
    parse_spools_view,
    unavailable_spools,
    unavailable_status,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_RETRY_AFTER = 60
MAX_RETRY_AFTER = 3600

HOST_REFUSED_MESSAGE = (
    "Print Handy refused the request because of the address it was sent to. "
    "With no Web UI password set, the server only answers by IP address or a "
    "local host name (a single word, or ending in .local, .lan, .home, "
    ".internal or .home.arpa). Use the server's IP address in the URL, or set "
    "a Web UI password in Print Handy and enter it as the access token."
)


class PrintHandyApiError(Exception):
    """Base error for the Print Handy API client."""


class PrintHandyAuthError(PrintHandyApiError):
    """The Web UI rejected the token (HTTP 401)."""


class PrintHandyConnectionError(PrintHandyApiError):
    """The Web UI could not be reached at all."""


class PrintHandyRateLimitedError(PrintHandyApiError):
    """The Web UI is rate-limiting this client (HTTP 429).

    The server answers 429 with ``Retry-After`` while it is locking out an
    address after repeated wrong tokens, even for a correct token, so the
    right response is to stop sending requests until the wait is over.
    ``retry_after`` is that wait in seconds.
    """

    def __init__(self, message: str, retry_after: int = 60) -> None:
        """Keep the wait the server asked for."""
        super().__init__(message)
        self.retry_after = retry_after


class PrintHandyHostRefusedError(PrintHandyApiError):
    """The Web UI refused the request's Host header (HTTP 403 on a read).

    A Web UI with no password only answers requests whose Host is an IP
    address, a single-label name or a private-use suffix (DNS-rebinding
    guard), so a public-looking host name in the URL is refused.
    """


class PrintHandyServerError(PrintHandyApiError):
    """The Web UI answered with a server error (HTTP 5xx)."""


class PrintHandyResponseError(PrintHandyApiError):
    """The Web UI answered, but not with a body this client can use."""


class PrintHandyControlRefusedError(PrintHandyApiError):
    """Print Handy refused a control action, naming the reason.

    Covers the named refusals the control API sends: the controls
    gate (403), an unknown printer (404), a missing confirm or bad
    body (400), a printer in the wrong state or — for exclude — the
    last remaining object (409), and a printer family with no such
    capability (501). The usual first cause is the controls gate on
    the Print Handy side: control endpoints are off by default until
    the operator enables them. A 404 can also mean the Print Handy
    build predates the control API. The server's ``reason`` is
    carried in the message.
    """


class PrintHandySpoolRefusedError(PrintHandyApiError):
    """Print Handy refused a spool action, naming the reason (#789).

    Covers every named refusal the spool API sends: the controls gate
    (403), an unknown printer or spool (404), a bad body (400), a
    spool conflict (409) and a Spoolman transport failure the server
    reports as 502. The server's ``reason`` is carried in the message.
    """


def _reason(body: Any) -> str:
    """Return the server's ``reason`` from a JSON error body, or an empty string."""
    if isinstance(body, dict):
        return str(body.get("reason") or "")
    return ""


def _detail(body: Any) -> str:
    """``: <reason>`` for an error message when the body names one."""
    reason = _reason(body)
    return f": {reason}" if reason else ""


def _retry_after(headers: Any) -> int | None:
    """Return the ``Retry-After`` wait in whole seconds, clamped, or None."""
    if headers is None:
        return None
    try:
        raw = headers.get("Retry-After")
        seconds = int(float(str(raw).strip()))
    except (AttributeError, TypeError, ValueError):
        return None
    return max(1, min(seconds, MAX_RETRY_AFTER))


def normalize_base_url(url: str) -> str:
    """Normalize a user-entered Web UI URL: scheme assumed http, no trailing slash."""
    cleaned = str(url or "").strip().rstrip("/")
    if not cleaned:
        raise ValueError("URL is empty")
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    return cleaned


# Errors that concern the whole connection rather than one printer; the
# batch polls re-raise them instead of turning them into unavailable rows.
_ENTRY_WIDE_ERRORS = (
    PrintHandyAuthError,
    PrintHandyRateLimitedError,
    PrintHandyHostRefusedError,
)


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
    ) -> tuple[int, Any, int | None]:
        """Send a request, returning (HTTP status, JSON body, Retry-After).

        A 401 always raises PrintHandyAuthError; transport failures and
        timeouts raise PrintHandyConnectionError. Other statuses are the
        caller's call. ``Retry-After`` is the server's wait in seconds
        when it sent a usable one, else None.
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
                    return (
                        response.status,
                        body,
                        _retry_after(getattr(response, "headers", None)),
                    )
        except PrintHandyApiError:
            raise
        except TimeoutError as err:
            raise PrintHandyConnectionError(
                f"The Print Handy Web UI at {self.base_url} did not answer "
                f"within {REQUEST_TIMEOUT} seconds"
            ) from err
        except Exception as err:  # transport failure of any flavor
            raise PrintHandyConnectionError(
                f"Could not reach the Print Handy Web UI at {self.base_url}: "
                f"{err or type(err).__name__}"
            ) from err

    async def _async_get_json(self, url: str) -> tuple[int, Any]:
        """GET a URL, returning (HTTP status, decoded JSON body if any).

        A 429 and a 403 on a read concern the whole connection, not one
        printer (rate limiting, the Host-header guard), so they raise
        their own errors here; every other status is the caller's call.
        """
        status, body, retry_after = await self._async_request_json("get", url)
        if status == 429:
            wait = retry_after or DEFAULT_RETRY_AFTER
            raise PrintHandyRateLimitedError(
                "Print Handy is rate-limiting this client after failed access "
                f"attempts; waiting {wait} seconds before trying again",
                wait,
            )
        if status == 403:
            raise PrintHandyHostRefusedError(HOST_REFUSED_MESSAGE)
        return status, body

    async def _async_post_control(
        self, url: str, what: str, json_body: Any = None
    ) -> None:
        """POST a control action, mapping refusals to a dedicated error.

        Any 2xx is success. A 400/403/404/409/501 means Print Handy
        refused the action — a bad or unconfirmed body, controls
        disabled there, the printer is unknown, the printer is in the
        wrong state (or, for exclude, the object cannot be excluded),
        the family has no such capability, or the build predates the
        control API — and raises PrintHandyControlRefusedError
        carrying the server's reason when it sent one. Other statuses
        raise the generic API error; auth and transport failures raise
        as everywhere else in this client.
        """
        status, body, retry_after = await self._async_request_json(
            "post", url, json_body
        )
        if 200 <= status < 300:
            return
        reason = _reason(body)
        if status == 403 and not reason:
            # The Host-header guard answers 403 as plain text, not the
            # control gate's JSON reason.
            reason = (
                "Print Handy gave no reason. If controls are enabled there, "
                "it may be refusing the host name in the URL: use the "
                "server's IP address, or set a Web UI password and use it as "
                "the access token"
            )
        if status == 429:
            reason = reason or "too many failed attempts"
            if retry_after:
                reason = f"{reason}; try again in {retry_after} seconds"
        detail = f": {reason}" if reason else ""
        if status in (400, 403, 404, 409, 501):
            raise PrintHandyControlRefusedError(
                f"Print Handy refused {what} (HTTP {status}){detail}"
            )
        # 5xx (the server's 502 reasons end with "Nothing sent." for a
        # printer it could not reach), 429 and anything else: still carry
        # the server's reason, which matters most for emergency stop.
        raise PrintHandyApiError(
            f"Print Handy Web UI returned HTTP {status} for {what}{detail}"
        )

    async def async_get_status(self) -> Snapshot:
        """Fetch and parse GET /api/status."""
        status, body = await self._async_get_json(f"{self.base_url}{STATUS_PATH}")
        if status >= 500:
            raise PrintHandyServerError(
                f"Print Handy Web UI returned HTTP {status}{_detail(body)}; "
                "its self-check may be failing"
            )
        if status != 200:
            raise PrintHandyApiError(
                f"Print Handy Web UI returned HTTP {status}{_detail(body)}"
            )
        try:
            return parse_snapshot(body)
        except (TypeError, ValueError, KeyError) as err:
            raise PrintHandyResponseError(
                f"Print Handy answered, but not with a usable status snapshot: {err}"
            ) from err

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
        the batch. The entry-wide errors propagate (401, 429 and a
        refused Host header concern the whole connection), and so does
        a transport failure on every printer at once, which means the
        Web UI itself is unreachable.
        """

        outage: list[PrintHandyConnectionError] = []

        async def _one(printer_id: str) -> PrinterStatus:
            try:
                return await self.async_get_printer_status(printer_id)
            except _ENTRY_WIDE_ERRORS:
                raise
            except PrintHandyConnectionError as err:
                outage.append(err)
                LOGGER.debug("Status for printer %s failed: %s", printer_id, err)
                return unavailable_status(printer_id, str(err))
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
        if printer_ids and len(outage) == len(printer_ids):
            # Every printer's request failed at the transport level: the
            # Web UI itself is unreachable, which is an update failure,
            # not a roster of individually unavailable printers.
            raise outage[0]
        statuses: dict[str, PrinterStatus] = {}
        for printer_id, result in zip(printer_ids, results, strict=True):
            if isinstance(result, _ENTRY_WIDE_ERRORS):
                raise result
            if isinstance(result, PrinterStatus):
                statuses[printer_id] = result
            else:  # pragma: no cover - _one already converts these
                statuses[printer_id] = unavailable_status(
                    printer_id, f"{type(result).__name__}: {result}"
                )
        return statuses

    async def async_printer_control(self, printer_id: str, action: str) -> None:
        """POST a button control action for one printer.

        Covers pause / resume / cancel plus the issue-#1 remainder
        buttons (emergency_stop / firmware_restart / server_restart).
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
        # Print Handy refuses the CONFIRM_ACTIONS (400) without an
        # explicit confirm; the button press is that confirmation.
        # Pause and resume take no body.
        body = {"confirm": True} if action in CONFIRM_ACTIONS else None
        await self._async_post_control(
            f"{self.base_url}{path}",
            f"the {action} action for printer '{printer_id}'",
            body,
        )

    async def async_send_gcode(self, printer_id: str, script: str) -> None:
        """POST a G-code script to one printer (issue #1, send_gcode).

        The body is exactly the contract's ``{"script": ...}``; the
        script goes to the named printer only, through Print Handy's
        controls gate. Refusals raise PrintHandyControlRefusedError
        carrying the server's reason — this is a control action, so
        the control error is the fit, not the spool error. An empty
        script is a programming error, refused before any POST.
        """
        if not str(script or "").strip():
            raise ValueError("a G-code script is required")
        path = PRINTER_GCODE_PATH.format(printer_id=quote(printer_id, safe=""))
        await self._async_post_control(
            f"{self.base_url}{path}",
            f"sending G-code to printer '{printer_id}'",
            {"script": script},
        )

    async def async_exclude_object(self, printer_id: str, object_name: str) -> None:
        """POST an exclude-object request for one printer (issue #1).

        The body carries the object name plus ``confirm: true`` — the
        service call is that confirmation. Refusals raise
        PrintHandyControlRefusedError carrying the server's reason
        verbatim, including the 409 for the last remaining object
        (where the reason says to use cancel instead) and for an
        unknown object. An empty name is refused before any POST.
        """
        if not str(object_name or "").strip():
            raise ValueError("an object name is required")
        path = PRINTER_EXCLUDE_PATH.format(printer_id=quote(printer_id, safe=""))
        await self._async_post_control(
            f"{self.base_url}{path}",
            f"excluding object '{object_name}' on printer '{printer_id}'",
            {"object": object_name, "confirm": True},
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

    async def async_get_printer_spools(self, printer_id: str) -> PrinterSpools:
        """Fetch and parse GET /api/printers/<id>/spools for one printer.

        A 404 (unknown id, or a Print Handy too old to have the spools
        endpoint) is data, not an error: it yields the explicit
        unavailable view, matching the per-printer status treatment.
        """
        path = PRINTER_SPOOLS_PATH.format(printer_id=quote(printer_id, safe=""))
        status, body = await self._async_get_json(f"{self.base_url}{path}")
        if status == 404:
            reason = ""
            if isinstance(body, dict):
                reason = str(body.get("reason") or "")
            return unavailable_spools(
                printer_id, reason or "Print Handy reports no such printer"
            )
        if status != 200:
            raise PrintHandyApiError(f"Print Handy Web UI returned HTTP {status}")
        return parse_spools_view(body, printer_id)

    async def async_get_printer_spool_views(
        self, printer_ids: list[str]
    ) -> dict[str, PrinterSpools]:
        """Fetch every roster printer's spools view, tolerating failures.

        Mirrors async_get_printer_statuses: one printer failing yields
        that printer's unavailable view and never fails the batch;
        only the entry-wide errors (401, 429, a refused Host) propagate.
        """

        async def _one(printer_id: str) -> PrinterSpools:
            try:
                return await self.async_get_printer_spools(printer_id)
            except _ENTRY_WIDE_ERRORS:
                raise
            except PrintHandyApiError as err:
                LOGGER.debug("Spools for printer %s failed: %s", printer_id, err)
                return unavailable_spools(printer_id, str(err))
            except Exception as err:  # parse/transport failure for this printer
                LOGGER.debug("Spools for printer %s failed: %s", printer_id, err)
                return unavailable_spools(printer_id, f"{type(err).__name__}: {err}")

        results = await asyncio.gather(
            *(_one(printer_id) for printer_id in printer_ids),
            return_exceptions=True,
        )
        views: dict[str, PrinterSpools] = {}
        for printer_id, result in zip(printer_ids, results, strict=True):
            if isinstance(result, _ENTRY_WIDE_ERRORS):
                raise result
            if isinstance(result, PrinterSpools):
                views[printer_id] = result
            else:  # pragma: no cover - _one already converts these
                views[printer_id] = unavailable_spools(
                    printer_id, f"{type(result).__name__}: {result}"
                )
        return views

    async def _async_post_spool(
        self, printer_id: str, action: str, json_body: Any = None
    ) -> dict[str, Any]:
        """POST one spool action, mapping named refusals to an error.

        Any 2xx returns the decoded body. Any other status whose body
        carries the spool API's ``reason`` raises
        PrintHandySpoolRefusedError with that reason (the controls
        gate, unknown spool or slot, a conflict and validation all
        refuse this way); a refusal without a reason, and any other
        failure, raises as elsewhere in this client.
        """
        if action not in SPOOL_ACTIONS:
            raise ValueError(f"unknown spool action: {action!r}")
        path = PRINTER_SPOOL_ACTION_PATH.format(
            printer_id=quote(printer_id, safe=""), action=action
        )
        what = f"the spool {action} action for printer '{printer_id}'"
        status, body, _ = await self._async_request_json(
            "post", f"{self.base_url}{path}", json_body
        )
        if 200 <= status < 300:
            return body if isinstance(body, dict) else {}
        reason = _reason(body)
        if reason:
            raise PrintHandySpoolRefusedError(
                f"Print Handy refused {what} (HTTP {status}): {reason}"
            )
        if status in (400, 403, 404, 409, 502):
            raise PrintHandySpoolRefusedError(
                f"Print Handy refused {what} (HTTP {status})"
            )
        raise PrintHandyApiError(
            f"Print Handy Web UI returned HTTP {status} for {what}"
        )

    async def async_assign_spool(
        self, printer_id: str, slot: int, spool_id: int
    ) -> dict[str, Any]:
        """POST assign: bind ``spool_id`` to ``slot`` on one printer.

        A spool already in the slot goes back on the shelf (the API's
        bind behavior). Refusals raise PrintHandySpoolRefusedError.
        """
        return await self._async_post_spool(
            printer_id, "assign", {"slot": int(slot), "spool_id": int(spool_id)}
        )

    async def async_swap_spools(
        self, printer_id: str, slot_a: int, slot_b: int
    ) -> dict[str, Any]:
        """POST swap: exchange the spools in two slots of one printer."""
        return await self._async_post_spool(
            printer_id,
            "swap",
            {"slot_a": int(slot_a), "slot_b": int(slot_b)},
        )

    async def async_unassign_spool(
        self, printer_id: str, slot: int
    ) -> dict[str, Any]:
        """POST unassign: clear one slot; its spool goes on the shelf."""
        return await self._async_post_spool(
            printer_id, "unassign", {"slot": int(slot)}
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
    ) -> dict[str, Any]:
        """POST add: create one spool on the shelf for one printer.

        Maps directly onto the API's add action (the #512 creation
        path): material, color and the full starting weight in grams
        are required there, brand and hex are optional and are only
        sent when given. Loading the new spool into a slot is a
        separate assign, exactly as on the Print Handy side.
        """
        body: dict[str, Any] = {
            "material": material,
            "color": color,
            "grams": float(grams),
        }
        if brand:
            body["brand"] = brand
        if hex:
            body["hex"] = hex
        return await self._async_post_spool(printer_id, "add", body)

    async def async_sync_spools(self, printer_id: str) -> dict[str, Any]:
        """POST sync: reconcile one printer's slot state with Spoolman.

        The API's sync action takes no body.
        """
        return await self._async_post_spool(printer_id, "sync", None)
