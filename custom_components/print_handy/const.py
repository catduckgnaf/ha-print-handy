"""Constants for Print Handy."""

from homeassistant.const import Platform

NAME = "Print Handy"
DOMAIN = "print_handy"
VERSION = "0.2.0"
ATTRIBUTION = "Data provided by Print Handy"
ISSUE_URL = "https://github.com/catduckgnaf/ha-print-handy/issues"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
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

ATTR_ATTRIBUTION = "attribution"
