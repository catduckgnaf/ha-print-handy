"""Parsing of Print Handy's /api/status snapshot into typed structures.

This module is deliberately free of Home Assistant (and aiohttp) imports so
the parsing rules can be unit tested anywhere. The snapshot shape comes from
print_handy/webui/status.py in the Print Handy repo:

- top level: version, host, ok, printer{}, bot{}, listener{}, printers[],
  spoolman{}, services{}, doctor[]
- ``printers`` is the roster: one row per printer with id, name, model,
  vendor, connection and a ``default`` flag.
- ``listener`` carries per-printer listener health: for a multi-printer
  instance under ``listener.printers.<id>`` ({required, ok, reason}); for a
  single-printer instance as the section's own {required, ok, reason}.
- The snapshot intentionally carries NO live print state (no print state,
  progress, temperatures, ETA or current file).

Live per-printer status comes from the companion endpoint
``GET /api/printers/<id>/status`` (Print Handy #664, parsed by
:func:`parse_printer_status` below): the body is the roster identity plus a
``status`` object holding the adapter's normalized status — ``state``,
``filename``, ``progress`` (0..1), ``hotend_c`` and ``bed_c`` always, plus
where the adapter provides them ``hotend_target_c``, ``bed_target_c``,
``remaining_min``, ``layer`` and a total-layer count (Bambu spells it
``total_layer``, Elegoo SDCP ``total_layers``), and vendor extras such as
Bambu's decoded ``ams`` block. A printer whose live read fails still
answers 200 with ``status.state == "unavailable"`` and a ``detail``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PrinterInfo:
    """One printer from the Print Handy roster."""

    printer_id: str
    name: str
    model: str
    vendor: str
    connection: str
    is_default: bool = False
    listener_required: bool = False
    listener_ok: bool | None = None
    listener_reason: str = ""

    @property
    def display_name(self) -> str:
        """Configured name, falling back to the printer id."""
        return self.name or self.printer_id


@dataclass(frozen=True)
class InstanceInfo:
    """The Print Handy instance itself (the hub device)."""

    version: str = ""
    host: str = ""
    ok: bool = False
    bot_ok: bool = False
    bot_platform: str = ""
    bot_mode: str = ""
    bot_connected: bool | None = None
    spoolman_ok: bool = False
    spoolman_url: str = ""
    spoolman_bundled: bool = False
    doctor_failures: int = 0
    doctor_warnings: int = 0


@dataclass(frozen=True)
class PrinterStatus:
    """One printer's live status from GET /api/printers/<id>/status.

    Only the fields the endpoint genuinely sends are modeled; anything an
    adapter does not provide stays None (never guessed). ``ams`` is the
    raw decoded AMS block Bambu printers report (units/trays/external
    spool); other vendors do not send it.
    """

    printer_id: str
    state: str = "unknown"
    filename: str = ""
    progress: float | None = None  # 0..1, as the endpoint sends it
    hotend_c: float | None = None
    bed_c: float | None = None
    hotend_target_c: float | None = None
    bed_target_c: float | None = None
    remaining_min: float | None = None
    layer: int | None = None
    total_layers: int | None = None
    detail: str = ""
    ams: dict[str, Any] | None = None

    @property
    def unavailable(self) -> bool:
        """True when Print Handy could not read this printer live."""
        return self.state == "unavailable"


def _number(value: Any) -> float | None:
    """Return a JSON number as float; bools and non-numbers are not readings."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _count(value: Any) -> int | None:
    """Return a layer-style count: whole numbers only, as int."""
    number = _number(value)
    return int(number) if number is not None else None


def parse_printer_status(raw: Any, printer_id: str = "") -> PrinterStatus:
    """Parse a decoded /api/printers/<id>/status body.

    Raises TypeError when the body is not an object carrying a ``status``
    object. Optional fields an adapter omits stay None.
    """
    if not isinstance(raw, dict):
        raise TypeError("printer status body is not a JSON object")
    status = raw.get("status")
    if not isinstance(status, dict):
        raise TypeError("printer status body has no status object")
    resolved_id = str(raw.get("id") or printer_id)
    total_layers = _count(status.get("total_layer"))
    if total_layers is None:
        total_layers = _count(status.get("total_layers"))
    ams = status.get("ams")
    return PrinterStatus(
        printer_id=resolved_id,
        state=str(status.get("state") or "unknown"),
        filename=str(status.get("filename") or ""),
        progress=_number(status.get("progress")),
        hotend_c=_number(status.get("hotend_c")),
        bed_c=_number(status.get("bed_c")),
        hotend_target_c=_number(status.get("hotend_target_c")),
        bed_target_c=_number(status.get("bed_target_c")),
        remaining_min=_number(status.get("remaining_min")),
        layer=_count(status.get("layer")),
        total_layers=total_layers,
        detail=str(status.get("detail") or ""),
        ams=ams if isinstance(ams, dict) else None,
    )


def unavailable_status(printer_id: str, detail: str) -> PrinterStatus:
    """Build the explicit unavailable status for a printer we could not read."""
    return PrinterStatus(printer_id=printer_id, state="unavailable", detail=detail)


def ams_trays(status: PrinterStatus) -> list[dict[str, Any]]:
    """Every AMS tray across all units, each tagged with its unit id."""
    trays: list[dict[str, Any]] = []
    if status.ams is None:
        return trays
    for unit in status.ams.get("units") or []:
        if not isinstance(unit, dict):
            continue
        for tray in unit.get("trays") or []:
            if isinstance(tray, dict):
                trays.append({**tray, "unit": unit.get("id")})
    return trays


def active_ams_tray(status: PrinterStatus) -> dict[str, Any] | None:
    """Return the tray currently feeding the printer (unit tray or external spool)."""
    for tray in ams_trays(status):
        if tray.get("active") and tray.get("present"):
            return tray
    if status.ams is not None:
        external = status.ams.get("external")
        if isinstance(external, dict) and external.get("active") and external.get("present"):
            return {**external, "unit": "external"}
    return None


def has_ams_filament(status: PrinterStatus) -> bool:
    """Report whether the printer reported any loaded AMS/external filament."""
    return active_ams_tray(status) is not None


@dataclass(frozen=True)
class Snapshot:
    """A parsed /api/status response, plus per-printer live statuses."""

    instance: InstanceInfo
    printers: tuple[PrinterInfo, ...] = field(default_factory=tuple)
    statuses: dict[str, PrinterStatus] = field(default_factory=dict)

    def printer(self, printer_id: str) -> PrinterInfo | None:
        """Look up one printer by roster id."""
        for printer in self.printers:
            if printer.printer_id == printer_id:
                return printer
        return None

    def status(self, printer_id: str) -> PrinterStatus | None:
        """Return the live status for one printer, or None if it was not polled."""
        return self.statuses.get(printer_id)


def _as_bool(value: Any) -> bool:
    return value is True


def _listener_row(listener: dict[str, Any], printer_id: str, is_default: bool) -> dict[str, Any]:
    """Return the listener health row for one printer.

    Multi-printer snapshots nest rows under ``listener.printers``; a
    single-printer snapshot puts the row on the section itself, and it
    belongs to the (only, hence default) printer.
    """
    nested = listener.get("printers")
    if isinstance(nested, dict):
        row = nested.get(printer_id)
        return row if isinstance(row, dict) else {}
    return listener if is_default else {}


def parse_snapshot(raw: Any) -> Snapshot:
    """Parse a decoded /api/status JSON body. Raises TypeError on bad shape."""
    if not isinstance(raw, dict):
        raise TypeError("status snapshot is not a JSON object")
    roster = raw.get("printers")
    if not isinstance(roster, list):
        raise TypeError("status snapshot has no printer roster")

    listener = raw.get("listener") if isinstance(raw.get("listener"), dict) else {}
    bot = raw.get("bot") if isinstance(raw.get("bot"), dict) else {}
    heartbeat = bot.get("heartbeat") if isinstance(bot.get("heartbeat"), dict) else {}
    spoolman = raw.get("spoolman") if isinstance(raw.get("spoolman"), dict) else {}
    doctor = raw.get("doctor") if isinstance(raw.get("doctor"), list) else []

    printers: list[PrinterInfo] = []
    for entry in roster:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        printer_id = str(entry["id"])
        is_default = _as_bool(entry.get("default"))
        row = _listener_row(listener, printer_id, is_default)
        ok = row.get("ok")
        printers.append(
            PrinterInfo(
                printer_id=printer_id,
                name=str(entry.get("name") or ""),
                model=str(entry.get("model") or ""),
                vendor=str(entry.get("vendor") or ""),
                connection=str(entry.get("connection") or ""),
                is_default=is_default,
                listener_required=_as_bool(row.get("required")),
                listener_ok=ok if isinstance(ok, bool) else None,
                listener_reason=str(row.get("reason") or ""),
            )
        )

    instance = InstanceInfo(
        version=str(raw.get("version") or ""),
        host=str(raw.get("host") or ""),
        ok=_as_bool(raw.get("ok")),
        bot_ok=_as_bool(bot.get("ok")),
        bot_platform=str(bot.get("platform") or ""),
        bot_mode=str(bot.get("mode") or ""),
        bot_connected=heartbeat.get("connected")
        if isinstance(heartbeat.get("connected"), bool)
        else None,
        spoolman_ok=_as_bool(spoolman.get("ok")),
        spoolman_url=str(spoolman.get("url") or ""),
        spoolman_bundled=_as_bool(spoolman.get("bundled")),
        doctor_failures=sum(1 for row in doctor if isinstance(row, dict) and row.get("status") == "fail"),
        doctor_warnings=sum(1 for row in doctor if isinstance(row, dict) and row.get("status") == "warn"),
    )
    return Snapshot(instance=instance, printers=tuple(printers))
