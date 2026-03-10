"""CozyLife energy sensor platform.

Creates three sensor entities per device that exposes energy-monitoring dpids:
  • dpid 18 → current  (mA raw  → A displayed,  divide by 1000)
  • dpid 19 → power    (0.1 W   → W displayed,  divide by 10)
  • dpid 20 → voltage  (0.1 V   → V displayed,  divide by 10)

Entities are only registered when the device's dpid list actually includes the
relevant dpid, so lights and basic switches are left untouched.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

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

# ── Sensor catalogue ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CozyLifeSensorDescription(SensorEntityDescription):
    """Extends SensorEntityDescription with CozyLife-specific fields."""
    dpid: int = 0
    # Raw value from device → display value conversion
    scale: float = 1.0


SENSOR_DESCRIPTIONS: tuple[CozyLifeSensorDescription, ...] = (
    CozyLifeSensorDescription(
        key="current",
        dpid=18,
        name="Current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        scale=0.001,          # raw mA → A
        icon="mdi:current-ac",
    ),
    CozyLifeSensorDescription(
        key="power",
        dpid=19,
        name="Power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        scale=0.1,            # raw 0.1 W units → W
        icon="mdi:flash",
    ),
    CozyLifeSensorDescription(
        key="voltage",
        dpid=20,
        name="Voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        scale=0.1,            # raw 0.1 V units → V
        icon="mdi:sine-wave",
    ),
)


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_platform(
    hass: HomeAssistant,
    config: dict,
    async_add_entities: AddEntitiesCallback,
    discovery_info: dict | None = None,
) -> None:
    """Set up sensor entities from configuration.yaml (legacy path)."""
    _setup_sensors(hass, async_add_entities)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensor entities from a config entry (UI flow)."""
    _setup_sensors(hass, async_add_entities)


def _setup_sensors(
    hass: HomeAssistant,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensor entities for every device that supports energy dpids."""
    devices: list = hass.data.get(DOMAIN, {}).get("devices", [])

    entities: list[CozyLifeSensor] = []
    for device in devices:
        device_dpids: list[int] = list(getattr(device, "dpid", []) or [])

        for description in SENSOR_DESCRIPTIONS:
            if description.dpid in device_dpids:
                entities.append(CozyLifeSensor(device, description))
                _LOGGER.debug(
                    "CozyLife sensor: registering %s for device %s (dpid %d)",
                    description.key,
                    getattr(device, "ip", "?"),
                    description.dpid,
                )

    if entities:
        async_add_entities(entities, update_before_add=True)
    else:
        _LOGGER.debug(
            "CozyLife sensor: no energy-monitoring devices found "
            "(dpids 18/19/20 not present in any device's dpid list)"
        )


# ── Entity class ─────────────────────────────────────────────────────────────

class CozyLifeSensor(SensorEntity):
    """A single energy measurement sensor for one CozyLife device."""

    entity_description: CozyLifeSensorDescription

    def __init__(self, device, description: CozyLifeSensorDescription) -> None:
        self._device = device
        self.entity_description = description

        # Use device did if available, fall back to IP
        self._device_id: str = (
            getattr(device, "did", None) or getattr(device, "ip", "unknown")
        )
        self._attr_unique_id = f"{self._device_id}_{description.key}"
        self._attr_name = (
            f"{getattr(device, 'dmn', 'CozyLife')} {description.name}"
        )
        self._attr_native_value: float | None = None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=getattr(self._device, "dmn", "CozyLife Device"),
            manufacturer="CozyLife",
            model=getattr(self._device, "pid", None),
        )

    @property
    def available(self) -> bool:
        return getattr(self._device, "_tcp", None) is not None or getattr(
            self._device, "available", True
        )

    def update(self) -> None:
        """Pull fresh state from the device (called in executor by HA)."""
        try:
            # query() refreshes self._device.state (the raw dpid dict)
            state: dict = self._device.query() or {}
            raw = state.get(str(self.entity_description.dpid))
            if raw is None:
                # Some firmware puts integer keys
                raw = state.get(self.entity_description.dpid)

            if raw is not None:
                self._attr_native_value = round(
                    float(raw) * self.entity_description.scale, 3
                )
                _LOGGER.debug(
                    "CozyLife sensor %s: raw=%s → %s %s",
                    self._attr_unique_id,
                    raw,
                    self._attr_native_value,
                    self.entity_description.native_unit_of_measurement,
                )
            else:
                _LOGGER.debug(
                    "CozyLife sensor %s: dpid %d missing from state %s",
                    self._attr_unique_id,
                    self.entity_description.dpid,
                    state,
                )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "CozyLife sensor %s: update failed: %s",
                self._attr_unique_id,
                exc,
            )
