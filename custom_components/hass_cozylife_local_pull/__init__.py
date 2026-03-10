"""CozyLife Local Pull integration.

Supports both:
  • UI-based setup via config_flow (recommended)
  • Legacy configuration.yaml for backward compatibility

The key design principle: platform files (light.py / switch.py) are left
completely untouched.  They only implement async_setup_platform, so we always
drive them through async_load_platform regardless of whether the config came
from a config entry or from YAML.
"""
from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.discovery import async_load_platform
import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hass_cozylife_local_pull"
CONF_LANG = "lang"
CONF_IPS = "ip"

PLATFORMS = ["light", "switch"]

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

    # Use the same platform-loading path the original integration used
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

    We deliberately call async_load_platform (not async_forward_entry_setups)
    so that the existing light.py / switch.py async_setup_platform functions
    work without any modification.
    """
    # Build a minimal config dict that looks like a yaml config to the platforms
    domain_conf = {
        CONF_LANG: entry.data.get(CONF_LANG, "en"),
        CONF_IPS: entry.data.get(CONF_IPS, []),
    }
    fake_hass_config = {DOMAIN: domain_conf}

    _store_runtime_config(hass, domain_conf)

    _LOGGER.info(
        "CozyLife (config entry): lang=%s, ips=%s",
        domain_conf[CONF_LANG],
        domain_conf[CONF_IPS],
    )

    for platform in PLATFORMS:
        hass.async_create_task(
            async_load_platform(hass, platform, DOMAIN, {}, fake_hass_config)
        )

    # Re-apply options when the user edits them via Configure
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Clean up when a config entry is removed or reloaded."""
    hass.data.pop(DOMAIN, None)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


# ═══════════════════════════════════════════════════════════════════════════
# Shared helpers
# ═══════════════════════════════════════════════════════════════════════════

def _store_runtime_config(hass: HomeAssistant, conf: dict) -> None:
    """Store config in hass.data so platform files can read it if needed."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][CONF_LANG] = conf.get(CONF_LANG, "en")
    hass.data[DOMAIN][CONF_IPS] = conf.get(CONF_IPS, [])