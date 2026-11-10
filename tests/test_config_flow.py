"""Config flow tests: user, errors, duplicate, reauth, reconfigure."""

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.print_handy import api  # noqa: E402
from custom_components.print_handy.const import DOMAIN  # noqa: E402
from custom_components.print_handy.model import parse_snapshot  # noqa: E402
from tests.fake_client import World, patched  # noqa: E402
from tests.fixtures import MOONRAKER_ROW, status_body  # noqa: E402

GET_STATUS = "custom_components.print_handy.config_flow.PrintHandyApiClient.async_get_status"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for these tests."""
    yield


@pytest.fixture(autouse=True)
def _no_setup():
    with patch("custom_components.print_handy.async_setup_entry", return_value=True):
        yield


def _ok():
    return parse_snapshot(status_body(MOONRAKER_ROW))


async def _start_user(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_step_success(hass: HomeAssistant):
    """User step success."""
    result = await _start_user(hass)
    assert result["type"] is FlowResultType.FORM
    with patch(GET_STATUS, return_value=_ok()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "192.0.2.10:8088", "token": "tok"}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["url"] == "http://192.0.2.10:8088"
    assert result["data"]["token"] == "tok"
    entry = result["result"]
    assert entry.version == 2
    assert entry.data["instance_id"] and entry.unique_id == entry.data["instance_id"]


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (api.PrintHandyAuthError("no"), "invalid_auth"),
        (api.PrintHandyHostRefusedError("host"), "forbidden_host"),
        (api.PrintHandyRateLimitedError("wait", 30), "rate_limited"),
        (api.PrintHandyConnectionError("down"), "cannot_connect"),
        (api.PrintHandyServerError("500"), "server_error"),
        (api.PrintHandyResponseError("bad"), "unknown"),
    ],
)
async def test_user_step_errors(hass: HomeAssistant, error, key):
    """User step errors."""
    result = await _start_user(hass)
    with patch(GET_STATUS, side_effect=error):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "192.0.2.10:8088", "token": ""}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}
    # and the user can fix it
    with patch(GET_STATUS, return_value=_ok()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "192.0.2.10:8088", "token": ""}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_invalid_url(hass: HomeAssistant):
    """User step invalid url."""
    result = await _start_user(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "  ", "token": ""}
    )
    assert result["errors"] == {"url": "invalid_url"}


async def test_already_configured(hass: HomeAssistant):
    """Already configured."""
    MockConfigEntry(
        domain=DOMAIN, version=2, data={"url": "http://192.0.2.10:8088", "token": ""}
    ).add_to_hass(hass)
    result = await _start_user(hass)
    with patch(GET_STATUS, return_value=_ok()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "http://192.0.2.10:8088/", "token": ""}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def _reauth(hass, entry):
    return await entry.start_reauth_flow(hass)


@pytest.mark.parametrize("token", ["new-token", ""])
async def test_reauth_accepts_empty_and_non_empty_token(hass: HomeAssistant, token):
    """Reauth accepts empty and non empty token."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data={"url": "http://192.0.2.10:8088", "token": "old"}
    )
    entry.add_to_hass(hass)
    result = await _reauth(hass, entry)
    assert result["step_id"] == "reauth_confirm"
    with patch(GET_STATUS, return_value=_ok()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": token}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["token"] == token


async def test_reauth_wrong_token_keeps_the_old_one(hass: HomeAssistant):
    """Reauth wrong token keeps the old one."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data={"url": "http://192.0.2.10:8088", "token": "old"}
    )
    entry.add_to_hass(hass)
    result = await _reauth(hass, entry)
    with patch(GET_STATUS, side_effect=api.PrintHandyAuthError("no")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"token": "bad"}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["token"] == "old"


async def test_reconfigure_changes_url_and_token(hass: HomeAssistant):
    """Reconfigure changes url and token."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=2,
        data={"url": "http://print-handy.local:8088", "token": "old", "instance_id": "i1"},
        unique_id="i1",
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    with patch(GET_STATUS, return_value=_ok()):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "https://192.0.2.10", "token": "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["url"] == "https://192.0.2.10"
    assert entry.data["token"] == "new"
    assert entry.data["instance_id"] == "i1"  # identity never changes


async def test_reconfigure_rejects_another_entrys_url_and_bad_credentials(
    hass: HomeAssistant,
):
    """Reconfigure rejects another entrys url and bad credentials."""
    MockConfigEntry(
        domain=DOMAIN, version=2, data={"url": "http://192.0.2.11:8088", "token": ""}
    ).add_to_hass(hass)
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data={"url": "http://192.0.2.10:8088", "token": ""}
    )
    entry.add_to_hass(hass)
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"url": "192.0.2.11:8088", "token": ""}
    )
    assert result["errors"] == {"url": "url_in_use"}
    with patch(GET_STATUS, side_effect=api.PrintHandyAuthError("no")):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"url": "192.0.2.12:8088", "token": "x"}
        )
    assert result["errors"] == {"base": "invalid_auth"}
    assert entry.data["url"] == "http://192.0.2.10:8088"


async def test_options_flow_has_both_intervals(hass: HomeAssistant):
    """Options flow has both intervals."""
    entry = MockConfigEntry(
        domain=DOMAIN, version=2, data={"url": "http://192.0.2.10:8088", "token": ""}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 20, "hub_scan_interval": 600}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 20, "hub_scan_interval": 600}


def test_world_import_keeps_fixtures_honest():
    """World import keeps fixtures honest."""
    assert World().rows[0]["connection"] == "moonraker"
    assert patched  # exported helper stays importable
