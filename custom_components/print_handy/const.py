"""Constants for Print Handy."""

from homeassistant.const import Platform

NAME = "Print Handy"
DOMAIN = "print_handy"
VERSION = "0.5.1"
ATTRIBUTION = "Data provided by Print Handy"
ISSUE_URL = "https://github.com/catduckgnaf/ha-print-handy/issues"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SENSOR,
]

# Configuration constants
CONF_TOKEN = "token"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_SCAN_INTERVAL = 30
REQUEST_TIMEOUT = 15

# Print Handy's Web UI status API.
STATUS_PATH = "/api/status"
PRINTER_STATUS_PATH = "/api/printers/{printer_id}/status"
HEALTHZ_PATH = "/healthz"
TOKEN_HEADER = "X-WebUI-Token"

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
