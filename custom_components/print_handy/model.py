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

Spool assignment comes from ``GET /api/printers/<id>/spools`` (Print
Handy #789, parsed by :func:`parse_spools_view` below): the body is
``{ok, id, name, model, slots, shelf, spools, conflicts}`` where each
slot row is ``{slot, spool}`` with ``spool`` either null (empty) or a
spool summary ``{id, name, color, material, brand, color_hex,
remaining_weight, initial_weight, remaining_percent, archived,
location}``. A percent is present only when the spool's starting
weight is recorded — never estimated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class DoctorCheck:
    """One doctor row from the snapshot: label, status and detail.

    Status is one of "ok", "skip", "fail", "warn" as the server sends
    it. Rows ride along per check (not just as failure counts) for the
    doctor binary sensors and the Repairs issues (issue #14).
    """

    label: str
    status: str = "ok"
    detail: str = ""

    @property
    def slug(self) -> str:
        """The label as a stable id fragment ("orca cli" -> "orca_cli")."""
        return re.sub(r"[^a-z0-9]+", "_", self.label.lower()).strip("_") or "check"


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
    # The roster only carries this where a snapshot URL is configured
    # for the printer; it is the design's condition for a camera
    # entity (the image itself is fetched through the server's
    # snapshot proxy, never from this URL directly).
    camera_snapshot_url: str = ""
    # Control facts the server sends on every roster row since #1400:
    # whether it accepts printer controls at all (None from an older
    # server that does not say), and the per-heater ceilings in °C
    # ("nozzle", "bed", "chamber") its control routes refuse above.
    controls_enabled: bool | None = None
    temperature_limits: dict[str, float] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Configured name, falling back to the printer id."""
        return self.name or self.printer_id

    @property
    def has_camera(self) -> bool:
        """True where the roster carries a camera snapshot URL."""
        return bool(self.camera_snapshot_url)


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
    # None when Spoolman is not configured (the server sends null): that is
    # not the same as a configured Spoolman that is down (False).
    spoolman_ok: bool | None = None
    spoolman_url: str = ""
    spoolman_bundled: bool = False
    doctor_failures: int = 0
    doctor_warnings: int = 0
    doctor_checks: tuple[DoctorCheck, ...] = ()
    # The server's own stable id (#1400); empty from an older server.
    instance_id: str = ""
    controls_enabled: bool | None = None


@dataclass(frozen=True)
class Roster:
    """The cheap roster from GET /api/printers (Print Handy #1400).

    Read from the server's configuration only: no doctor run, no
    service probe, no printer contact, so it is safe to poll often.
    """

    version: str = ""
    instance_id: str = ""
    controls_enabled: bool | None = None
    printers: tuple[PrinterInfo, ...] = ()


@dataclass(frozen=True)
class HmsError:
    """One decoded Bambu HMS fault from a live status.

    ``description`` is None for a code the server's table does not
    list; ``listed`` says whether the code is in that table at all.
    """

    code: str
    description: str | None = None
    listed: bool = False


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
    # Object data (issue #1 remainder): the companion Print Handy build
    # always sends these keys — ``current_object`` is a name or null,
    # ``objects`` / ``excluded_objects`` are name lists, empty for
    # printers that do not report objects. Older builds omit the keys
    # entirely; parsing degrades to None / empty, never an exception.
    current_object: str | None = None
    objects: tuple[str, ...] = ()
    excluded_objects: tuple[str, ...] = ()
    # Bambu fault data (issue #14): the Bambu adapter's normalized
    # status always carries ``hms_errors`` (decoded HMS entries) and
    # ``print_error_text``; other families omit both keys, so
    # ``hms_errors`` stays None there and no fault entity is created.
    hms_errors: tuple[HmsError, ...] | None = None
    print_error_text: str = ""

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


def _names(value: Any) -> tuple[str, ...]:
    """Return a list of names as a tuple; anything else degrades to empty."""
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _hms_errors(value: Any) -> tuple[HmsError, ...] | None:
    """Parse the Bambu ``hms_errors`` list; absent key stays None.

    Only Bambu statuses carry the key at all, so its absence is the
    signal that the family reports no fault data. Junk rows inside a
    present list are dropped, never guessed at.
    """
    if not isinstance(value, list):
        return None
    errors: list[HmsError] = []
    for row in value:
        if not isinstance(row, dict) or not row.get("code"):
            continue
        description = row.get("description")
        errors.append(
            HmsError(
                code=str(row["code"]),
                description=str(description) if description else None,
                listed=row.get("listed") is True,
            )
        )
    return tuple(errors)


def _doctor_checks(value: Any) -> tuple[DoctorCheck, ...]:
    """Parse the snapshot's doctor rows, dropping rows without a label."""
    if not isinstance(value, list):
        return ()
    checks: list[DoctorCheck] = []
    for row in value:
        if not isinstance(row, dict) or not row.get("label"):
            continue
        checks.append(
            DoctorCheck(
                label=str(row["label"]),
                status=str(row.get("status") or "ok"),
                detail=str(row.get("detail") or ""),
            )
        )
    return tuple(checks)


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
        current_object=(
            str(status["current_object"])
            if isinstance(status.get("current_object"), str)
            and status.get("current_object")
            else None
        ),
        objects=_names(status.get("objects")),
        excluded_objects=_names(status.get("excluded_objects")),
        hms_errors=_hms_errors(status.get("hms_errors")),
        print_error_text=str(status.get("print_error_text") or ""),
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
class SpoolSummary:
    """One spool as the spools endpoint summarizes it (#789).

    Identity comes from the Spoolman filament record (its name is the
    color name); ``remaining_percent`` is None when the spool's
    starting weight is not recorded — never estimated.
    """

    spool_id: int | None = None
    name: str = ""
    color: str = ""
    material: str = ""
    brand: str | None = None
    color_hex: str | None = None
    remaining_weight: float | None = None
    initial_weight: float | None = None
    remaining_percent: float | None = None
    archived: bool = False
    location: str | None = None


@dataclass(frozen=True)
class SlotSpool:
    """One slot of one printer and the spool assigned to it, if any."""

    slot: int
    spool: SpoolSummary | None = None


@dataclass(frozen=True)
class PrinterSpools:
    """One printer's spools view from GET /api/printers/<id>/spools.

    ``available`` is False for the explicit unavailable view built when
    Print Handy cannot serve the endpoint for this printer (unknown
    id, or a build that predates #789); ``detail`` then names why.
    """

    printer_id: str
    name: str = ""
    model: str = ""
    slots: tuple[SlotSpool, ...] = ()
    shelf: tuple[SpoolSummary, ...] = ()
    spools: tuple[SpoolSummary, ...] = ()
    conflicts: tuple[str, ...] = ()
    available: bool = True
    detail: str = ""

    def slot(self, slot: int) -> SlotSpool | None:
        """Return one slot row by slot number, or None if not reported."""
        for row in self.slots:
            if row.slot == slot:
                return row
        return None


def _spool_id(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _parse_spool_summary(raw: Any) -> SpoolSummary | None:
    if not isinstance(raw, dict):
        return None
    brand = raw.get("brand")
    color_hex = raw.get("color_hex")
    location = raw.get("location")
    return SpoolSummary(
        spool_id=_spool_id(raw.get("id")),
        name=str(raw.get("name") or ""),
        color=str(raw.get("color") or ""),
        material=str(raw.get("material") or ""),
        brand=str(brand) if brand else None,
        color_hex=str(color_hex) if color_hex else None,
        remaining_weight=_number(raw.get("remaining_weight")),
        initial_weight=_number(raw.get("initial_weight")),
        remaining_percent=_number(raw.get("remaining_percent")),
        archived=raw.get("archived") is True,
        location=str(location) if location else None,
    )


def parse_spools_view(raw: Any, printer_id: str = "") -> PrinterSpools:
    """Parse a decoded /api/printers/<id>/spools body.

    Raises TypeError when the body is not an object carrying a
    ``slots`` list. Rows that are not objects, and slots without a
    whole-number slot number, are skipped rather than guessed at.
    """
    if not isinstance(raw, dict):
        raise TypeError("spools body is not a JSON object")
    raw_slots = raw.get("slots")
    if not isinstance(raw_slots, list):
        raise TypeError("spools body has no slots list")
    slots: list[SlotSpool] = []
    for row in raw_slots:
        if not isinstance(row, dict):
            continue
        slot_number = _count(row.get("slot"))
        if slot_number is None:
            continue
        spool_raw = row.get("spool")
        spool = _parse_spool_summary(spool_raw) if isinstance(spool_raw, dict) else None
        slots.append(SlotSpool(slot=slot_number, spool=spool))
    shelf = tuple(
        summary
        for summary in (_parse_spool_summary(row) for row in raw.get("shelf") or [])
        if summary is not None
    ) if isinstance(raw.get("shelf"), list) else ()
    spools = tuple(
        summary
        for summary in (_parse_spool_summary(row) for row in raw.get("spools") or [])
        if summary is not None
    ) if isinstance(raw.get("spools"), list) else ()
    conflicts_raw = raw.get("conflicts")
    conflicts = (
        tuple(str(row) for row in conflicts_raw if isinstance(row, str))
        if isinstance(conflicts_raw, list)
        else ()
    )
    return PrinterSpools(
        printer_id=str(raw.get("id") or printer_id),
        name=str(raw.get("name") or ""),
        model=str(raw.get("model") or ""),
        slots=tuple(slots),
        shelf=shelf,
        spools=spools,
        conflicts=conflicts,
    )


def unavailable_spools(printer_id: str, detail: str) -> PrinterSpools:
    """Build the explicit unavailable spools view for one printer."""
    return PrinterSpools(printer_id=printer_id, available=False, detail=detail)


def slot_spool_attributes(view: PrinterSpools | None, slot: int) -> dict[str, Any]:
    """Return the grouped attributes for one slot's spool sensor.

    The sensor's state is the spool name; material, color and
    remaining weight ride as attributes (the grouped-data pattern
    DESIGN.md records for the AMS filament sensor). An empty slot
    still reports its slot number and a null spool id, and any
    conflicts the view names ride along so they are visible.
    """
    attrs: dict[str, Any] = {"slot": slot, "spool_id": None}
    if view is None:
        return attrs
    if view.conflicts:
        attrs["conflicts"] = list(view.conflicts)
    row = view.slot(slot)
    spool = row.spool if row is not None else None
    if spool is None:
        return attrs
    attrs.update(
        {
            "spool_id": spool.spool_id,
            "spool_name": spool.name or None,
            "material": spool.material or None,
            "color": spool.color or None,
            "color_hex": spool.color_hex,
            "brand": spool.brand,
            "remaining_weight": spool.remaining_weight,
            "initial_weight": spool.initial_weight,
            "remaining_percent": spool.remaining_percent,
            "archived": spool.archived,
            "location": spool.location,
        }
    )
    return attrs


@dataclass(frozen=True)
class Snapshot:
    """A parsed /api/status response, plus per-printer live statuses."""

    instance: InstanceInfo
    printers: tuple[PrinterInfo, ...] = field(default_factory=tuple)
    statuses: dict[str, PrinterStatus] = field(default_factory=dict)
    spools: dict[str, PrinterSpools] = field(default_factory=dict)

    def printer(self, printer_id: str) -> PrinterInfo | None:
        """Look up one printer by roster id."""
        for printer in self.printers:
            if printer.printer_id == printer_id:
                return printer
        return None

    def status(self, printer_id: str) -> PrinterStatus | None:
        """Return the live status for one printer, or None if it was not polled."""
        return self.statuses.get(printer_id)

    def spool_view(self, printer_id: str) -> PrinterSpools | None:
        """Return the spools view for one printer, or None if not polled."""
        return self.spools.get(printer_id)


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


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _temperature_limits(value: Any) -> dict[str, float]:
    """Parse ``temperature_limits_c``; junk and non-numbers are dropped."""
    if not isinstance(value, dict):
        return {}
    limits: dict[str, float] = {}
    for heater, limit in value.items():
        number = _number(limit)
        if isinstance(heater, str) and number is not None and number > 0:
            limits[heater] = number
    return limits


def _roster_printers(
    roster: list[Any], listener: dict[str, Any] | None = None
) -> tuple[PrinterInfo, ...]:
    """Parse roster rows (shared by /api/status and /api/printers)."""
    printers: list[PrinterInfo] = []
    for entry in roster:
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        printer_id = str(entry["id"])
        is_default = _as_bool(entry.get("default"))
        row = _listener_row(listener, printer_id, is_default) if listener else {}
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
                camera_snapshot_url=str(entry.get("camera_snapshot_url") or ""),
                controls_enabled=_optional_bool(entry.get("controls_enabled")),
                temperature_limits=_temperature_limits(entry.get("temperature_limits_c")),
            )
        )
    return tuple(printers)


def parse_roster(raw: Any) -> Roster:
    """Parse a decoded GET /api/printers body. Raises TypeError on bad shape."""
    if not isinstance(raw, dict):
        raise TypeError("roster body is not a JSON object")
    rows = raw.get("printers")
    if not isinstance(rows, list):
        raise TypeError("roster body has no printers list")
    return Roster(
        version=str(raw.get("version") or ""),
        instance_id=str(raw.get("instance_id") or ""),
        controls_enabled=_optional_bool(raw.get("controls_enabled")),
        printers=_roster_printers(rows),
    )


def merge_roster(snapshot: Snapshot, roster: Roster) -> Snapshot:
    """Lay a fresh cheap roster over the last full snapshot.

    The roster decides which printers exist and their names, endpoints
    and control facts; listener health only comes with the full
    snapshot, so each printer keeps the listener fields it had there.
    Instance health stays as the full snapshot measured it.
    """
    previous = {printer.printer_id: printer for printer in snapshot.printers}
    printers = []
    for printer in roster.printers:
        old = previous.get(printer.printer_id)
        if old is not None:
            printer = replace(
                printer,
                listener_required=old.listener_required,
                listener_ok=old.listener_ok,
                listener_reason=old.listener_reason,
            )
        printers.append(printer)
    instance = replace(
        snapshot.instance,
        version=roster.version or snapshot.instance.version,
        instance_id=roster.instance_id or snapshot.instance.instance_id,
        controls_enabled=(
            roster.controls_enabled
            if roster.controls_enabled is not None
            else snapshot.instance.controls_enabled
        ),
    )
    return replace(snapshot, instance=instance, printers=tuple(printers))


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

    printers = _roster_printers(roster, listener)

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
        spoolman_ok=spoolman.get("ok") if isinstance(spoolman.get("ok"), bool) else None,
        spoolman_url=str(spoolman.get("url") or ""),
        spoolman_bundled=_as_bool(spoolman.get("bundled")),
        doctor_failures=sum(1 for row in doctor if isinstance(row, dict) and row.get("status") == "fail"),
        doctor_warnings=sum(1 for row in doctor if isinstance(row, dict) and row.get("status") == "warn"),
        doctor_checks=_doctor_checks(doctor),
        instance_id=str(raw.get("instance_id") or ""),
        controls_enabled=_optional_bool(raw.get("controls_enabled")),
    )
    return Snapshot(instance=instance, printers=printers)
