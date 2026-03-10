"""CozyLife switch platform - updated for HA 2026.3.

Replaces the original switch.py entirely.
Adds async_setup_entry so entities appear under the integration card,
and removes any SUPPORT_* / SwitchDeviceClass legacy patterns.

CozyLife dpid reference (relevant to switches/plugs):
  1 : on/off  bool
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
    _setup_switches(hass, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Config-entry setup path - links entities to the integration card."""
    _setup_switches(hass, async_add_entities)


def _setup_switches(
    hass: HomeAssistant,
    async_add_entities: AddEntitiesCallback,
) -> None:
    devices: list = hass.data.get(DOMAIN, {}).get("devices", [])
    entities: list[CozyLifeSwitch] = []

    for device in devices:
        dpids: list[int] = list(getattr(device, "dpid", []) or [])
        if 1 not in dpids:
            continue
        # Skip devices that are lights (they have brightness dpid 4)
        if 4 in dpids:
            continue
        entities.append(CozyLifeSwitch(device))
        _LOGGER.debug("CozyLife switch: registered device %s", getattr(device, "ip", "?"))

    if entities:
        async_add_entities(entities, update_before_add=True)


# ---------------------------------------------------------------------------
# Entity
# ---------------------------------------------------------------------------

class CozyLifeSwitch(SwitchEntity):
    """Represents a CozyLife switch or smart plug."""

    _attr_device_class = SwitchDeviceClass.OUTLET

    def __init__(self, device) -> None:
        self._device = device
        self._device_id: str = (
            getattr(device, "did", None) or getattr(device, "ip", "unknown")
        )
        self._attr_unique_id = self._device_id
        self._attr_name = getattr(device, "dmn", "CozyLife Switch")
        self._attr_is_on: bool = False

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

    def update(self) -> None:
        """Poll state from device (runs in executor thread)."""
        try:
            state: dict = self._device.query() or {}
            raw = state.get(_DP_SWITCH)
            if raw is not None:
                self._attr_is_on = bool(raw)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("CozyLife switch %s: update error: %s", self._attr_unique_id, exc)

    def turn_on(self, **kwargs: Any) -> None:
        try:
            self._device.apply_state({_DP_SWITCH: True})
            self._attr_is_on = True
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("CozyLife switch %s: turn_on failed: %s", self._attr_unique_id, exc)

    def turn_off(self, **kwargs: Any) -> None:
        try:
            self._device.apply_state({_DP_SWITCH: False})
            self._attr_is_on = False
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("CozyLife switch %s: turn_off failed: %s", self._attr_unique_id, exc)
