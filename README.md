# Print Handy for Home Assistant

A Home Assistant custom integration for [Print Handy](https://github.com/catduckgnaf/print-handy) — every printer Print Handy manages, in one integration, instead of a separate Moonraker, OctoPrint or vendor integration per printer.

## What it does today (0.2.0)

The integration polls the Print Handy Web UI's status API (`GET /api/status` for the instance and roster, `GET /api/printers/<id>/status` for each printer's live status) and creates:

- **One device per printer** on the Print Handy roster:
  - **Current state** sensor — `standby` / `printing` / `paused` / `complete` / `error` / `cancelled` / `unknown`, plus `unavailable` when Print Handy cannot read the printer (with the reason as an attribute).
  - **Print progress** (%) and **Filename** sensors.
  - **Nozzle temperature** and **Bed temperature** sensors, plus **Nozzle target temperature** / **Bed target temperature** sensors on printers whose adapter reports targets (Bambu, Elegoo SDCP, PrusaLink).
  - **Estimated finish time** (timestamp) and **Time remaining** sensors on printers that report remaining time.
  - **Current layer** / **Total layers** sensors where the adapter reports layers (Bambu, Elegoo SDCP).
  - **Filament** sensor on Bambu printers with an AMS — the active tray's filament name, with color, material, remaining % and the full tray list as attributes.
  - **Printing** and **Printing error** binary sensors, for conditional cards and automations.
  - **Online** binary sensor — on while Print Handy's listener reports the printer healthy.
  - **Connection** diagnostic sensor — how Print Handy reaches the printer (`moonraker`, `octoprint`, `bambulab_lan`, ...), with model, vendor and listener detail as attributes.
- **One Print Handy instance device**:
  - **Status** sensor (`ok` / `problem`), **Version** and **Doctor failures** diagnostic sensors.
  - **Bot connected** and **Spoolman connected** connectivity binary sensors.

Entity ids use one fixed suffix vocabulary per printer (`_current_state`, `_print_progress`, `_filename`, `_nozzle_temperature`, `_bed_temperature`, `_estimated_finish_time`, `_current_layer`, ...), so dashboards and cards can hard-code them safely — see [DESIGN.md](DESIGN.md).

Not yet available: print thumbnails (Print Handy has no per-printer thumbnail source yet — tracked on the Print Handy side) and print controls (pause/resume/cancel stay behind Print Handy's own chat confirmation flow; a control API is phase 3 in [DESIGN.md](DESIGN.md)).

Requires a Print Handy build with the per-printer status endpoint (Print Handy PR #667, for the first release after 2.2.1). Against an older Print Handy the roster and health entities above still work; the live job entities read `unavailable`.

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

- Read-only: the integration never sends commands to printers or changes Print Handy configuration.
- The token is stored only in the config entry and sent only as the `X-WebUI-Token` header.
- Print Handy's optional MQTT events (`print-handy/<event>`) work unchanged alongside this integration for automations that only need print started/finished notifications today.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
