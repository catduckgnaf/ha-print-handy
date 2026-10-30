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
  progress, temperatures, ETA or current file) — see DESIGN.md for the gap
  this creates and the follow-up endpoint proposed for Print Handy itself.
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
class Snapshot:
    """A parsed /api/status response."""

    instance: InstanceInfo
    printers: tuple[PrinterInfo, ...] = field(default_factory=tuple)

    def printer(self, printer_id: str) -> PrinterInfo | None:
        """Look up one printer by roster id."""
        for printer in self.printers:
            if printer.printer_id == printer_id:
                return printer
        return None


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
