# Print Handy for Home Assistant

A Home Assistant custom integration for [Print Handy](https://github.com/catduckgnaf/print-handy) — every printer Print Handy manages, in one integration, instead of a separate Moonraker, OctoPrint or vendor integration per printer.

## What it does today (0.1.0)

The integration polls the Print Handy Web UI's status API (`GET /api/status`) and creates:

- **One device per printer** on the Print Handy roster:
  - **Online** binary sensor — on while Print Handy's listener reports the printer healthy.
  - **Connection** diagnostic sensor — how Print Handy reaches the printer (`moonraker`, `octoprint`, `bambulab_lan`, ...), with model, vendor and listener detail as attributes.
- **One Print Handy instance device**:
  - **Status** sensor (`ok` / `problem`), **Version** and **Doctor failures** diagnostic sensors.
  - **Bot connected** and **Spoolman connected** connectivity binary sensors.

Print state, progress, temperatures, ETA and print controls are **not** in 0.1.0: Print Handy's status API does not publish live per-printer job data yet. [DESIGN.md](DESIGN.md) records the gap precisely, the entity model planned against the Moonraker integration's parity bar, and the small status endpoint proposed for Print Handy itself that unlocks it.

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
