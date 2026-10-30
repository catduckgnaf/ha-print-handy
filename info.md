# Print Handy Integration

Home Assistant custom component for **Print Handy** — every printer Print Handy manages (Klipper/Moonraker, OctoPrint, Bambu Lab, PrusaLink, Elegoo SDCP, stock Flashforge and more) in **one** integration, instead of a separate integration per printer.

## Features:
- 🖨️ **One device per printer** on your Print Handy roster, grouped under the Print Handy instance.
- 💚 **Online status** per printer from Print Handy's listener health, plus a **Connection** diagnostic (how Print Handy reaches the printer).
- 🩺 **Instance health**: overall status, Print Handy version, doctor failure count, bot and Spoolman connectivity.
- 🔒 **Read-only** and local: polls the Print Handy Web UI status API on your network; the access token is sent only as a header and never displayed.

## Setup
1. Enable the Web UI in Print Handy (`[webui] enabled = true` with a port reachable from Home Assistant).
2. Search for `Print Handy` in **Settings** > **Devices & Services** > **Add Integration**.
3. Enter the Web UI URL and, unless it is a loopback install without one, the Web UI access token.

Print progress, temperatures and controls arrive with the per-printer live status endpoint designed in the repo's DESIGN.md.
