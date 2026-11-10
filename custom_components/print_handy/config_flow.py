"""Config flow for Print Handy."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_URL
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    PrintHandyApiClient,
    PrintHandyApiError,
    PrintHandyAuthError,
    PrintHandyConnectionError,
    PrintHandyHostRefusedError,
    PrintHandyRateLimitedError,
    PrintHandyServerError,
    normalize_base_url,
)
from .const import (
    CONF_HUB_SCAN_INTERVAL,
    CONF_INSTANCE_ID,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_HUB_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_HUB_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)

LOGGER = logging.getLogger(__name__)

# Version 1 entries (0.5.x) keyed every entity and device on the typed URL
# and used the URL as the entry's unique id; version 2 uses a stable
# instance id (see async_migrate_entry in __init__).
ENTRY_VERSION = 2

_TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


def _credentials_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """URL plus the optional token (the access token or the Web UI password)."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): str,
            vol.Optional(CONF_TOKEN, default=defaults.get(CONF_TOKEN, "")): _TOKEN_SELECTOR,
        }
    )


def _token_schema() -> vol.Schema:
    """Reauth form: the token may be empty, for a server that went back to open."""
    return vol.Schema({vol.Optional(CONF_TOKEN, default=""): _TOKEN_SELECTOR})


async def async_validate_connection(hass: Any, url: str, token: str) -> dict[str, str]:
    """Try one /api/status read; return a {field: error} map (empty = ok)."""
    session = async_get_clientsession(hass)
    client = PrintHandyApiClient(session=session, base_url=url, token=token)
    try:
        await client.async_get_status()
    except PrintHandyAuthError:
        return {"base": "invalid_auth"}
    except PrintHandyRateLimitedError:
        return {"base": "rate_limited"}
    except PrintHandyHostRefusedError:
        return {"base": "forbidden_host"}
    except PrintHandyServerError:
        return {"base": "server_error"}
    except PrintHandyConnectionError:
        return {"base": "cannot_connect"}
    except PrintHandyApiError as err:  # an answer we cannot use is not a crash
        LOGGER.debug("Unusable response from Print Handy at %s: %s", url, err)
        return {"base": "unknown"}
    except Exception:  # noqa: BLE001 - anything else is also a bad answer
        LOGGER.exception("Unexpected response from Print Handy at %s", url)
        return {"base": "unknown"}
    return {}


class PrintHandyFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow for Print Handy."""

    VERSION = ENTRY_VERSION

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Get the options flow for this handler."""
        return PrintHandyOptionsFlowHandler()

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
                self._async_abort_entries_match({CONF_URL: url})
                token = str(user_input.get(CONF_TOKEN, "") or "")
                errors = await async_validate_connection(self.hass, url, token)
                if not errors:
                    instance_id = uuid.uuid4().hex
                    await self.async_set_unique_id(instance_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Print Handy ({url})",
                        data={
                            CONF_URL: url,
                            CONF_TOKEN: token,
                            CONF_INSTANCE_ID: instance_id,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_credentials_schema(user_input),
            errors=errors,
            description_placeholders={"example_url": "http://print-handy.local:8088"},
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> config_entries.ConfigFlowResult:
        """Handle reauthentication when the Web UI token stops working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Confirm a replacement token (possibly empty) for the existing entry.

        An empty token is valid: a server whose password was removed is
        open again and must be reconnectable. Either way the entry is only
        updated after a successful request with the value given.
        """
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            token = str(user_input.get(CONF_TOKEN, "") or "")
            errors = await async_validate_connection(
                self.hass, str(entry.data[CONF_URL]), token
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_token_schema(),
            errors=errors,
            description_placeholders={"url": str(entry.data[CONF_URL])},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Change the URL and token of an existing entry, keeping its entities.

        Entity and device ids hang off the entry's stable instance id, not
        the URL, so moving from a host name to an IP address or from http
        to https leaves every entity and its history in place.
        """
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                url = normalize_base_url(str(user_input.get(CONF_URL, "")))
            except ValueError:
                errors[CONF_URL] = "invalid_url"
            else:
                token = str(user_input.get(CONF_TOKEN, "") or "")
                if any(
                    other.entry_id != entry.entry_id
                    and other.data.get(CONF_URL) == url
                    for other in self._async_current_entries(include_ignore=False)
                ):
                    errors[CONF_URL] = "url_in_use"
                else:
                    errors = await async_validate_connection(self.hass, url, token)
                if not errors:
                    return self.async_update_reload_and_abort(
                        entry,
                        title=f"Print Handy ({url})",
                        data_updates={CONF_URL: url, CONF_TOKEN: token},
                    )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_credentials_schema(user_input or entry.data),
            errors=errors,
        )


class PrintHandyOptionsFlowHandler(config_entries.OptionsFlow):
    """Options flow: the two polling intervals.

    ``self.config_entry`` comes from the base class.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        entry = self.config_entry
        scan = int(
            entry.options.get(CONF_SCAN_INTERVAL)
            or entry.data.get(CONF_SCAN_INTERVAL)
            or DEFAULT_SCAN_INTERVAL
        )
        hub = int(
            entry.options.get(CONF_HUB_SCAN_INTERVAL)
            or entry.data.get(CONF_HUB_SCAN_INTERVAL)
            or DEFAULT_HUB_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=scan): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_SCAN_INTERVAL, max=3600)
                    ),
                    vol.Optional(CONF_HUB_SCAN_INTERVAL, default=hub): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_HUB_SCAN_INTERVAL, max=3600)
                    ),
                }
            ),
        )
