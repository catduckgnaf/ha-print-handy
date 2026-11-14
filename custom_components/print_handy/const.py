"""Constants for Print Handy."""

from homeassistant.const import Platform

NAME = "Print Handy"
DOMAIN = "print_handy"
VERSION = "0.6.2"
ATTRIBUTION = "Data provided by Print Handy"
ISSUE_URL = "https://github.com/catduckgnaf/ha-print-handy/issues"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.NUMBER,
    Platform.SENSOR,
]

# Fired on the bus when a printer's live state moves from an active
# print state to "complete" (issue #14, five features).
EVENT_PRINT_FINISHED = f"{DOMAIN}_print_finished"

# Configuration constants
CONF_TOKEN = "token"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_HUB_SCAN_INTERVAL = "hub_scan_interval"
# Stable identity of one Print Handy instance inside Home Assistant. It
# is minted once per entry (a random id for new entries, the config entry
# id for entries migrated from 0.5.x) so changing the URL never changes
# an entity or device id.
CONF_INSTANCE_ID = "instance_id"
# The Print Handy server's own instance id (#1400), when it sends one.
# Used to recognize the same server under a new address, never to key
# entities (those stay on CONF_INSTANCE_ID, so nothing is renamed).
CONF_SERVER_ID = "server_id"

# Cadences. /api/status makes the server run its whole self-diagnosis
# (it launches the slicer and probes every service), so it is the slow
# poll; the per-printer status endpoint is cheap and is the fast one.
DEFAULT_SCAN_INTERVAL = 30  # per-printer live status
DEFAULT_HUB_SCAN_INTERVAL = 300  # /api/status: roster, versions, health
DEFAULT_SPOOL_INTERVAL = 120  # spools views (a Spoolman read per printer)
HUB_RETRY_INTERVAL = 60  # retry a failed hub refresh after this long
MIN_SCAN_INTERVAL = 10
MIN_HUB_SCAN_INTERVAL = 60
REQUEST_TIMEOUT = 15

# The estimated-finish sensor only moves when the projection shifts by
# more than this many seconds, so it does not rewrite on every poll.
FINISH_TOLERANCE_SECONDS = 120

# Print Handy's Web UI status API.
STATUS_PATH = "/api/status"
# The cheap roster (Print Handy #1400): configuration only, no doctor
# run and no printer contact. Older servers answer 404.
ROSTER_PATH = "/api/printers"
PRINTER_STATUS_PATH = "/api/printers/{printer_id}/status"
HEALTHZ_PATH = "/healthz"
TOKEN_HEADER = "X-WebUI-Token"

# The camera snapshot proxy (Print Handy #708): one JPEG per request
# for one printer, behind the same token as every other read.
PRINTER_CAMERA_PATH = "/api/printers/{printer_id}/camera/snapshot"

# A slot's spool reads as low on filament at or below either bound:
# the recorded remaining percent when the spool's starting weight is
# known, else the remaining grams on their own (issue #14).
LOW_FILAMENT_PERCENT = 10.0
LOW_FILAMENT_GRAMS = 50.0

# Print Handy's Web UI control API (phase 3; Print Handy #708). Controls
# are gated by an operator setting on the Print Handy side and are OFF
# by default there — a server with controls disabled refuses these
# POSTs, and the client surfaces that refusal as an error.
PRINTER_PAUSE_PATH = "/api/printers/{printer_id}/pause"
PRINTER_RESUME_PATH = "/api/printers/{printer_id}/resume"
PRINTER_CANCEL_PATH = "/api/printers/{printer_id}/cancel"
PRINTER_TARGET_PATH = "/api/printers/{printer_id}/target"
# The issue-#1 remainder (0.5.0): emergency stop and the two restarts
# are button actions behind the same controls gate, each requiring
# the {"confirm": true} body like cancel; send-G-code and
# exclude-object are services (below) on their own paths.
PRINTER_EMERGENCY_STOP_PATH = "/api/printers/{printer_id}/emergency_stop"
PRINTER_FIRMWARE_RESTART_PATH = "/api/printers/{printer_id}/firmware_restart"
PRINTER_SERVER_RESTART_PATH = "/api/printers/{printer_id}/server_restart"
PRINTER_GCODE_PATH = "/api/printers/{printer_id}/gcode"
PRINTER_EXCLUDE_PATH = "/api/printers/{printer_id}/exclude"

# The control actions the button platform exposes, by button key.
CONTROL_PATHS = {
    "pause": PRINTER_PAUSE_PATH,
    "resume": PRINTER_RESUME_PATH,
    "cancel": PRINTER_CANCEL_PATH,
    "emergency_stop": PRINTER_EMERGENCY_STOP_PATH,
    "firmware_restart": PRINTER_FIRMWARE_RESTART_PATH,
    "server_restart": PRINTER_SERVER_RESTART_PATH,
}

# Button actions Print Handy refuses without an explicit confirm body;
# the button press itself is that confirmation.
CONFIRM_ACTIONS = ("cancel", "emergency_stop", "firmware_restart", "server_restart")

# Connection type of the printers the server supports these on. Print
# Handy answers 501 for every other family, so no button is created.
MOONRAKER_CONNECTION = "moonraker"
MOONRAKER_ONLY_ACTIONS = ("emergency_stop", "firmware_restart", "server_restart")

# Fallback UI bounds (°C) for the target-temperature numbers when the
# server does not send its per-heater ceilings (servers before #1400).
DEFAULT_HEATER_MAX = {"nozzle": 350.0, "bed": 150.0}

# Print Handy's Web UI spool API (Print Handy #789). The read surface
# is ``GET /api/printers/<id>/spools``; the write surface is
# ``POST /api/printers/<id>/spools/{assign,swap,unassign,add,edit,sync}``
# behind the same controls gate and token as the control API above.
PRINTER_SPOOLS_PATH = "/api/printers/{printer_id}/spools"
PRINTER_SPOOL_ACTION_PATH = "/api/printers/{printer_id}/spools/{action}"

# The spool actions this integration exposes (the API's ``edit`` and
# purchase actions are not exposed as services in 0.4.0).
SPOOL_ACTIONS = ("assign", "swap", "unassign", "add", "sync")

# Service names (services.yaml documents the fields and selectors).
SERVICE_ASSIGN_SPOOL = "assign_spool"
SERVICE_SWAP_SPOOLS = "swap_spools"
SERVICE_UNASSIGN_SPOOL = "unassign_spool"
SERVICE_ADD_SPOOL = "add_spool"
SERVICE_SYNC_SPOOLS = "sync_spools"

SPOOL_SERVICES = (
    SERVICE_ASSIGN_SPOOL,
    SERVICE_SWAP_SPOOLS,
    SERVICE_UNASSIGN_SPOOL,
    SERVICE_ADD_SPOOL,
    SERVICE_SYNC_SPOOLS,
)

# Issue-#1 remainder services (0.5.0), on the control API above.
SERVICE_SEND_GCODE = "send_gcode"
SERVICE_EXCLUDE_OBJECT = "exclude_object"

CONTROL_SERVICES = (
    SERVICE_SEND_GCODE,
    SERVICE_EXCLUDE_OBJECT,
)

ATTR_ATTRIBUTION = "attribution"
