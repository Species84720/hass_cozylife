"""CozyLife light platform - HA 2026.3 API, correct CozyLife protocol.

Protocol note: device returns/expects integer values, not booleans.
State lives in msg["data"] with string keys: {"1":1, "3":500, "4":800, ...}
dpid list is populated from the "attr" array in CMD_QUERY response.

CozyLife dpid reference:
  "1" : on/off       0 or 1
  "2" : work mode    0=normal, 1=effect
  "3" : color temp   int 0-1000  (0=warm ~2700K, 1000=cool ~6500K)
  "4" : brightness   int 0-1000
  "5" : hue          int 0-360
  "6" : saturation   int 0-1000
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

_CT_KELVIN_WARM = 2700
_CT_KELVIN_COOL = 6500
_CT_RAW_MAX = 1000
_BR_RAW_MAX = 1000

_DP_SWITCH = "1"
_DP_CT     = "3"
_DP_BRIGHT = "4"
_DP_HUE    = "5"
_DP_SAT    = "6"


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict | None = None,
) -> None:
    _setup_lights(hass, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _setup_lights(hass, async_add_entities)


def _is_light(device) -> bool:
    dpids: list[int] = list(getattr(device, "dpid", None) or [])
    if dpids:
        return 4 in dpids
    dmn: str = (getattr(device, "dmn", "") or "").lower()
    return any(k in dmn for k in ("light", "bulb", "lamp", "strip", "led"))


def _setup_lights(hass: HomeAssistant, async_add_entities: AddEntitiesCallback) -> None:
    devices: list = hass.data.get(DOMAIN, {}).get("devices", [])
    if not devices:
        _LOGGER.debug("CozyLife light: no devices in hass.data yet")
        return
    entities = [CozyLifeLight(d) for d in devices if _is_light(d)]
    _LOGGER.debug("CozyLife light: registering %d entity/entities", len(entities))
    if entities:
        async_add_entities(entities, update_before_add=True)


def _ct_raw_to_kelvin(raw: int) -> int:
    raw = max(0, min(_CT_RAW_MAX, raw))
    return round(_CT_KELVIN_WARM + (raw / _CT_RAW_MAX) * (_CT_KELVIN_COOL - _CT_KELVIN_WARM))


def _ct_kelvin_to_raw(kelvin: int) -> int:
    kelvin = max(_CT_KELVIN_WARM, min(_CT_KELVIN_COOL, kelvin))
    return round((kelvin - _CT_KELVIN_WARM) / (_CT_KELVIN_COOL - _CT_KELVIN_WARM) * _CT_RAW_MAX)


def _br_ha_to_raw(v: int) -> int:
    return round(v / 255 * _BR_RAW_MAX)


def _br_raw_to_ha(v: int) -> int:
    return round(v / _BR_RAW_MAX * 255)


class CozyLifeLight(LightEntity):
    """One CozyLife light (RGBCW or CW)."""

    def __init__(self, device) -> None:
        self._device = device
        dpids: list[int] = list(getattr(device, "dpid", None) or [])

        self._device_id: str = (
            getattr(device, "did", None) or getattr(device, "ip", "unknown")
        )
        self._attr_unique_id = self._device_id
        self._attr_name = getattr(device, "dmn", None) or "CozyLife Light"

        modes: set[ColorMode] = set()
        if not dpids or (5 in dpids and 6 in dpids):
            modes.add(ColorMode.HS)
        if not dpids or 3 in dpids:
            modes.add(ColorMode.COLOR_TEMP)
        if not modes or 4 in dpids:
            modes.add(ColorMode.BRIGHTNESS)
        if not modes:
            modes.add(ColorMode.ONOFF)
        self._attr_supported_color_modes = modes

        if ColorMode.COLOR_TEMP in modes:
            self._attr_min_color_temp_kelvin = _CT_KELVIN_WARM
            self._attr_max_color_temp_kelvin = _CT_KELVIN_COOL

        self._attr_is_on: bool = False
        self._attr_brightness: int = 255
        self._attr_color_mode: ColorMode = (
            ColorMode.COLOR_TEMP if ColorMode.COLOR_TEMP in modes
            else ColorMode.HS if ColorMode.HS in modes
            else ColorMode.BRIGHTNESS
        )
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

    def update(self) -> None:
        try:
            state: dict = self._device.query_cached() or {}
        except Exception as exc:
            _LOGGER.warning("CozyLife light %s update error: %s", self._attr_unique_id, exc)
            return

        raw_on = state.get(_DP_SWITCH)
        if raw_on is not None:
            self._attr_is_on = bool(int(raw_on))

        raw_bright = state.get(_DP_BRIGHT)
        if raw_bright is not None:
            self._attr_brightness = _br_raw_to_ha(int(raw_bright))

        raw_ct = state.get(_DP_CT)
        if raw_ct is not None and ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._attr_color_temp_kelvin = _ct_raw_to_kelvin(int(raw_ct))

        raw_hue = state.get(_DP_HUE)
        raw_sat = state.get(_DP_SAT)
        if raw_hue is not None and raw_sat is not None:
            self._attr_hs_color = (float(raw_hue), round(float(raw_sat) / 10, 1))

        if (ColorMode.HS in self._attr_supported_color_modes
                and self._attr_hs_color and self._attr_hs_color[1] > 0):
            self._attr_color_mode = ColorMode.HS
        elif ColorMode.COLOR_TEMP in self._attr_supported_color_modes:
            self._attr_color_mode = ColorMode.COLOR_TEMP
        else:
            self._attr_color_mode = ColorMode.BRIGHTNESS

    def turn_on(self, **kwargs: Any) -> None:
        dp: dict = {_DP_SWITCH: 1}  # integer 1, not True

        if ATTR_BRIGHTNESS in kwargs:
            dp[_DP_BRIGHT] = _br_ha_to_raw(kwargs[ATTR_BRIGHTNESS])

        if ATTR_COLOR_TEMP_KELVIN in kwargs:
            dp[_DP_CT] = _ct_kelvin_to_raw(kwargs[ATTR_COLOR_TEMP_KELVIN])
            self._attr_color_mode = ColorMode.COLOR_TEMP

        if ATTR_HS_COLOR in kwargs and ColorMode.HS in self._attr_supported_color_modes:
            hue, sat = kwargs[ATTR_HS_COLOR]
            dp[_DP_HUE] = round(hue)
            dp[_DP_SAT] = round(sat * 10)
            self._attr_color_mode = ColorMode.HS

        try:
            self._device.apply_state(dp)
        except Exception as exc:
            _LOGGER.error("CozyLife light %s turn_on error: %s", self._attr_unique_id, exc)

    def turn_off(self, **kwargs: Any) -> None:
        try:
            self._device.apply_state({_DP_SWITCH: 0})  # integer 0, not False
        except Exception as exc:
            _LOGGER.error("CozyLife light %s turn_off error: %s", self._attr_unique_id, exc)
