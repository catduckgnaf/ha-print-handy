"""Diagnostics for Print Handy, with addresses and credentials redacted."""

from __future__ import annotations

import dataclasses
from typing import Any
from urllib.parse import urlparse

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_URL
from homeassistant.core import HomeAssistant

from . import PrintHandyConfigEntry
from .const import CONF_INSTANCE_ID, CONF_SERVER_ID, CONF_TOKEN, VERSION

# Config entry and snapshot fields that hold an address, a host name or a
# credential. ``detail`` strings can embed the printer or service address
# an error mentioned, so they are masked too.
TO_REDACT = {
    CONF_URL,
    CONF_TOKEN,
    CONF_INSTANCE_ID,
    CONF_SERVER_ID,
    "host",
    "spoolman_url",
    "detail",
    "listener_reason",
    "unique_id",
    "title",
}

REDACTED = "**REDACTED**"


def _scrub(text: str | None, secrets: list[str]) -> str | None:
    """Replace any known address fragment inside a free-text error message."""
    if text is None:
        return None
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PrintHandyConfigEntry
) -> dict[str, Any]:
    """Return the entry, the last snapshot and the cadence state, redacted."""
    coordinator = entry.runtime_data
    parsed = urlparse(str(entry.data.get(CONF_URL, "")))
    secrets = [
        str(entry.data.get(CONF_URL, "")),
        parsed.netloc,
        parsed.hostname or "",
    ]
    snapshot = coordinator.data
    return {
        "integration_version": VERSION,
        "entry": async_redact_data(
            {
                "title": entry.title,
                "version": entry.version,
                "data": dict(entry.data),
                "options": dict(entry.options),
            },
            TO_REDACT,
        ),
        "update": {
            "last_update_success": coordinator.last_update_success,
            "last_exception": _scrub(
                str(coordinator.last_exception) if coordinator.last_exception else None,
                secrets,
            ),
            "hub_error": _scrub(coordinator.hub_error, secrets),
            "update_interval_s": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
            "hub_interval_s": coordinator.hub_interval,
            "roster_supported": coordinator.roster_supported,
        },
        "snapshot": (
            async_redact_data(dataclasses.asdict(snapshot), TO_REDACT)
            if snapshot is not None
            else None
        ),
    }
