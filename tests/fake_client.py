"""A scripted stand-in for PrintHandyApiClient, driven by a mutable World."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from custom_components.print_handy.model import (
    parse_printer_status,
    parse_snapshot,
    parse_spools_view,
    unavailable_spools,
)
from tests.fixtures import MOONRAKER_ROW, printer_body, status_body
from tests.test_spool_model import SPOOLS_BODY

CLIENT_PATH = "custom_components.print_handy.coordinator.PrintHandyApiClient"
CLOCK_PATH = "custom_components.print_handy.coordinator._monotonic"


@dataclass
class World:
    """What the fake server currently answers, plus call counters."""

    rows: list[dict] = field(default_factory=lambda: [MOONRAKER_ROW])
    printers: dict[str, dict] = field(default_factory=dict)
    doctor: list[dict] | None = None
    camera_images: dict[str, bytes] = field(default_factory=dict)
    camera_error: Exception | None = None
    hub_error: Exception | None = None
    status_error: Exception | None = None
    spoolman_ok: bool | None = True
    spools_body: dict | None = field(default_factory=lambda: SPOOLS_BODY)
    hub_calls: int = 0
    status_calls: int = 0
    spool_calls: int = 0
    controls: list[tuple] = field(default_factory=list)
    control_error: Exception | None = None


class FakeClient:
    """Constructed by the coordinator; reads everything from the World."""

    world: World

    def __init__(self, session=None, base_url="", token=""):
        """Initialize."""
        self.base_url = "http://ph.local:8088"

    async def async_get_status(self):
        """Async get status."""
        w = self.world
        w.hub_calls += 1
        if w.hub_error is not None:
            raise w.hub_error
        return parse_snapshot(
            status_body(*w.rows, spoolman_ok=w.spoolman_ok, doctor=w.doctor)
        )

    async def async_get_camera_snapshot(self, printer_id):
        """Async get camera snapshot."""
        w = self.world
        if w.camera_error is not None:
            raise w.camera_error
        return w.camera_images[printer_id]

    async def async_get_printer_statuses(self, printer_ids):
        """Async get printer statuses."""
        w = self.world
        w.status_calls += 1
        if w.status_error is not None:
            raise w.status_error
        return {
            pid: parse_printer_status(w.printers.get(pid) or printer_body(pid), pid)
            for pid in printer_ids
        }

    async def async_get_printer_spool_views(self, printer_ids):
        """Async get printer spool views."""
        w = self.world
        w.spool_calls += 1
        if w.spools_body is None:
            return {pid: unavailable_spools(pid, "none") for pid in printer_ids}
        return {pid: parse_spools_view(w.spools_body, pid) for pid in printer_ids}

    async def async_printer_control(self, printer_id, action):
        """Async printer control."""
        self.world.controls.append((printer_id, action))
        if self.world.control_error is not None:
            raise self.world.control_error

    async def async_set_printer_target(self, printer_id, **kwargs):
        """Async set printer target."""
        self.world.controls.append((printer_id, "target"))

    async def async_send_gcode(self, printer_id, script):
        """Async send gcode."""
        self.world.controls.append((printer_id, "gcode", script))

    async def async_exclude_object(self, printer_id, object_name):
        """Async exclude object."""
        self.world.controls.append((printer_id, "exclude", object_name))

    async def _spool(self, *args: Any):
        self.world.controls.append(args)
        return {"ok": True}

    async def async_assign_spool(self, printer_id, slot, spool_id):
        """Async assign spool."""
        return await self._spool(printer_id, "assign", slot, spool_id)

    async def async_swap_spools(self, printer_id, a, b):
        """Async swap spools."""
        return await self._spool(printer_id, "swap", a, b)

    async def async_unassign_spool(self, printer_id, slot):
        """Async unassign spool."""
        return await self._spool(printer_id, "unassign", slot)

    async def async_add_spool(self, printer_id, **kwargs):
        """Async add spool."""
        return await self._spool(printer_id, "add")

    async def async_sync_spools(self, printer_id):
        """Async sync spools."""
        return await self._spool(printer_id, "sync")


def patched(world: World):
    """Patch the coordinator's client class to serve ``world``."""
    FakeClient.world = world
    return patch(CLIENT_PATH, FakeClient)


class Clock:
    """A controllable monotonic clock for the cadence tests."""

    def __init__(self) -> None:
        """Initialize."""
        self.now = 1_000.0

    def __call__(self) -> float:
        """Call."""
        return self.now

    def advance(self, seconds: float) -> None:
        """Advance."""
        self.now += seconds
