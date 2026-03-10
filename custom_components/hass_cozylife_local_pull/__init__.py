"""CozyLife Local Pull integration.

Supports both:
  • UI-based setup via config_flow (recommended, shows entities under the
    integration card and supports the options flow)
  • Legacy configuration.yaml for backward compatibility

Entity-to-config-entry linking is achieved by using
async_forward_entry_setups, which requires async_setup_entry to be present
in light.py and switch.py (provided as patches alongside this file).
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

PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.SWITCH]

# ── Schema for configuration.yaml (legacy) ──────────────────────────────────
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


# ═══════════════════════════════════════════════════════════════════════════
# Legacy setup (configuration.yaml)
# ═══════════════════════════════════════════════════════════════════════════

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up from configuration.yaml (legacy path).

    Skipped automatically when a config entry already exists so devices are
    never loaded twice.
    """
    if DOMAIN not in config:
        return True

    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.debug(
            "CozyLife: config entry exists – skipping configuration.yaml setup"
        )
        return True

    conf = config[DOMAIN]
    _store_runtime_config(hass, conf)

    for platform in PLATFORMS:
        hass.async_create_task(
            async_load_platform(hass, platform, DOMAIN, {}, config)
        )

    return True


# ═══════════════════════════════════════════════════════════════════════════
# Config-entry setup (UI flow)
# ═══════════════════════════════════════════════════════════════════════════

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CozyLife from a UI config entry.

    Stores config in hass.data so the platform async_setup_entry functions
    (in light.py and switch.py) can read it, then forwards to those platforms
    so all entities are properly linked to this config entry.
    """
    _store_runtime_config(hass, entry.data)

    _LOGGER.info(
        "CozyLife (config entry): lang=%s, ips=%s",
        entry.data.get(CONF_LANG, "en"),
        entry.data.get(CONF_IPS, []),
    )

    # async_forward_entry_setups links entities to this config entry so they
    # appear under Settings → Devices & Services → CozyLife card.
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


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════

def _store_runtime_config(hass: HomeAssistant, data: dict) -> None:
    """Write lang + ip list into hass.data[DOMAIN] for platform files to read."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][CONF_LANG] = data.get(CONF_LANG, "en")
    hass.data[DOMAIN][CONF_IPS] = data.get(CONF_IPS, [])
