"""Constants for Print Handy."""

from homeassistant.const import Platform

NAME = "Print Handy"
DOMAIN = "print_handy"
VERSION = "0.3.0"
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

# The control actions the button platform exposes, by button key.
CONTROL_PATHS = {
    "pause": PRINTER_PAUSE_PATH,
    "resume": PRINTER_RESUME_PATH,
    "cancel": PRINTER_CANCEL_PATH,
}

ATTR_ATTRIBUTION = "attribution"
