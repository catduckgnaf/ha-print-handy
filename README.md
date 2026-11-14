# Print Handy for Home Assistant

A Home Assistant custom integration for [Print Handy](https://github.com/catduckgnaf/print-handy) — every printer Print Handy manages, in one integration, instead of a separate Moonraker, OctoPrint or vendor integration per printer.

## Supported versions

Developed and tested against Print Handy 2.2.7. Print Handy 2.2.2 is the oldest release with the per-printer status endpoint this integration needs; older releases are not supported. The Emergency stop, restart, `send_gcode` and `exclude_object` features need a build that has those routes (see below). Newer releases are expected to keep working because the API only grows. Home Assistant 2024.11 or newer is required.

## What it does today (0.6.1)

The integration polls Print Handy's Web UI API at three cadences, because `GET /api/status` makes the server run its whole self-check:

- `GET /api/status` (roster, versions, service health, doctor rows): once at startup and then every 5 minutes (option: *Instance status interval*). A slow or failed refresh keeps the last roster, makes only the instance-level entities unavailable and retries after a minute; printers stay available while their own polls succeed.
- `GET /api/printers/<id>/status` (live status of each printer): every 30 seconds (option: *Printer polling interval*).
- `GET /api/printers/<id>/spools` (spool assignments): every 2 minutes, and immediately after a spool service call.

A 429 reply (Print Handy locks an address out after repeated wrong tokens) makes the integration stop polling for the `Retry-After` wait instead of hammering the server. A 403 reply means the server refuses the host name in the URL (see Configuration). The integration creates:

- **One device per printer** on the Print Handy roster:
  - **Current state** sensor — `standby` / `printing` / `paused` / `complete` / `error` / `cancelled`. A state string Print Handy sends that is not in this list reads `unknown`, with the raw value in a `raw_state` attribute. When Print Handy cannot read the printer, every live entity of that printer is unavailable (Home Assistant's own state), not a made-up value.
  - **Print progress** (%) and **Filename** sensors.
  - **Nozzle temperature** and **Bed temperature** sensors, plus **Nozzle target temperature** / **Bed target temperature** sensors on printers whose adapter reports targets (Bambu, Elegoo SDCP, PrusaLink).
  - **Pause**, **Resume** and **Cancel** buttons — one set per printer, acting only on that printer.
  - **Emergency stop**, **Firmware restart** and **Moonraker restart** buttons — only on printers Print Handy reaches through Moonraker (every other family answers 501 on the server, so no button is created). The Moonraker restart restarts Moonraker, not Print Handy; its entity key is still `server_restart`. Home Assistant buttons have no confirmation step, so **Cancel, Emergency stop and both restarts are disabled by default**: enable them from the device's entity list. Entities you already enabled on 0.5.x stay enabled. Moonraker-only buttons that 0.5.x created for other printer families are removed.
  - **Current object** sensor (0.5.0, issue #1) — the object currently printing, with the full object list and the excluded objects as attributes, so cards and automations can see every object's name. Object data only exists while a print runs, so the sensor is created the first time a poll reports it (a printer idle at startup gains it when its print starts); printers that never report objects gain no sensor.
  - **Nozzle target temperature** / **Bed target temperature** numbers (phase 3) — writable versions of the target sensors, created only on printers whose adapter reports targets; the read-only sensors stay for history and cards.
  - **Estimated finish time** (timestamp) and **Time remaining** sensors on printers that report remaining time.
  - **Current layer** / **Total layers** sensors where the adapter reports layers (Bambu, Elegoo SDCP).
  - **Filament** sensor on Bambu printers with an AMS — the active tray's filament name, with color, material, remaining % and the full tray list as attributes.
  - **Slot 1–4 spool** sensors (0.4.0, Print Handy #789) — one per slot on printers whose Print Handy serves the spools endpoint: the assigned spool's name, with spool id, material, color, brand and remaining weight / percent as attributes (percent only where the spool's starting weight is recorded, never estimated). An empty slot reads as no state with a null spool id. Any slot conflicts Print Handy names ride as a `conflicts` attribute.
  - **Camera** entity (0.6.1, issue #14) — on printers whose roster row carries a camera snapshot URL: a still camera served through Print Handy's snapshot proxy (`GET /api/printers/<id>/camera/snapshot`), fetched on demand, never polled. Cameras the roster does not signal (for example a Bambu printer's built-in camera, which Print Handy reaches through its adapter) gain no entity until the roster carries a camera field for them.
  - **Faults** sensor (0.6.1, issue #14) — on Bambu printers: the count of active HMS faults, with the fault codes, their descriptions and the printer's own error text as attributes. Other families send no fault data and gain no sensor.
  - **Slot spool weight** sensors (0.6.1, issue #14) — one per slot whose spool records a remaining weight: the weight in grams as a number, next to the slot spool sensor's name state.
  - **Low filament** binary sensors (0.6.1, issue #14) — one per slot with a spool: on while the spool's recorded remaining percent is at or below 10%, or, where only grams are recorded, at or below 50 g.
  - **Printing** and **Printing error** binary sensors, for conditional cards and automations.
  - **Online** binary sensor — on while Print Handy's listener reports the printer healthy.
  - **Connection** diagnostic sensor — how Print Handy reaches the printer (`moonraker`, `octoprint`, `bambulab_lan`, ...), with model, vendor and listener detail as attributes.
- **One Print Handy instance device**:
  - **Status** sensor (`ok` / `problem`), **Version** and **Doctor failures** diagnostic sensors.
  - **Ready** binary sensor (0.6.1, issue #14) — on while the instance snapshot reads ok.
  - **One binary sensor per doctor check** (0.6.1, issue #14) — a problem sensor that turns on while that check fails, with the check's raw status and the server's detail text as attributes. A failing check also raises a **Repairs issue** in Home Assistant while it fails and clears it when the check passes again; warnings stay on the binary sensors and do not raise issues.
  - **Bot connected** and **Spoolman connected** connectivity binary sensors. Spoolman connected reads unknown when Spoolman is not configured, and off only when a configured Spoolman is down.

The integration also fires a **`print_handy_print_finished`** event (0.6.1, issue #14) when a printer's live state moves from printing or paused to complete, with `printer_id`, `printer_name`, `filename` and the config entry's `entry_id` as event data — the hook for "tell me when the print is done" automations.

Entity ids use one fixed suffix vocabulary per printer (`_current_state`, `_print_progress`, `_filename`, `_nozzle_temperature`, `_bed_temperature`, `_estimated_finish_time`, `_current_layer`, `_current_object`, `_slot_1_spool` … `_slot_4_spool`, `_slot_1_spool_weight` … and `_slot_1_low_filament` …, `_camera`, `_faults`, plus button suffixes `_pause`, `_resume`, `_cancel`, `_emergency_stop`, `_firmware_restart`; the Moonraker restart is `_moonraker_restart` on new installs and keeps its old `_server_restart` id where it already exists), so dashboards and cards can hard-code them safely — see [DESIGN.md](DESIGN.md).

## Control services

**Security: `send_gcode` hands the text you give it to the printer.** Print Handy enforces a temperature ceiling and refuses in unsafe states, and its controls are off by default, but a G-code command can still start a print from the printer's storage or move the machine. Restrict who can call this service (administrators and trusted automations only) and never build the script from untrusted input.

All services take the printer either as a device target (pick the printer's device) or as the free-text Print Handy `printer_id` field, which still works. They act only on the printers they name, never on a default or fallback printer:

- `print_handy.send_gcode` — fields: `printer_id` (or a device target), `script`. Sends the G-code script text to that printer. Print Handy and the printer decide whether to honor it; a refusal fails the call visibly with Print Handy's reason.
- `print_handy.exclude_object` — fields: `printer_id` (or a device target), `object`. Moonraker printers only. Excludes the named object (use the names on the **Current object** sensor) from the print running on that printer. Excluding the last remaining object is refused by Print Handy — its reason, carried into the failed call, says to cancel the print instead. An unknown object name is refused the same way.

## Spool services (0.4.0)

Five services manage spools through the same Print Handy spool API the Web UI Spools page uses (Print Handy #789) — Spoolman owns the spool records, Print Handy owns the slot assignment, and this integration stores no spool state beyond its poll:

- `print_handy.assign_spool` — fields: `printer_id`, `slot` (1–4), `spool_id`. A spool already in the slot goes back on the shelf.
- `print_handy.swap_spools` — fields: `printer_id`, `slot_a`, `slot_b`.
- `print_handy.unassign_spool` — fields: `printer_id`, `slot`. The spool goes back on the shelf.
- `print_handy.add_spool` — fields: `printer_id`, `material`, `color`, `grams`, optional `brand` and `hex`. Creates one spool on the shelf; load it with `assign_spool` afterwards.
- `print_handy.sync_spools` — fields: `printer_id`. Reconciles that printer's slot state with Spoolman.

The spool writes sit behind the same Print Handy controls gate as the phase-3 controls (off by default there). A refusal — controls disabled, unknown spool or slot, a conflict — fails the service call visibly in Home Assistant with Print Handy's reason; nothing fails silently. The API's spool-record `edit` action is not exposed as a service in 0.4.0; edit spool records in Print Handy's Web UI Spools page. Spoolman's own UI is a second option only for installs that run an external Spoolman — the Spoolman bundled with Print Handy has no web UI (see [Dashboards: opening spools](#dashboards-opening-spools)).

Not yet available: print thumbnails (Print Handy has no per-printer thumbnail source yet — tracked on the Print Handy side).

Requires a Print Handy build with the per-printer status endpoint (Print Handy PR #667, for the first release after 2.2.1). Against an older Print Handy the roster and health entities above still work; the live job entities read `unavailable`.

The phase-3 controls additionally require a Print Handy build with the control API (Print Handy issue #708) **and** controls enabled on the Print Handy side — they are off by default there, behind the operator's own setting. Against a Print Handy without the control API, or with controls disabled, the buttons and numbers are still created but every press or set fails visibly in Home Assistant with Print Handy's refusal; nothing fails silently.

The 0.5.0 remainder (the Emergency stop / restart buttons, the `send_gcode` and `exclude_object` services, and the Current object sensor) additionally requires the companion Print Handy build that adds those endpoints (`POST /api/printers/<id>/emergency_stop|firmware_restart|server_restart|gcode|exclude`, plus `current_object` / `objects` / `excluded_objects` in the per-printer status) — see the pull request description for the companion Print Handy PR. Against an older Print Handy the new buttons and services fail visibly with Print Handy's refusal and the Current object sensor is not created; the rest of the integration works unchanged. The new buttons and services sit behind the same controls gate, so they also need controls enabled on the Print Handy side.

The spool sensors and services additionally require a Print Handy build with the spool API (Print Handy issue #789). Against an older Print Handy the slot spool sensors are not created and the rest of the integration works unchanged; the spool services fail visibly with Print Handy's refusal. The spool writes share the controls gate above, so they also need controls enabled on the Print Handy side.

## Dashboards: opening spools

To open spools from a Home Assistant dashboard, you have two supported routes:

- **Native cards** built from the slot spool sensors (`_slot_1_spool` … `_slot_4_spool`) — the spool name, material, color and remaining weight are already attributes on those sensors.
- **A webpage card or panel** pointing at the Print Handy Web UI Spools page: `<Web UI base URL>/spools`. Use the Web UI URL you configured for this integration (see [Configuration](#configuration) — for example, `http://print-handy.local:8080/spools` if that is your Web UI URL); it is also the URL shown as the Print Handy device's configuration URL in Home Assistant, with `/spools` added. Editing spool records and the full spool flows live on that page.

Do **not** point a card at Spoolman itself. The Spoolman bundled with Print Handy is API-only and has no web UI (Print Handy #214): a browser opening its root — or the `spoolman_url` from Print Handy's status payload, which for the bundled instance is the container-internal `http://127.0.0.1:8000` — gets only the one line "Spoolman is managed by Print Handy, use /api/v1." That address is the API endpoint Print Handy talks to, not a dashboard target. If an existing dashboard card points there, repoint it at the Web UI Spools page URL above — a one-line URL change.

An install that runs its own external Spoolman (`PRINT_HANDY_BUNDLED_SPOOLMAN=false` on the Print Handy side) is different: that Spoolman does have its own web UI, and dashboard cards may point at it as usual.

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
3. Enter the Web UI URL and the credential. Use the server's **IP address** or a local host name: while the Web UI has no password, Print Handy refuses requests that use a public-looking host name (anything with a dot that is not `.local`, `.lan`, `.home`, `.internal` or `.home.arpa`), and the setup form says so. The credential field takes either the Web UI **access token** (`WEBUI_ACCESS_TOKEN`) or the Web UI **password**; both work in the same header. A Print Handy install with no password is open on the network by design, so the field is simply optional there.
4. Options change the printer polling interval (default 30 seconds) and the instance status interval (default 300 seconds).
5. To change the URL or credential later, use **Reconfigure** on the integration entry. Entity and device ids hang off a stable instance id, not the URL, so switching from a host name to an IP address or from http to https keeps every entity and its history. If the credential stops working, Home Assistant asks for a new one (it may be left empty for a server that went back to open).

### Upgrading from 0.5.x

The config entry migrates itself on the first start: entity and device registry rows are rewritten in place, so entity ids, history and automations are kept. Entity names now come from translations and icons from `icons.json`.

## Notes

- Polling is read-only; commands are sent only when you press a control button, set a target temperature or call a service (spool, `send_gcode` or `exclude_object`), and only to Print Handy (never directly to a printer). Print Handy decides whether to honor them — its controls gate is off by default.
- The access token or password is stored only in the config entry and sent only as the `X-WebUI-Token` header. Diagnostics downloads redact the URL, token and server host name.
- Temperature number limits are the fixed 0-350 (nozzle) and 0-150 (bed) UI bounds; Print Handy's status payload does not expose per-printer limits, and Print Handy and the printer refuse out-of-range values.
- Print Handy's optional MQTT events (`print-handy/<event>`) work unchanged alongside this integration for automations that only need print started/finished notifications today.

## Support and community

Questions and chat live in the **Print Handy Discord server**: [discord.gg/mT2Ht3nqZu](https://discord.gg/mT2Ht3nqZu) — this integration has its own **#print-handy** channel there (under HA-Integrations). Bugs still belong in this repo's issues, where they can be tracked.

## License

GNU Affero General Public License v3.0 only (AGPL-3.0-only) — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
