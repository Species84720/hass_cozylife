"""CozyLife Local Pull integration.

Supports both:
  - UI-based setup via config_flow (recommended)
  - Legacy configuration.yaml for backward compatibility

Uses async_forward_entry_setups so entities (light, switch, sensor) are all
properly linked to the config entry and appear under the integration card.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hass_cozylife_local_pull"
CONF_LANG = "lang"
CONF_IPS = "ip"

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SWITCH, Platform.SENSOR]

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_LANG, default="en"): cv.string,
                vol.Optional(CONF_IPS, default=[]): vol.All(
                    cv.ensure_list, [cv.string]
                ),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


# ---------------------------------------------------------------------------
# Legacy setup (configuration.yaml)
# ---------------------------------------------------------------------------

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up from configuration.yaml (legacy path).

    Skipped automatically when a config entry already exists so devices are
    never loaded twice.
    """
    if DOMAIN not in config:
        return True

    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.debug(
            "CozyLife: config entry exists - skipping configuration.yaml setup"
        )
        return True

    conf = config[DOMAIN]
    _store_runtime_config(hass, conf)

    for platform in PLATFORMS:
        hass.async_create_task(
            async_load_platform(hass, platform, DOMAIN, {}, config)
        )

    return True


# ---------------------------------------------------------------------------
# Config-entry setup (UI flow)
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CozyLife from a UI config entry.

    Stores config in hass.data and forwards to all platform files via
    async_forward_entry_setups so every entity is linked to this entry.
    """
    _store_runtime_config(hass, entry.data)

    _LOGGER.info(
        "CozyLife (config entry): lang=%s, ips=%s",
        entry.data.get(CONF_LANG, "en"),
        entry.data.get(CONF_IPS, []),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all platforms and clean up runtime state."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are saved."""
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _store_runtime_config(hass: HomeAssistant, data: dict) -> None:
    """Write config into hass.data[DOMAIN] for all platform files to read.

    Ensures the 'devices' list key always exists so sensor.py can safely
    iterate it. switch.py and light.py append CozyLifeDevice instances to
    hass.data[DOMAIN]['devices'] during their async_setup_platform calls;
    sensor.py reads from that list to find energy-monitoring devices.
    """
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][CONF_LANG] = data.get(CONF_LANG, "en")
    hass.data[DOMAIN][CONF_IPS] = data.get(CONF_IPS, [])
    # Preserve existing device list if platforms already populated it
    hass.data[DOMAIN].setdefault("devices", [])
