"""CozyLife switch / plug platform.

Protocol note: the device returns state as integers (0/1), not booleans.
CMD_SET payload uses string keys: {"1": 1} to turn on, {"1": 0} to turn off.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hass_cozylife_local_pull"
_DP_SWITCH = "1"


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict | None = None,
) -> None:
    _setup_switches(hass, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _setup_switches(hass, async_add_entities)


def _is_switch(device) -> bool:
    dpids: list[int] = list(getattr(device, "dpid", None) or [])
    if dpids:
        return 4 not in dpids
    dmn: str = (getattr(device, "dmn", "") or "").lower()
    if any(k in dmn for k in ("light", "bulb", "lamp", "strip", "led")):
        return False
    return True


def _setup_switches(hass: HomeAssistant, async_add_entities: AddEntitiesCallback) -> None:
    devices: list = hass.data.get(DOMAIN, {}).get("devices", [])
    if not devices:
        _LOGGER.debug("CozyLife switch: no devices in hass.data yet")
        return
    entities = [CozyLifeSwitch(d) for d in devices if _is_switch(d)]
    _LOGGER.debug("CozyLife switch: registering %d entity/entities", len(entities))
    if entities:
        async_add_entities(entities, update_before_add=True)


class CozyLifeSwitch(SwitchEntity):
    """One CozyLife switch or smart plug."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, device) -> None:
        self._device = device
        self._device_id: str = (
            getattr(device, "did", None) or getattr(device, "ip", "unknown")
        )
        self._attr_unique_id = self._device_id
        self._attr_name = getattr(device, "dmn", None) or "CozyLife Switch"
        self._attr_is_on: bool = False

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
            state: dict = self._device.query() or {}
            raw = state.get(_DP_SWITCH)
            if raw is not None:
                # Device returns 0/1 integers, not booleans
                self._attr_is_on = bool(int(raw))
        except Exception as exc:
            _LOGGER.warning("CozyLife switch %s update error: %s", self._attr_unique_id, exc)

    def turn_on(self, **kwargs: Any) -> None:
        try:
            # Use integer 1, not True - device expects int
            self._device.apply_state({_DP_SWITCH: 1})
            self._attr_is_on = True
        except Exception as exc:
            _LOGGER.error("CozyLife switch %s turn_on error: %s", self._attr_unique_id, exc)

    def turn_off(self, **kwargs: Any) -> None:
        try:
            # Use integer 0, not False
            self._device.apply_state({_DP_SWITCH: 0})
            self._attr_is_on = False
        except Exception as exc:
            _LOGGER.error("CozyLife switch %s turn_off error: %s", self._attr_unique_id, exc)
