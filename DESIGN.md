# Design — Print Handy for Home Assistant

Status: 0.2.0 shipped the phase-2 job-level entities, built on the
per-printer status endpoint that landed in Print Handy (issue #664, PR
#667). This document records what Print Handy actually exposes (verified
against its source at 2.2.1, main `93b5144`, plus the merged #667 endpoint),
the transport decision that follows from it, the entity model, and the
parity bar set by the existing Moonraker integration.

## 1. What Print Handy exposes today (Stage 1 recon)

### 1.1 HTTP: the Web UI status API

Print Handy's only machine-readable HTTP surface is its Web UI
(`print_handy/webui/server.py`), and the only data route is:

- `GET /api/status` → the snapshot from `print_handy/webui/status.py`:
  `{version, host, ok, printer{model,name,vendor,connection}, bot{platform,
  mode, ok, heartbeat}, listener{...}, printers[roster], spoolman{...},
  services{}, doctor[]}`.
- `GET /healthz` (unauthenticated by design, for the container healthcheck).
- Everything else is the human config UI: `GET /`, `/config`, `/designs`,
  `/layers`, and POSTs only for `/login`, `/logout`, `/config/preview`,
  `/config` (settings save) and `/debug`. There are **no print-control
  endpoints** — the server's own docstring states it offers no pause or
  exclude control. `print_handy/bot/rest.py` is outbound Discord REST, not
  a Print Handy API.

Auth: the access token lives in the environment variable `[webui]
access_token_env` names (default `WEBUI_ACCESS_TOKEN`) and is sent as the
`X-WebUI-Token` header (constant-time compared). When the Web UI binds
beyond loopback a token is mandatory; on loopback it is optional. A login
session cookie exists for browsers. The Web UI is off by default
(`[webui] enabled = false`, port 0, host 127.0.0.1); a deployment must
enable it for this integration to connect.

**Critical content limit (0.1.0):** the snapshot carries **no live print
data at all** — no per-printer print state, progress, temperatures, ETA or
current file, for any printer. What it does carry per printer is identity
and health: the roster (`printers[]`: id, name, model, vendor, connection,
default flag, non-secret endpoints) and listener health
(`listener.printers.<id>` = `{required, ok, reason}` on multi-printer
instances; the section's own `{required, ok, reason}` on single-printer
ones), plus instance health (bot heartbeat, Spoolman reachability, doctor
rows). That is exactly what 0.1.0 exposed.

**The endpoint that closed the gap (§4):** `GET /api/printers/<id>/status`
(same `X-WebUI-Token` auth) returns the roster identity plus a `status`
object holding the adapter's normalized status verbatim: the core fields
of §1.3 plus, where the adapter provides them, `hotend_target_c`,
`bed_target_c`, `remaining_min`, `layer`, a total-layer count (Bambu:
`total_layer`; Elegoo SDCP: `total_layers`), and vendor extras (Bambu:
`ams`, `hms_errors`, `print_error_text`, `gcode_state`). An unknown id is
a 404; a known printer whose live read fails answers 200 with
`status.state == "unavailable"` and a `detail`. 0.2.0 polls it once per
roster printer per update, alongside the snapshot.

### 1.2 MQTT: event notifications only

`print_handy/notify.py` (#622) publishes, when `[notify] mqtt_enabled` is
true and `mqtt_host` is set, one packet per event to
`<topic_root>/<kind>` (default root `print-handy`), kind ∈
{print_started, paused, runout, watchdog_offer, complete, failed,
spool_added}. The payload is a **plain-text one-line summary** (first line,
max 240 chars, optionally prefixed `<printer_id>: `). The client is a
hand-rolled MQTT 3.1.1 CONNECT+PUBLISH over a raw socket: QoS 0, **not
retained**, no JSON, no state, no temperatures or progress, no Home
Assistant discovery topics, no subscribe. It is an event feed for
automations, not a state channel — entities cannot be built on it.

### 1.3 The status Print Handy maintains internally (not exposed)

Every printer adapter implements `normalize_status(raw)` → a common dict
(`print_handy/printers/base.py::basic_status` and vendor overrides):

- Klipper/Moonraker family: `{state, filename, progress (0..1), hotend_c,
  bed_c}` from print_stats / virtual_sdcard / extruder / heater_bed.
- OctoPrint: same shape, state mapped from OctoPrint flags.
- Bambu LAN (richest): adds `hotend_target_c`, `bed_target_c`,
  `remaining_min`, `layer`, `total_layer`, AMS tray state, HMS errors.
- Also Elegoo SDCP, PrusaLink, stock Flashforge adapters in the same shape.

States seen across adapters: standby, printing, paused, complete, error,
cancelled, unknown. Print Handy also *controls* printers internally
(Moonraker `POST /printer/print/pause|resume|cancel` via
`print_handy/hermes/print_control.py` and `family_control.py`, behind its
chat confirmation flow) — none of that is reachable over HTTP today.

## 2. Transport decision

**API polling of the Web UI, `iot_class = local_polling`.** It is the only
surface that exists. MQTT is supported *alongside* as a user-side option
(HA's own MQTT integration can already consume the event topics for
automations); this integration does not depend on it, because its payloads
carry no state and are not retained.

Polling cadence: 30 s default, user-adjustable (10–3600 s) in options.
The Moonraker integration's dual-rate pattern (fast while printing, slow
while idle) remains a planned refinement — 0.2.0 keeps the single
configured interval for snapshot and per-printer polls alike.

Config flow: the user enters the **Web UI URL** and the **access token**
(optional, loopback no-token installs only). Validation performs one
`GET /api/status`; 401 → `invalid_auth` (with a reauth flow), unreachable
→ `cannot_connect`, unparsable → `unknown`. Unique id = normalized URL.

## 3. Entity model

### 3.1 Shipped in 0.1.0 (from the real surface)

Hub device "Print Handy" (identifiers `instance:<base_url>`, model
"Print Handy instance", sw_version from the snapshot):

| Entity | Type | Source |
|---|---|---|
| Status | sensor | snapshot `ok` → `ok` / `problem` |
| Version | sensor (diagnostic) | snapshot `version` |
| Doctor failures | sensor (diagnostic) | count of doctor rows with status `fail` |
| Bot connected | binary_sensor (connectivity) | `bot.heartbeat.connected` |
| Spoolman connected | binary_sensor (connectivity) | `spoolman.ok` |

One device per roster printer (`printer:<id>`, manufacturer = vendor,
model = model key, via_device = hub):

| Entity | Type | Source |
|---|---|---|
| Online | binary_sensor (connectivity) | listener row `ok` (None = unknown when no listener is required) |
| Connection | sensor (diagnostic) | roster `connection`; attributes: model, vendor, default, listener_required, listener_reason |

### 3.2 Shipped in 0.2.0 (phase 2, per printer, from the §4 endpoint)

Fixed suffix vocabulary (entity ids end in the key; documented in the
README): `current_state`, `print_progress`, `filename`,
`nozzle_temperature`, `bed_temperature`, `nozzle_target_temperature`,
`bed_target_temperature`, `estimated_finish_time`, `time_remaining`,
`current_layer`, `total_layers`, `filament`, plus binary sensors
`printing` and `printing_error` — alongside 0.1.0's `online` and
`connection`, which are unchanged.

| Entity | Type | Source (endpoint `status`) |
|---|---|---|
| Current state | sensor (enum) | `state`, guarded to the known states + `unavailable`; attributes: `filename`, `detail` |
| Print progress | sensor (%, measurement) | `progress` (0..1) × 100 |
| Filename | sensor | `filename` (blank → no state) |
| Nozzle / Bed temperature | sensor (temperature, measurement) | `hotend_c` / `bed_c` |
| Nozzle / Bed target temperature | sensor (temperature, measurement) | `hotend_target_c` / `bed_target_c` — created only when the first poll carries the field |
| Estimated finish time | sensor (timestamp) | now + `remaining_min`; created only when reported |
| Time remaining | sensor (duration, minutes) | `remaining_min`; same gating |
| Current layer / Total layers | sensor (measurement) | `layer` / `total_layer` or `total_layers`; same gating |
| Filament | sensor | Bambu `ams`: state = active tray's name (else material); attributes: material, color, remaining_pct, slot, unit, full tray list, external spool, filament_backup. Created only when a tray/spool is loaded |
| Printing | binary sensor (running) | `state == "printing"` |
| Printing error | binary sensor (problem) | `state == "error"` |

Deviations from the original phase-2 sketch, each forced by what the
endpoint genuinely sends (named here, not silently dropped):

- **No start-time, print-duration or end-time values, and no
  `start_time`/`end_time`/`duration` attributes on the state sensor** —
  the normalized status carries no start timestamp or elapsed time, so
  there is nothing truthful to publish. Timer cards get the ETA
  timestamp and the remaining-duration sensor instead.
- **Targets are read-only sensors, not numbers** — the endpoint is
  read-only; writable targets are phase-3 control scope (the Moonraker
  integration's target numbers write G-code).
- **The Spool sensor shipped as the AMS Filament sensor above, not a
  Spoolman sensor** — the endpoint carries no Spoolman assignment data
  at all (no spool id, filament name or remaining weight from
  Spoolman); what it does carry is the printer-reported AMS block, so
  the sensor is built on that, following the same grouped-attribute
  pattern. A true Spoolman spool sensor needs Print Handy to add spool
  assignment fields to the endpoint.
- **No thumbnail image entity** — deferred on Print Handy #664: no
  per-printer thumbnail source exists yet.
- **No camera entity** — the design's conditional ("where the roster
  carries a snapshot URL") is not wired: the 0.1.0 model does not parse
  a camera field from the roster, and camera support was outside the
  0.2.0 scope. Revisit with the thumbnail work.

Buttons Pause / Resume / Cancel only when a control API exists
(phase 3) — Print Handy's chat-confirm safety posture means the API
addition needs its own guard design; this integration will not invent
endpoints or bypass it.

## 4. The gap, precisely — closed, except the thumbnail

Nothing in this repo could manufacture the phase-2 entities: the data
existed inside Print Handy (adapter `normalize_status`) but no HTTP
route served it. The draft issue below was filed as Print Handy #664
and implemented by Print Handy PR #667 (`GET /api/printers/<id>/status`,
§1.1); 0.2.0 is built on it. The one part that remains open on the
Print Handy side is the **job thumbnail route** — no per-printer
thumbnail source exists yet (deferred on #664). Original draft, kept
for the record:

> **Web UI API: per-printer live status endpoint**
> The Web UI status snapshot (`GET /api/status`) carries roster identity
> and listener health but no live print data, so external consumers (the
> Home Assistant integration, dashboards) cannot show print state,
> progress, temperatures or ETA for any printer.
> Proposed: `GET /api/printers/<id>/status` (and/or a `status` object per
> roster row) returning the adapter's existing normalized status —
> `{state, filename, progress, hotend_c, bed_c}` plus, where the adapter
> already provides them, `hotend_target_c`, `bed_target_c`,
> `remaining_min` / an ETA basis, `layer`, `total_layer` — read through
> the same per-printer view the listeners use, behind the existing
> `X-WebUI-Token` auth, read-only, no control actions. A job thumbnail
> route (the sliced file's embedded thumbnail Print Handy already
> extracts for chat posts) would complete the dashboard surface.
> Acceptance: multi-printer roster returns each printer's own status;
> offline/unreachable printers read as an explicit unavailable state,
> not an error that fails the whole snapshot.

## 5. Parity matrix — the Moonraker integration bar

Reference: **marcolivierarsenault/moonraker-home-assistant** (HACS,
domain `moonraker`, `local_polling`, client lib `moonraker-api==4.0.0`;
5,516 installs per HA analytics), code-read 2026-10-30 at its 1.13.4
head. Despite the websocket transport it **never subscribes** — it
polls with JSON-RPC `printer.objects.query` calls over one persistent
socket, 30 s at idle and a dynamic 2 s while printing. (Its README
currently carries a "currently unmaintained" banner even though
releases continued into October 2026.) Ours must deliver the
equivalent for **every** printer Print Handy manages — Klipper,
OctoPrint, Bambu, PrusaLink, SDCP, stock Flashforge — from the one
Print Handy connection.

| Feature / entity | Moonraker integration | Print Handy integration |
|---|---|---|
| Printer state sensor | `sensor.<p>_current_state` from print_stats.state | Shipped 0.2.0: `_current_state` enum sensor, incl. the endpoint's `unavailable` |
| Progress | file-relative (Mainsail-style: file_position between the file's gcode start/end bytes), falling back to display_status.progress; % sensor | Shipped 0.2.0: `_print_progress` % sensor from normalized `progress` (the endpoint's figure is already the adapter's best progress) |
| ETA | timestamp sensor: now + projected time left, where the projected total uses a "calculated %" averaging raw progress and filament-used fraction; slicer estimates are separate, unblended sensors | Shipped 0.2.0 where `remaining_min` is reported (Bambu, SDCP, PrusaLink): `_estimated_finish_time` = now + remaining. No calculated-% blend and no slicer-estimate sensors — the endpoint carries neither filament-used nor slicer estimates to blend |
| Print duration / time left | projected-total, time-left, print_duration and slicer-estimate duration sensors | Time remaining shipped 0.2.0 (`_time_remaining` duration sensor). Projected-total, elapsed print-duration and slicer-estimate sensors NOT shipped — the endpoint carries no elapsed time or slicer estimate |
| Filename | sensor, blanked when not printing (as are most job sensors) | Shipped 0.2.0: `_filename` sensor (blank filename reads as no state) |
| Thumbnail | dedicated `Thumbnail` camera entity (HTTP download from Moonraker's file store, image only while printing); webcam is a separate MJPEG camera entity per `server.webcams.list` | Not shipped — thumbnail route deferred on Print Handy #664 (no per-printer source exists) |
| Nozzle / bed temps + targets | Temperature + Power sensors per heater object; **targets exist only as writable Number entities** (via G-code), there are no target sensors | Shipped 0.2.0: actual + target temperature sensors (read-only, so here targets ARE sensors — the inverse of Moonraker); writable targets = phase 3 control scope |
| Chamber temp, fan speeds, speed factor, toolhead position, MCU/system stats | fully object-discovered sensors/numbers from heater_generic, temperature_sensor, fan, gcode_move, mcu and system objects; plus LED lights, output-pin switches/numbers, Moonraker power-device switches, one button per Klipper macro, host-service and machine-update entities | Only where a Print Handy adapter exposes the underlying data; today the normalized shape carries none of it → adapter-level extensions, later phases. Print Handy's own power/light story runs through HA itself, so power-device and LED parity is deliberately not chased |
| Current / total layer | print_stats.info, then virtual_sdcard, then computed from file metadata (object/first-layer height) | Shipped 0.2.0 where provided (Bambu, Elegoo SDCP); Klipper-family normalized status still surfaces neither, so those printers get no layer sensors |
| Filament used / spool | filament_used sensor + history totals; Spoolman support is **only a Spool ID sensor** — no name, material or remaining weight | Shipped 0.2.0 as the AMS Filament sensor from the printer-reported `ams` block (name + color/material/remaining_pct attributes, full tray list) — matches this bar's shape with richer data. No filament-used sensor and no Spoolman spool data: the endpoint carries neither a filament-used figure nor any Spoolman assignment, so the Spoolman-depth sensor (spool id, remaining weight) awaits Print Handy endpoint fields |
| Job queue count / list | Queue State + Jobs-in-queue sensors, Start-queue button; no per-job control | Print Handy has no cross-vendor queue concept today → out of scope until it does |
| Exclude object | **not supported** (never queried) | Not in the bar; Print Handy tracks objects from slicing, so this is a possible phase 3 differentiator, not parity |
| Pause / Resume / Cancel buttons | buttons → /printer/print/* | Phase 3, gated on a Print Handy control API (§3.2) |
| Emergency stop, firmware/server restart buttons | buttons | Phase 3 discussion; Print Handy's confirm-first posture may keep E-stop chat-side |
| Send G-code | `moonraker.send_gcode` service | Phase 3 at earliest; same gate |
| Camera | camera entity from Moonraker webcam config (snapshot/stream URLs) | Not shipped in 0.2.0 — roster camera data is not parsed by the integration's model yet; revisit with the thumbnail work |
| Config flow | host, port (7125), optional API key (32 chars), TLS, printer name; options: polling rate, camera/thumbnail ports, and a "quiet unreachable" mode for printers on smart plugs; no discovery, no reconfigure flow, no unique id | URL + token (§2), unique id set, reauth flow included; options: scan interval (dual-rate in phase 2) |
| Multiple printers | one config entry per printer (per Moonraker) | **Better:** one entry surfaces the whole roster as separate devices |

OctoPrint core integration (home-assistant/core `octoprint`), read on
the dev branch, is broader than its reputation: a fixed sensor set
(current state enum, job percentage, estimated finish + start time as
`timestamp` sensors, current file, file size), dynamic per-tool
actual/target temperature sensors, printing + printing-error binary
sensors, six buttons (resume/pause/stop job, shutdown, reboot, restart),
target-temperature number entities per bed/tool, an MJPEG camera when a
webcam is enabled, and one service (`octoprint.printer_connect`). Its
notable design choice: **no entity carries attributes** — every datum is
its own entity. The Moonraker integration remains the richer bar, but
OctoPrint core sets the floor for buttons and numbers.

### Card-friendliness (from the card survey)

Surveyed: Threedy (archived OctoPrint card, prefix-derived entities),
`kasperlaursen/octoprint-card` (explicit entity slots), the Bambu Lab
integration's bundled cards, Mushroom/button-card/bar-card dashboard
builds around the Moonraker integration, `timer-bar-card`, and
`lovelace-entity-progress-card`. Findings that shape phase 2:

- **One predictable prefix, stable suffixes, forever.** Prefix-deriving
  cards and every dashboard gist hard-code entity IDs like
  `sensor.<printer>_current_state` and `button.<printer>_pause_job`.
  Near-synonym drift (`pause_job` vs `pause_print`, `current_state` vs
  `print_status`) silently breaks them. Phase 2 fixes one suffix
  vocabulary per device and documents it.
- **Separate, correctly typed sensors beat attributes for core
  metrics** — that is what cards actually bind: progress as a numeric
  % sensor, actual *and* target temps as temperature/measurement
  sensors (history feeds graph cards), filament used, current/total
  layer. Nothing reads temperatures out of attributes.
- **Time needs all three forms:** a `timestamp` ETA sensor, a
  `timestamp` start-time sensor, *and* duration remaining/elapsed
  sensors. OctoPrint core dropped its duration sensors and both
  dedicated cards broke on exactly that. As a bonus, carrying
  `start_time`/`end_time`/`duration` attributes on the print-state
  sensor would let generic timer cards work with zero per-integration
  glue — no printer integration does this today.
- **Status = one enum sensor + binary flags:** lowercase enum state
  (Print Handy's normalized states already match) plus
  printing / printing-error binary sensors for conditional cards.
- **Thumbnail and webcam as entities, not attributes:** Bambu's
  `image.<printer>_cover_image` and Moonraker's thumbnail/webcam
  camera entities are the established pattern; cards bind
  picture-entity/picture-glance and the standard `entity_picture`.
- **Attributes are for grouped data, not metrics:** the proven case is
  Bambu's AMS tray sensor — state = filament name, attributes carry
  color, type, remaining, empty, name, nozzle temperature range and
  serial. Print Handy's Spoolman depth maps directly onto this pattern
  for a per-printer spool sensor; the same shape fits an error sensor
  (state = count, attributes = codes/descriptions).

A companion custom card is **phase 2, scoped but not built here**: a
single card listing every Print Handy printer with state, progress bar,
ETA and thumbnail — the multi-printer glance that per-printer cards
cannot give. It would consume exactly the entities above.

## 6. Structure (mirrors catduck's ryobi_gdo)

`custom_components/print_handy/`: `manifest.json` (domain `print_handy`,
config_flow, `local_polling`, hub), `__init__.py` (runtime_data
coordinator, forward platforms), `config_flow.py` (+ reauth + options),
`coordinator.py`, `api.py` (duck-typed session client), `model.py`
(HA-free snapshot parsing, unit-tested without Home Assistant),
`entity.py`, `sensor.py`, `binary_sensor.py`, `strings.json` +
`translations/en.json`. Repo: `hacs.json`, HACS + hassfest + ruff +
pytest workflows, `.ruff.toml`, Apache-2.0.

## 7. Validation status

Local (this VM, no Home Assistant installed): `compileall` clean,
manifest/hacs/strings JSON valid, `ruff check` clean under the
ryobi-style ruleset, 21 unit tests pass — the 9 from 0.1.0 plus 12 for
phase 2 (per-printer status parsing incl. the Bambu full shape, the
SDCP plural `total_layers`, the unavailable body and type guards; AMS
tray helpers incl. the external spool; client per-printer fetch with
token header and URL quoting, 404-as-unavailable, and the bulk poll's
per-printer failure tolerance with 401 propagation).
GitHub CI on the shipped 0.1.0 head: **HACS validation, hassfest, Lint
(ruff) and Python testing all SUCCESS** (the test job installs the real
pytest-homeassistant-custom-component harness on Python 3.14). The
0.2.0 code head (`30ae154`) verified the same way: **all four SUCCESS**.
**Not yet done:** a live load in a real Home Assistant — the first live
check is installing the integration in catduck's HA from the HACS
custom repository and confirming the hub + printer devices appear with
correct states, now including the phase-2 job entities against a Print
Handy build that has the #667 endpoint.
