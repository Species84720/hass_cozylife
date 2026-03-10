"""Config flow for CozyLife Local Pull integration.

Adds UI-based setup via Settings → Devices & Services → Add Integration,
so users no longer need to edit configuration.yaml manually.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_IP_ADDRESS
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hass_cozylife_local_pull"

CONF_LANG = "lang"
CONF_IPS = "ip"

SUPPORTED_LANGUAGES = ["en", "zh"]

DEFAULT_LANG = "en"
DEFAULT_PORT = 5555
CONNECTION_TIMEOUT = 3  # seconds


def _parse_ip_input(raw: str) -> list[str]:
    """Parse a newline- or comma-separated string of IP addresses.

    Returns a deduplicated list of validated IPs, raises vol.Invalid on bad input.
    """
    separators = raw.replace(",", "\n")
    candidates = [s.strip() for s in separators.splitlines() if s.strip()]
    if not candidates:
        raise vol.Invalid("At least one IP address is required.")
    validated: list[str] = []
    for ip in candidates:
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            raise vol.Invalid(f"'{ip}' is not a valid IP address.")
        if ip not in validated:
            validated.append(ip)
    return validated


async def _test_device_connection(ip: str, port: int = DEFAULT_PORT) -> bool:
    """Try a TCP connection to verify a CozyLife device is reachable."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=CONNECTION_TIMEOUT
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except Exception:
        return False


class CozyLifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the initial UI setup flow for CozyLife."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the setup form and validate user input."""
        errors: dict[str, str] = {}

        if user_input is not None:
            raw_ips: str = user_input.get("ip_input", "")
            lang: str = user_input.get(CONF_LANG, DEFAULT_LANG)

            try:
                ip_list = _parse_ip_input(raw_ips)
            except vol.Invalid as exc:
                errors["ip_input"] = str(exc)
                ip_list = []

            if not errors:
                # Optionally verify at least one device is reachable
                reachable = await asyncio.gather(
                    *[_test_device_connection(ip) for ip in ip_list]
                )
                if not any(reachable):
                    errors["ip_input"] = "no_devices_found"

            if not errors:
                # Prevent duplicate entries (same set of IPs)
                await self.async_set_unique_id(
                    "_".join(sorted(ip_list))
                )
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title=f"CozyLife ({', '.join(ip_list)})",
                    data={
                        CONF_LANG: lang,
                        CONF_IPS: ip_list,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    "ip_input",
                    description={
                        "suggested_value": (
                            "\n".join(
                                (user_input or {}).get("ip_input", "").splitlines()
                            )
                            if user_input
                            else ""
                        )
                    },
                ): str,
                vol.Optional(CONF_LANG, default=DEFAULT_LANG): vol.In(
                    SUPPORTED_LANGUAGES
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "example": "192.168.1.10\n192.168.1.11",
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CozyLifeOptionsFlow:
        """Return the options flow so users can edit IPs after setup."""
        return CozyLifeOptionsFlow(config_entry)


class CozyLifeOptionsFlow(OptionsFlow):
    """Allow users to edit IP list and language after initial setup."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the options form pre-filled with current values."""
        errors: dict[str, str] = {}

        current_ips: list[str] = self._entry.data.get(CONF_IPS, [])
        current_lang: str = self._entry.data.get(CONF_LANG, DEFAULT_LANG)

        if user_input is not None:
            raw_ips: str = user_input.get("ip_input", "")
            lang: str = user_input.get(CONF_LANG, DEFAULT_LANG)

            try:
                ip_list = _parse_ip_input(raw_ips)
            except vol.Invalid as exc:
                errors["ip_input"] = str(exc)
                ip_list = []

            if not errors:
                # Update the config entry data in place
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={
                        CONF_LANG: lang,
                        CONF_IPS: ip_list,
                    },
                    title=f"CozyLife ({', '.join(ip_list)})",
                )
                # Reload so the new IPs take effect immediately
                await self.hass.config_entries.async_reload(self._entry.entry_id)
                return self.async_create_entry(title="", data={})

        schema = vol.Schema(
            {
                vol.Required(
                    "ip_input",
                    default="\n".join(current_ips),
                ): str,
                vol.Optional(CONF_LANG, default=current_lang): vol.In(
                    SUPPORTED_LANGUAGES
                ),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "example": "192.168.1.10\n192.168.1.11",
            },
        )
