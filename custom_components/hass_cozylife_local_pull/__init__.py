"""CozyLife Local Pull integration.

Supports both:
  • UI-based setup via config_flow (recommended)
  • Legacy configuration.yaml entry for backward compatibility
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import discovery
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.discovery import async_load_platform

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

# ── Runtime storage key ──────────────────────────────────────────────────────
COZYLIFE_DEVICES_KEY = f"{DOMAIN}_devices"


# ═══════════════════════════════════════════════════════════════════════════
# Legacy setup (configuration.yaml)
# ═══════════════════════════════════════════════════════════════════════════

async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up from configuration.yaml (legacy path).

    When the user has BOTH a config entry AND a yaml block we skip yaml so
    the config entry takes precedence and we don't load devices twice.
    """
    if DOMAIN not in config:
        return True

    # Skip yaml setup if a config entry already exists for this domain.
    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.debug(
            "CozyLife config entry exists – skipping configuration.yaml setup."
        )
        return True

    conf = config[DOMAIN]
    lang: str = conf.get(CONF_LANG, "en")
    ip_list: list[str] = conf.get(CONF_IPS, [])

    _LOGGER.info(
        "CozyLife (yaml): lang=%s, devices=%s", lang, ip_list
    )

    await _async_init_devices(hass, lang, ip_list)

    for platform in PLATFORMS:
        await async_load_platform(hass, platform, DOMAIN, {}, config)

    return True


# ═══════════════════════════════════════════════════════════════════════════
# Config-entry setup (UI flow)
# ═══════════════════════════════════════════════════════════════════════════

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CozyLife from a config entry (UI-based setup)."""
    lang: str = entry.data.get(CONF_LANG, "en")
    ip_list: list[str] = entry.data.get(CONF_IPS, [])

    _LOGGER.info(
        "CozyLife (config entry): lang=%s, devices=%s", lang, ip_list
    )

    await _async_init_devices(hass, lang, ip_list)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register listener so options-flow changes are applied on reload
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up device state."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


# ═══════════════════════════════════════════════════════════════════════════
# Shared initialisation helper
# ═══════════════════════════════════════════════════════════════════════════

async def _async_init_devices(
    hass: HomeAssistant, lang: str, ip_list: list[str]
) -> None:
    """Discover / connect to CozyLife devices and store them in hass.data."""
    from .cozylife_device import CozyLifeDevice  # local import to avoid circular

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["lang"] = lang
    hass.data[DOMAIN]["devices"] = []

    if not ip_list:
        # No explicit IPs → rely on UDP broadcast discovery (original behaviour)
        _LOGGER.debug("CozyLife: no IPs provided, using UDP discovery")
        return

    connect_tasks = [
        _async_connect_device(hass, ip, lang) for ip in ip_list
    ]
    await asyncio.gather(*connect_tasks, return_exceptions=True)


async def _async_connect_device(
    hass: HomeAssistant, ip: str, lang: str
) -> None:
    """Connect to a single device and register it."""
    from .cozylife_device import CozyLifeDevice  # local import

    try:
        device = CozyLifeDevice(ip)
        await hass.async_add_executor_job(device.query)
        hass.data[DOMAIN]["devices"].append(device)
        _LOGGER.debug("CozyLife: connected to device at %s", ip)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning(
            "CozyLife: could not connect to device at %s: %s", ip, exc
        )
