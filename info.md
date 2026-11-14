# Print Handy Integration

Home Assistant custom component for **Print Handy** — every printer Print Handy manages (Klipper/Moonraker, OctoPrint, Bambu Lab, PrusaLink, Elegoo SDCP, stock Flashforge and more) in **one** integration, instead of a separate integration per printer.

## Features:
- 🖨️ **One device per printer** on your Print Handy roster, grouped under the Print Handy instance.
- 💚 **Online status** per printer from Print Handy's listener health, plus a **Connection** diagnostic (how Print Handy reaches the printer).
- 🩺 **Instance health**: overall status, Print Handy version, doctor failure count, bot and Spoolman connectivity, one binary sensor per doctor check, and a Repairs issue while a doctor check fails.
- 📷 **Camera** stills through Print Handy's snapshot proxy where the roster carries a camera, **Bambu fault** counts with their codes, per-slot **spool weight** sensors with a low-filament alert, and a `print_handy_print_finished` event for automations.
- 🔒 **Local**: talks only to your Print Handy Web UI on your network. Polling is read-only; controls (pause, resume, cancel, Moonraker emergency stop and restarts, target temperatures, spool and G-code services) are sent only when you press or call them, and Print Handy has them off by default. The access token or password is sent only as a header and never displayed.
- 🏠 **Home Assistant OS**: run Print Handy itself as an app from [print-handy-haos-addon](https://github.com/catduckgnaf/print-handy-haos-addon); it follows Print Handy releases automatically.
- ⚠️ **`send_gcode` passes arbitrary G-code to the printer** (it can start a print or move the machine). Restrict who can call it.

## Setup
1. Enable the Web UI in Print Handy (`[webui] enabled = true` with a port reachable from Home Assistant).
2. Search for `Print Handy` in **Settings** > **Devices & Services** > **Add Integration**.
3. Enter the Web UI URL (use the server's IP address) and, if the Web UI has one, the access token or Web UI password.

Requires Print Handy 2.2.2 or newer (tested on 2.2.7) and Home Assistant 2024.11 or newer.

