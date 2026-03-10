"""CozyLife energy sensor platform.

Energy dpids (18/19/20) only appear in the CMD_QUERY attr response when the
plug is actively drawing power - so we can't rely on device.dpid being
populated with those values at setup time.

Fix: create energy sensor entities for every switch-type device (anything
without brightness dpid 4) unconditionally. update() will try to read the
values and mark the entity unavailable if they're not in the response.

dpid reference:
  18 -> current  (raw mA,     divide by 1000 -> A)
  19 -> power    (raw 0.1 W,  divide by 10   -> W)
  20 -> voltage  (raw 0.1 V,  divide by 10   -> V)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_LOGGER = logging.getLogger(__name__)

DOMAIN = "hass_cozylife_local_pull"


@dataclass(frozen=True)
class CozyLifeSensorDescription(SensorEntityDescription):
    dpid: int = 0
    scale: float = 1.0


SENSOR_DESCRIPTIONS: tuple[CozyLifeSensorDescription, ...] = (
    CozyLifeSensorDescription(
        key="current",
        dpid=18,
        name="Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        scale=0.001,
        icon="mdi:current-ac",
    ),
    CozyLifeSensorDescription(
        key="power",
        dpid=19,
        name="Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        scale=0.1,
        icon="mdi:flash",
    ),
    CozyLifeSensorDescription(
        key="voltage",
        dpid=20,
        name="Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        scale=0.1,
        icon="mdi:sine-wave",
    ),
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict | None = None,
) -> None:
    _setup_sensors(hass, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    _setup_sensors(hass, async_add_entities)


def _is_switch_device(device) -> bool:
    """True for plugs/switches (no brightness dpid 4).

    Same logic as switch.py so sensors only appear alongside switch entities.
    """
    dpids: list[int] = list(getattr(device, "dpid", None) or [])
    if dpids:
        return 4 not in dpids
    # No dpid info yet - use model name heuristic
    dmn: str = (getattr(device, "dmn", "") or "").lower()
    if any(k in dmn for k in ("light", "bulb", "lamp", "strip", "led")):
        return False
    # Default: assume switch/plug and try energy sensors
    return True


def _setup_sensors(hass: HomeAssistant, async_add_entities: AddEntitiesCallback) -> None:
    devices: list = hass.data.get(DOMAIN, {}).get("devices", [])
    if not devices:
        _LOGGER.debug("CozyLife sensor: no devices in hass.data yet")
        return

    entities: list[CozyLifeSensor] = []
    for device in devices:
        if not _is_switch_device(device):
            continue
        # Always create all three energy sensors - update() will handle
        # unavailability if the device doesn't support them
        for description in SENSOR_DESCRIPTIONS:
            entities.append(CozyLifeSensor(device, description))
            _LOGGER.debug(
                "CozyLife sensor: registering %s for %s",
                description.key,
                getattr(device, "ip", "?"),
            )

    if entities:
        async_add_entities(entities, update_before_add=True)
    else:
        _LOGGER.debug("CozyLife sensor: no switch-type devices found")


class CozyLifeSensor(SensorEntity):
    """One energy sensor for a CozyLife plug."""

    entity_description: CozyLifeSensorDescription

    def __init__(self, device, description: CozyLifeSensorDescription) -> None:
        self._device = device
        self.entity_description = description
        self._device_id: str = (
            getattr(device, "did", None) or getattr(device, "ip", "unknown")
        )
        self._attr_unique_id = f"{self._device_id}_{description.key}"
        self._attr_name = f"{getattr(device, 'dmn', None) or 'CozyLife'} {description.name}"
        self._attr_native_value: float | None = None
        self._attr_available: bool = True

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=getattr(self._device, "dmn", None) or "CozyLife Device",
            manufacturer="CozyLife",
            model=getattr(self._device, "pid", None),
        )

    def update(self) -> None:
        """Poll state from device. Mark unavailable if dpid not in response."""
        try:
            state: dict = self._device.query_cached() or {}
        except Exception as exc:
            _LOGGER.warning(
                "CozyLife sensor %s: update error: %s", self._attr_unique_id, exc
            )
            self._attr_available = False
            return

        dpid_key = str(self.entity_description.dpid)
        raw = state.get(dpid_key)

        if raw is None:
            # dpid not in response - device may not support it or plug is idle
            # Keep previous value but stay available so it updates when power flows
            _LOGGER.debug(
                "CozyLife sensor %s: dpid %s not in state %s",
                self._attr_unique_id, dpid_key, state,
            )
            # Only set to None (unknown) if we've never had a value
            if self._attr_native_value is None:
                self._attr_available = False
            return

        self._attr_available = True
        self._attr_native_value = round(float(raw) * self.entity_description.scale, 3)
        _LOGGER.debug(
            "CozyLife sensor %s: raw=%s -> %s %s",
            self._attr_unique_id,
            raw,
            self._attr_native_value,
            self.entity_description.native_unit_of_measurement,
        )
