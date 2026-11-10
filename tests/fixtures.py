"""Recorded payload shapes, built from the Print Handy server's own field lists.

/api/status mirrors print_handy/webui/status.py ``snapshot()``: version,
host, printer{model,name,vendor,connection}, bot{platform,mode,ok,reason,
heartbeat}, listener{required,ok,reason[,printers]}, printers[] roster rows
(id,name,model,vendor,connection,chat_id,default plus endpoints),
spoolman{bundled,url,ok,detail} (ok is null when Spoolman is not
configured), services{}, doctor[{label,status,detail}], slice_failures[],
ok, components{}. The per-printer body mirrors ``printer_status()``: the
roster identity plus ``status`` carrying every _STATUS_DEFAULTS key.
"""

from __future__ import annotations

import copy

MOONRAKER_ROW = {
    "id": "voron",
    "name": "Voron",
    "model": "flashforge_ad5x",
    "vendor": "flashforge",
    "connection": "moonraker",
    "chat_id": "",
    "default": True,
    "moonraker_url": "http://voron.local:7125",
}
BAMBU_ROW = {
    "id": "x1c",
    "name": "X1C",
    "model": "bambu_x1c",
    "vendor": "bambu",
    "connection": "bambulab_lan",
    "chat_id": "",
    "default": False,
    "lan_host": "x1c.local",
}


def status_body(*rows, spoolman_ok=True, doctor=None):
    """Build a full /api/status body for the given roster rows."""
    return {
        "version": "2.2.7",
        "host": "print-handy-box",
        "printer": {
            "model": rows[0]["model"],
            "name": rows[0]["name"],
            "vendor": rows[0]["vendor"],
            "connection": rows[0]["connection"],
        },
        "bot": {
            "platform": "discord",
            "mode": "off",
            "ok": None,
            "reason": "",
            "heartbeat": None,
        },
        "listener": {"required": False, "ok": None, "reason": "not required"},
        "printers": [copy.deepcopy(row) for row in rows],
        "spoolman": {
            "bundled": False,
            "url": "" if spoolman_ok is None else "http://spoolman.local:7912",
            "ok": spoolman_ok,
            "detail": "not configured" if spoolman_ok is None else "reachable",
        },
        "services": {},
        "doctor": doctor if doctor is not None else [
            {"label": "settings", "status": "ok", "detail": "fine"}
        ],
        "slice_failures": [],
        "ok": True,
        "components": {"ready": True},
    }


def printer_body(printer_id="voron", state="standby", **status):
    """Build a /api/printers/<id>/status body with every defaulted key present."""
    base = {
        "state": state,
        "filename": "",
        "progress": None,
        "hotend_c": 25.0,
        "bed_c": 24.0,
        "hotend_target_c": None,
        "bed_target_c": None,
        "layer": None,
        "total_layers": None,
        "remaining_min": None,
        "current_object": None,
        "objects": [],
        "excluded_objects": [],
    }
    base.update(status)
    return {"ok": True, "id": printer_id, "name": printer_id, "status": base}
