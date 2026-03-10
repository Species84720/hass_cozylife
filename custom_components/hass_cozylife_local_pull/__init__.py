"""CozyLife Local Pull integration.

Supports both:
  - UI-based setup via config_flow (recommended)
  - Legacy configuration.yaml for backward compatibility

IMPORTANT: This file requires cozylife_device.py from the ORIGINAL repo to
remain in the same folder. We do NOT replace that file - it contains the
CozyLifeDevice class with the TCP socket logic for communicating with devices.
"""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
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
    """Set up from configuration.yaml (legacy path)."""
    if DOMAIN not in config:
        return True

    if hass.config_entries.async_entries(DOMAIN):
        _LOGGER.debug(
            "CozyLife: config entry exists - skipping configuration.yaml setup"
        )
        return True

    conf = config[DOMAIN]
    lang = conf.get(CONF_LANG, "en")
    ip_list = conf.get(CONF_IPS, [])

    try:
        await _async_build_devices(hass, ip_list, lang)
    except ImportError as exc:
        _LOGGER.error(
            "CozyLife: cannot import CozyLifeDevice - make sure cozylife_device.py "
            "from the original repository is present in the custom_components/"
            "hass_cozylife_local_pull/ folder. Error: %s", exc
        )
        return False

    for platform in PLATFORMS:
        hass.async_create_task(
            async_load_platform(hass, platform, DOMAIN, {}, config)
        )

    return True


# ---------------------------------------------------------------------------
# Config-entry setup (UI flow)
# ---------------------------------------------------------------------------

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CozyLife from a UI config entry."""
    lang: str = entry.data.get(CONF_LANG, "en")
    ip_list: list[str] = entry.data.get(CONF_IPS, [])

    _LOGGER.info(
        "CozyLife: setting up entry - lang=%s, ips=%s", lang, ip_list
    )

    try:
        await _async_build_devices(hass, ip_list, lang)
    except ImportError as exc:
        raise ConfigEntryNotReady(
            f"CozyLife: cannot import CozyLifeDevice. "
            f"Make sure cozylife_device.py from the original repository is "
            f"present in custom_components/hass_cozylife_local_pull/. "
            f"Error: {exc}"
        ) from exc

    _LOGGER.info(
        "CozyLife: %d device(s) ready, forwarding to platforms",
        len(hass.data[DOMAIN]["devices"]),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload all platforms and clean up."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.pop(DOMAIN, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when options are saved."""
    await hass.config_entries.async_reload(entry.entry_id)


# ---------------------------------------------------------------------------
# Device initialisation
# ---------------------------------------------------------------------------

async def _async_build_devices(
    hass: HomeAssistant, ip_list: list[str], lang: str
) -> None:
    """Create CozyLifeDevice objects for every IP and cache in hass.data.

    Raises ImportError if cozylife_device.py is missing (caller handles it).
    Devices that fail to respond are logged but still added so they can
    recover when they come back online.
    """
    # Import here so a missing file raises ImportError the caller can handle
    from .cozylife_device import CozyLifeDevice  # noqa: PLC0415

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][CONF_LANG] = lang
    hass.data[DOMAIN][CONF_IPS] = ip_list
    hass.data[DOMAIN]["devices"] = []

    if not ip_list:
        _LOGGER.warning(
            "CozyLife: no IP addresses configured - no devices will appear. "
            "Go to Settings -> Devices & Services -> CozyLife -> Configure "
            "to add device IPs."
        )
        return

    async def _init_one(ip: str) -> None:
        try:
            device = CozyLifeDevice(ip)
            await hass.async_add_executor_job(device.query)
            hass.data[DOMAIN]["devices"].append(device)
            _LOGGER.debug(
                "CozyLife: initialised device at %s  did=%s dpid=%s",
                ip,
                getattr(device, "did", "?"),
                getattr(device, "dpid", "?"),
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "CozyLife: failed to initialise device at %s: %s", ip, exc
            )

    await asyncio.gather(*[_init_one(ip) for ip in ip_list])