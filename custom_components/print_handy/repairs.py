"""Repairs issues for failing Print Handy doctor checks (issue #14).

Home Assistant's Repairs list is where a user looks for "something is
wrong with an integration". The doctor rows in the hub snapshot are
exactly that signal, so each failing check raises one Repairs issue
while it fails and clears it when the check passes again. Warnings do
not raise issues: they stay on the doctor binary sensors, where a
user who cares can see them without the Repairs list filling up.

The sync runs on every coordinator update. Issue ids are prefixed
with the config entry id so two Print Handy instances never collide.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import PrintHandyDataUpdateCoordinator

if TYPE_CHECKING:
    # Typing-only: this module is imported by the package __init__
    # itself, so the entry type cannot be imported at runtime here.
    from . import PrintHandyConfigEntry


def _prefix(entry: PrintHandyConfigEntry) -> str:
    return f"{entry.entry_id}_doctor_"


@callback
def async_sync_doctor_repairs(
    hass: HomeAssistant,
    entry: PrintHandyConfigEntry,
    coordinator: PrintHandyDataUpdateCoordinator,
) -> None:
    """Make the Repairs list match the latest snapshot's doctor rows."""
    if coordinator.data is None:
        return
    prefix = _prefix(entry)
    failing = {
        check.slug: check
        for check in coordinator.data.instance.doctor_checks
        if check.status == "fail"
    }
    registry = ir.async_get(hass)
    for (domain, issue_id), _issue in list(registry.issues.items()):
        if (
            domain == DOMAIN
            and issue_id.startswith(prefix)
            and issue_id[len(prefix) :] not in failing
        ):
            ir.async_delete_issue(hass, DOMAIN, issue_id)
    for slug, check in failing.items():
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{prefix}{slug}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="doctor_check_failed",
            translation_placeholders={
                "check": check.label,
                "detail": check.detail,
            },
        )
