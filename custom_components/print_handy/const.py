"""Constants for Print Handy."""

from homeassistant.const import Platform

NAME = "Print Handy"
DOMAIN = "print_handy"
VERSION = "0.1.0"
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

# Print Handy's Web UI status API (the one read surface it exposes today).
STATUS_PATH = "/api/status"
HEALTHZ_PATH = "/healthz"
TOKEN_HEADER = "X-WebUI-Token"

ATTR_ATTRIBUTION = "attribution"
