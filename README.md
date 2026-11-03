# Print Handy for Home Assistant

A Home Assistant custom integration for [Print Handy](https://github.com/catduckgnaf/print-handy) — every printer Print Handy manages, in one integration, instead of a separate Moonraker, OctoPrint or vendor integration per printer.

## What it does today (0.4.0)

The integration polls the Print Handy Web UI's status API (`GET /api/status` for the instance and roster, `GET /api/printers/<id>/status` for each printer's live status, `GET /api/printers/<id>/spools` for each printer's spool assignments) and creates:

- **One device per printer** on the Print Handy roster:
  - **Current state** sensor — `standby` / `printing` / `paused` / `complete` / `error` / `cancelled` / `unknown`, plus `unavailable` when Print Handy cannot read the printer (with the reason as an attribute).
  - **Print progress** (%) and **Filename** sensors.
  - **Nozzle temperature** and **Bed temperature** sensors, plus **Nozzle target temperature** / **Bed target temperature** sensors on printers whose adapter reports targets (Bambu, Elegoo SDCP, PrusaLink).
  - **Pause**, **Resume** and **Cancel** buttons (phase 3) — one set per printer, acting only on that printer.
  - **Nozzle target temperature** / **Bed target temperature** numbers (phase 3) — writable versions of the target sensors, created only on printers whose adapter reports targets; the read-only sensors stay for history and cards.
  - **Estimated finish time** (timestamp) and **Time remaining** sensors on printers that report remaining time.
  - **Current layer** / **Total layers** sensors where the adapter reports layers (Bambu, Elegoo SDCP).
  - **Filament** sensor on Bambu printers with an AMS — the active tray's filament name, with color, material, remaining % and the full tray list as attributes.
  - **Slot 1–4 spool** sensors (0.4.0, Print Handy #789) — one per slot on printers whose Print Handy serves the spools endpoint: the assigned spool's name, with spool id, material, color, brand and remaining weight / percent as attributes (percent only where the spool's starting weight is recorded, never estimated). An empty slot reads as no state with a null spool id. Any slot conflicts Print Handy names ride as a `conflicts` attribute.
  - **Printing** and **Printing error** binary sensors, for conditional cards and automations.
  - **Online** binary sensor — on while Print Handy's listener reports the printer healthy.
  - **Connection** diagnostic sensor — how Print Handy reaches the printer (`moonraker`, `octoprint`, `bambulab_lan`, ...), with model, vendor and listener detail as attributes.
- **One Print Handy instance device**:
  - **Status** sensor (`ok` / `problem`), **Version** and **Doctor failures** diagnostic sensors.
  - **Bot connected** and **Spoolman connected** connectivity binary sensors.

Entity ids use one fixed suffix vocabulary per printer (`_current_state`, `_print_progress`, `_filename`, `_nozzle_temperature`, `_bed_temperature`, `_estimated_finish_time`, `_current_layer`, `_slot_1_spool` … `_slot_4_spool`, ...), so dashboards and cards can hard-code them safely — see [DESIGN.md](DESIGN.md).

## Spool services (0.4.0)

Five services manage spools through the same Print Handy spool API the Web UI Spools page uses (Print Handy #789) — Spoolman owns the spool records, Print Handy owns the slot assignment, and this integration stores no spool state beyond its poll:

- `print_handy.assign_spool` — fields: `printer_id`, `slot` (1–4), `spool_id`. A spool already in the slot goes back on the shelf.
- `print_handy.swap_spools` — fields: `printer_id`, `slot_a`, `slot_b`.
- `print_handy.unassign_spool` — fields: `printer_id`, `slot`. The spool goes back on the shelf.
- `print_handy.add_spool` — fields: `printer_id`, `material`, `color`, `grams`, optional `brand` and `hex`. Creates one spool on the shelf; load it with `assign_spool` afterwards.
- `print_handy.sync_spools` — fields: `printer_id`. Reconciles that printer's slot state with Spoolman.

The spool writes sit behind the same Print Handy controls gate as the phase-3 controls (off by default there). A refusal — controls disabled, unknown spool or slot, a conflict — fails the service call visibly in Home Assistant with Print Handy's reason; nothing fails silently. The API's spool-record `edit` action is not exposed as a service in 0.4.0; edit spool records in Print Handy's Web UI Spools page or in Spoolman.

Not yet available: print thumbnails (Print Handy has no per-printer thumbnail source yet — tracked on the Print Handy side).

Requires a Print Handy build with the per-printer status endpoint (Print Handy PR #667, for the first release after 2.2.1). Against an older Print Handy the roster and health entities above still work; the live job entities read `unavailable`.

The phase-3 controls additionally require a Print Handy build with the control API (Print Handy issue #708) **and** controls enabled on the Print Handy side — they are off by default there, behind the operator's own setting. Against a Print Handy without the control API, or with controls disabled, the buttons and numbers are still created but every press or set fails visibly in Home Assistant with Print Handy's refusal; nothing fails silently.

The spool sensors and services additionally require a Print Handy build with the spool API (Print Handy issue #789). Against an older Print Handy the slot spool sensors are not created and the rest of the integration works unchanged; the spool services fail visibly with Print Handy's refusal. The spool writes share the controls gate above, so they also need controls enabled on the Print Handy side.

## Installation

### HACS (custom repository)

1. In HACS, open the menu in the top right and choose **Custom repositories**.
2. Add `https://github.com/catduckgnaf/ha-print-handy` with category **Integration**.
3. Install **Print Handy** from HACS and restart Home Assistant.

### Manual

Copy `custom_components/print_handy/` into your Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Configuration

1. In Print Handy, make sure the **Web UI is enabled** (`[webui] enabled = true` with a port, reachable from Home Assistant). If the Web UI binds beyond loopback, Print Handy requires an access token — it is the value of the `WEBUI_ACCESS_TOKEN` environment variable on the Print Handy side.
2. In Home Assistant: **Settings → Devices & services → Add integration → Print Handy**.
3. Enter the Web UI URL (for example `http://print-handy.local:8080`) and the access token (leave the token empty only for a loopback install with no token configured).
4. Options let you change the polling interval (default 30 seconds).

## Notes

- Polling is read-only; commands are sent only when you press a control button, set a target temperature or call a spool service, and only to Print Handy (never directly to a printer). Print Handy decides whether to honor them — its controls gate is off by default.
- The token is stored only in the config entry and sent only as the `X-WebUI-Token` header.
- Print Handy's optional MQTT events (`print-handy/<event>`) work unchanged alongside this integration for automations that only need print started/finished notifications today.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
