"""Config flow for Print Handy."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    PrintHandyApiClient,
    PrintHandyAuthError,
    PrintHandyConnectionError,
    normalize_base_url,
)
from .const import CONF_SCAN_INTERVAL, CONF_TOKEN, DEFAULT_SCAN_INTERVAL, DOMAIN

LOGGER = logging.getLogger(__name__)


def _user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): str,
            vol.Optional(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): str,
        }
    )


class PrintHandyFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Print Handy."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._reauth_entry: config_entries.ConfigEntry | None = None

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return PrintHandyOptionsFlowHandler(config_entry)

    async def _validate(self, url: str, token: str) -> dict[str, str]:
        """Try one /api/status read; return a {field: error} map (empty = ok)."""
        session = async_get_clientsession(self.hass)
        client = PrintHandyApiClient(session=session, base_url=url, token=token)
        try:
            await client.async_get_status()
        except PrintHandyAuthError:
            return {"base": "invalid_auth"}
        except PrintHandyConnectionError:
            return {"base": "cannot_connect"}
        except Exception:  # a snapshot we cannot parse is a bad answer, not a crash
            LOGGER.exception("Unexpected response from Print Handy at %s", url)
            return {"base": "unknown"}
        return {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial step: Print Handy Web UI URL and access token."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                url = normalize_base_url(str(user_input.get(CONF_URL, "")))
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                token = str(user_input.get(CONF_TOKEN, "") or "")
                errors = await self._validate(url, token)
                if not errors:
                    await self.async_set_unique_id(url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Print Handy ({url})",
                        data={CONF_URL: url, CONF_TOKEN: token},
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauthentication when the Web UI token stops working."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm a replacement token for the existing entry."""
        errors: dict[str, str] = {}
        entry = self._reauth_entry
        assert entry is not None

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN, "") or "")
            errors = await self._validate(str(entry.data[CONF_URL]), token)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): str}),
            errors=errors,
        )


class PrintHandyOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow: polling interval."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = int(
            self.config_entry.options.get(CONF_SCAN_INTERVAL)
            or self.config_entry.data.get(CONF_SCAN_INTERVAL)
            or DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_SCAN_INTERVAL, default=current
                    ): vol.All(vol.Coerce(int), vol.Range(min=10, max=3600)),
                }
            ),
        )
