"""CozyLife light platform - fully migrated to HA 2026.3 API.

Replaces the original light.py entirely. Key changes from the original:
  - ATTR_COLOR_TEMP / min_mireds / max_mireds removed (HA 2026.3)
    → replaced by ATTR_COLOR_TEMP_KELVIN / min_color_temp_kelvin / max_color_temp_kelvin
  - COLOR_MODE_* string constants removed
    → replaced by ColorMode enum
  - SUPPORT_* feature flags removed
    → replaced by supported_color_modes set
  - async_setup_entry added so entities link to the config entry card

CozyLife dpid reference (relevant to lights):
  1  : on/off          bool
  3  : color temp      int 0-1000  (0 = warmest ~2700 K, 1000 = coolest ~6500 K)
  4  : brightness      int 0-1000
  5  : hue             int 0-360
  6  : saturation      int 0-1000
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hass_cozylife_local_pull"

# CozyLife raw color-temp range (dpid 3: 0 = warm, 1000 = cool)
_CT_RAW_MIN = 0
_CT_RAW_MAX = 1000

# Kelvin range for CozyLife bulbs (matches typical 2700-6500 K spec)
_CT_KELVIN_WARM = 2700   # raw 0
_CT_KELVIN_COOL = 6500   # raw 1000

# CozyLife brightness raw range
_BR_RAW_MIN = 0
_BR_RAW_MAX = 1000

# dpid keys (as strings - the device returns string-keyed dicts)
_DP_SWITCH   = "1"
_DP_CT       = "3"
_DP_BRIGHT   = "4"
_DP_HUE      = "5"
_DP_SAT      = "6"


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------

async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict | None = None,
) -> None:
    """Legacy yaml setup path."""
    _setup_lights(hass, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Config-entry setup path - links entities to the integration card."""
    _setup_lights(hass, async_add_entities)


def _setup_lights(
    hass: HomeAssistant,
    async_add_entities: AddEntitiesCallback,
) -> None:
    devices: list = hass.data.get(DOMAIN, {}).get("devices", [])
    entities: list[CozyLifeLight] = []

    for device in devices:
        dpids: list[int] = list(getattr(device, "dpid", []) or [])
        # Only handle devices that have at least an on/off + brightness dpid
        if 1 not in dpids:
            continue
        # Distinguish switch-only (handled by switch.py) from lights
        if 4 not in dpids:
            continue
        entities.append(CozyLifeLight(device, dpids))
        _LOGGER.debug("CozyLife light: registered device %s", getattr(device, "ip", "?"))

    if entities:
        async_add_entities(entities, update_before_add=True)


# ---------------------------------------------------------------------------
# Helper conversions
# ---------------------------------------------------------------------------

def _ct_raw_to_kelvin(raw: int) -> int:
    """Convert CozyLife raw color-temp (0-1000) to Kelvin."""
    raw = max(_CT_RAW_MIN, min(_CT_RAW_MAX, raw))
    return round(
        _CT_KELVIN_WARM
        + (raw / _CT_RAW_MAX) * (_CT_KELVIN_COOL - _CT_KELVIN_WARM)
    )


def _ct_kelvin_to_raw(kelvin: int) -> int:
    """Convert Kelvin to CozyLife raw color-temp (0-1000)."""
    kelvin = max(_CT_KELVIN_WARM, min(_CT_KELVIN_COOL, kelvin))
    return round(
        (kelvin - _CT_KELVIN_WARM)
        / (_CT_KELVIN_COOL - _CT_KELVIN_WARM)
        * _CT_RAW_MAX
    )


def _brightness_ha_to_raw(ha_value: int) -> int:
    """Convert HA brightness 0-255 to CozyLife 0-1000."""
    return round(ha_value / 255 * _BR_RAW_MAX)


def _brightness_raw_to_ha(raw: int) -> int:
    """Convert CozyLife brightness 0-1000 to HA 0-255."""
    return round(raw / _BR_RAW_MAX * 255)


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class CozyLifeLight(LightEntity):
    """Represents a CozyLife light (RGBCW or CW)."""

    def __init__(self, device, dpids: list[int]) -> None:
        self._device = device
        self._dpids = dpids

        self._device_id: str = (
            getattr(device, "did", None) or getattr(device, "ip", "unknown")
        )
        self._attr_unique_id = self._device_id
        self._attr_name = getattr(device, "dmn", "CozyLife Light")

        # Determine supported color modes from advertised dpids
        modes: set[ColorMode] = set()
        if 5 in dpids and 6 in dpids:
            modes.add(ColorMode.HS)
        if 3 in dpids:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes:
            modes.add(ColorMode.BRIGHTNESS)
        self._attr_supported_color_modes = modes

        # Color temp bounds (fixed CozyLife range)
        if ColorMode.COLOR_TEMP in modes:
            self._attr_min_color_temp_kelvin = _CT_KELVIN_WARM
            self._attr_max_color_temp_kelvin = _CT_KELVIN_COOL

        # Runtime state
        self._attr_is_on: bool = False
        self._attr_brightness: int = 255
        self._attr_color_mode: ColorMode = next(iter(modes))
        self._attr_color_temp_kelvin: int | None = None
        self._attr_hs_color: tuple[float, float] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=self._attr_name,
            manufacturer="CozyLife",
            model=getattr(self._device, "pid", None),
        )

    @property
    def available(self) -> bool:
        return getattr(self._device, "_tcp", None) is not None or getattr(
            self._device, "available", True
        )

    # ------------------------------------------------------------------
    # State update
    # ------------------------------------------------------------------

    def update(self) -> None:
        """Poll fresh state from device (runs in executor thread)."""
        try:
            state: dict = self._device.query() or {}
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("CozyLife light %s: update error: %s", self._attr_unique_id, exc)
            return

        # On/off
        raw_on = state.get(_DP_SWITCH)
        if raw_on is not None:
            self._attr_is_on = bool(raw_on)

        # Brightness
        raw_bright = state.get(_DP_BRIGHT)
        if raw_bright is not None:
            self._attr_brightness = _brightness_raw_to_ha(int(raw_bright))

        # Color temperature
        raw_ct = state.get(_DP_CT)
        if raw_ct is not None and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._attr_color_temp_kelvin = _ct_raw_to_kelvin(int(raw_ct))

        # HS color
        raw_hue = state.get(_DP_HUE)
        raw_sat = state.get(_DP_SAT)
        if (
            raw_hue is not None
            and raw_sat is not None
            and ColorMode.HS in self._attr_supported_color_modes
        ):
            hue = float(raw_hue)                     # already 0-360
            sat = round(float(raw_sat) / 10, 1)      # 0-1000 → 0-100
            self._attr_hs_color = (hue, sat)

        # Derive active color mode from state
        # If the device is in HS mode, hue/sat will be non-zero and CT will be
        # at an extreme or absent; we infer from which dpids have sensible values.
        if (
            ColorMode.HS in self._attr_supported_color_modes
            and self._attr_hs_color is not None
            and self._attr_hs_color[1] > 0
        ):
            self._attr_color_mode = ColorMode.HS
        elif ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._attr_color_mode = ColorMode.COLOR_TEMP
        else:
            self._attr_color_mode = ColorMode.BRIGHTNESS

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def turn_on(self, **kwargs: Any) -> None:
        """Turn on (and optionally set brightness/color/color_temp)."""
        dp: dict[str, Any] = {_DP_SWITCH: True}

        if ATTR_BRIGHTNESS in kwargs:
            dp[_DP_BRIGHT] = _brightness_ha_to_raw(kwargs[ATTR_BRIGHTNESS])

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            kelvin: int = kwargs[ATTR_COLOR_TEMP_KELVIN]
            dp[_DP_CT] = _ct_kelvin_to_raw(kelvin)
            self._attr_color_mode = ColorMode.COLOR_TEMP

        if ATTR_HS_COLOR in kwargs and ColorMode.HS in self._attr_supported_color_modes:
            hue, sat = kwargs[ATTR_HS_COLOR]
            dp[_DP_HUE] = round(hue)
            dp[_DP_SAT] = round(sat * 10)   # 0-100 → 0-1000
            self._attr_color_mode = ColorMode.HS

        try:
            self._device.apply_state(dp)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("CozyLife light %s: turn_on failed: %s", self._attr_unique_id, exc)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn off the light."""
        try:
            self._device.apply_state({_DP_SWITCH: False})
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("CozyLife light %s: turn_off failed: %s", self._attr_unique_id, exc)
