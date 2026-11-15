"""HA-level tests for issue #19: new services and nozzle sensors."""

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.print_handy.const import DOMAIN  # noqa: E402
from tests.fake_client import CLOCK_PATH, Clock, World, patched  # noqa: E402
from tests.fixtures import printer_body  # noqa: E402

URL = "http://ph.local:8088"

HARDWARE = {
    "source": "klipper",
    "diameter_mm": 0.4,
    "type": "hardened_steel",
    "type_note": "",
    "installed": "2026-11-01",
    "z_offset_mm": -0.12,
    "z_offset_verified": True,
    "temp_offset_c": 5.0,
    "max_c": 320.0,
    "pending": ["bed_mesh"],
    "mesh_stale": False,
    "materials": [
        {"family": "PLA", "nozzle_c": 210.0, "base_nozzle_c": 205.0, "bed_c": 60.0},
        {"family": "PETG", "nozzle_c": 240.0, "base_nozzle_c": 235.0, "bed_c": 80.0},
    ],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


async def _setup(hass, world):
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"url": URL, "token": "secret-token"},
        unique_id="issue19",
    )
    entry.add_to_hass(hass)
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


async def test_rename_archive_shelve_and_swap_nozzle_services(hass: HomeAssistant):
    """The new per-printer services reach the fake client."""
    world = World()
    await _setup(hass, world)
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.services.async_call(
            DOMAIN, "rename_spool",
            {"printer_id": "voron", "spool_id": 7, "nickname": "Kitchen PLA"},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN, "archive_spool",
            {"printer_id": "voron", "spool_id": 7},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN, "archive_spool",
            {"printer_id": "voron", "spool_id": 9, "archived": False},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN, "shelve_spool",
            {"printer_id": "voron", "spool_id": 3},
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN, "swap_nozzle",
            {"printer_id": "voron", "diameter_mm": 0.6, "nozzle_type": "brass"},
            blocking=True,
        )
    assert ("voron", "rename", 7, "Kitchen PLA") in world.controls
    assert ("voron", "archive", 7, True) in world.controls
    assert ("voron", "archive", 9, False) in world.controls
    assert ("voron", "shelve", 3) in world.controls
    assert ("voron", "nozzle", 0.6, "brass") in world.controls


async def test_upload_file_service_reads_a_tmp_file(hass: HomeAssistant, tmp_path):
    """upload_file reads the named file and sends its basename and bytes."""
    world = World()
    await _setup(hass, world)
    target = tmp_path / "benchy.stl"
    target.write_bytes(b"solid benchy")
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.services.async_call(
            DOMAIN, "upload_file", {"file_path": str(target)}, blocking=True
        )
    assert ("upload", "benchy.stl", len(b"solid benchy")) in world.controls


async def test_upload_file_service_accepts_3mf_case_insensitive(hass: HomeAssistant, tmp_path):
    """A .3MF file (upper case) is accepted."""
    world = World()
    await _setup(hass, world)
    target = tmp_path / "part.3MF"
    target.write_bytes(b"3mf-bytes")
    with patched(world), patch(CLOCK_PATH, Clock()):
        await hass.services.async_call(
            DOMAIN, "upload_file", {"file_path": str(target)}, blocking=True
        )
    assert ("upload", "part.3MF", len(b"3mf-bytes")) in world.controls


async def test_upload_file_service_refuses_a_txt_file(hass: HomeAssistant, tmp_path):
    """A .txt file is refused before any upload."""
    world = World()
    await _setup(hass, world)
    target = tmp_path / "notes.txt"
    target.write_bytes(b"hello")
    with patched(world), patch(CLOCK_PATH, Clock()), pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "upload_file", {"file_path": str(target)}, blocking=True
        )
    assert not any(call and call[0] == "upload" for call in world.controls)


async def test_upload_file_service_refuses_a_missing_file(hass: HomeAssistant, tmp_path):
    """A missing file is a named HomeAssistantError."""
    world = World()
    await _setup(hass, world)
    missing = tmp_path / "nope.stl"
    with patched(world), patch(CLOCK_PATH, Clock()), pytest.raises(
        HomeAssistantError
    ) as excinfo:
        await hass.services.async_call(
            DOMAIN, "upload_file", {"file_path": str(missing)}, blocking=True
        )
    assert "nope.stl" in str(excinfo.value)


async def test_upload_file_service_refuses_an_empty_file(hass: HomeAssistant, tmp_path):
    """An empty file is refused."""
    world = World()
    await _setup(hass, world)
    target = tmp_path / "empty.stl"
    target.write_bytes(b"")
    with patched(world), patch(CLOCK_PATH, Clock()), pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "upload_file", {"file_path": str(target)}, blocking=True
        )


async def test_nozzle_sensors_exist_with_hardware(hass: HomeAssistant):
    """With a hardware block, the nozzle sensors and defaults appear."""
    body = printer_body("voron")
    body["hardware"] = HARDWARE
    world = World(printers={"voron": body})
    await _setup(hass, world)
    states = {s.entity_id: s for s in hass.states.async_all("sensor")}
    diameter = states.get("sensor.voron_nozzle_diameter")
    assert diameter is not None, sorted(states)
    assert diameter.state == "0.4"
    assert diameter.attributes.get("material") == "hardened steel"
    assert diameter.attributes.get("source") == "klipper"
    material = states.get("sensor.voron_nozzle_material")
    assert material is not None
    assert material.state == "hardened steel"
    z_offset = states.get("sensor.voron_nozzle_z_offset")
    assert z_offset is not None
    assert float(z_offset.state) == pytest.approx(-0.12)
    # Per-family defaults: find them by entity id fragment.
    pla_nozzle = [s for eid, s in states.items() if "pla" in eid and "nozzle_default" in eid]
    pla_bed = [s for eid, s in states.items() if "pla" in eid and "bed_default" in eid]
    petg_nozzle = [s for eid, s in states.items() if "petg" in eid and "nozzle_default" in eid]
    assert pla_nozzle and float(pla_nozzle[0].state) == pytest.approx(210.0), sorted(states)
    assert pla_bed and float(pla_bed[0].state) == pytest.approx(60.0)
    assert petg_nozzle and float(petg_nozzle[0].state) == pytest.approx(240.0)


async def test_nozzle_sensors_do_not_exist_without_hardware(hass: HomeAssistant):
    """Without a hardware block, no nozzle entities are created."""
    world = World(printers={"voron": printer_body("voron")})
    await _setup(hass, world)
    entity_ids = [s.entity_id for s in hass.states.async_all("sensor")]
    assert not any("nozzle_diameter" in eid for eid in entity_ids), entity_ids
    assert not any("nozzle_material" in eid for eid in entity_ids)
    assert not any("nozzle_z_offset" in eid for eid in entity_ids)
    assert not any("nozzle_default" in eid for eid in entity_ids)
    assert not any("bed_default" in eid for eid in entity_ids)
