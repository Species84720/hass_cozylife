# How to apply this patch

## Files you need

| File | Action |
|---|---|
| `__init__.py` | **Replace** existing file entirely |
| `config_flow.py` | **Replace** existing file entirely (from previous patch) |
| `manifest.json` | **Replace** existing file entirely (from previous patch) |
| `strings.json` | **Replace** existing file entirely (from previous patch) |
| `translations/en.json` | **Replace** existing file entirely (from previous patch) |
| `light_patch.py` | **Do NOT copy this file** – see instructions below |
| `switch_patch.py` | **Do NOT copy this file** – see instructions below |

---

## Step 1 — Copy the straightforward replacements

Copy `__init__.py`, `config_flow.py`, `manifest.json`, `strings.json`, and
`translations/en.json` directly into your
`/config/custom_components/hass_cozylife_local_pull/` folder, replacing
the existing versions.

---

## Step 2 — Patch light.py

Open your existing `light.py` and **append** the following to the very bottom:

```python
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    config = hass.data.get("hass_cozylife_local_pull", {})
    await async_setup_platform(hass, config, async_add_entities, discovery_info=None)
```

---

## Step 3 — Patch switch.py

Open your existing `switch.py` and **append** the following to the very bottom:

```python
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    config = hass.data.get("hass_cozylife_local_pull", {})
    await async_setup_platform(hass, config, async_add_entities, discovery_info=None)
```

---

## Step 4 — Restart Home Assistant

After all files are in place, restart HA fully (not just reload). Then:

- Go to **Settings → Devices & Services**
- Click your **CozyLife** card
- You should now see your light / switch entities listed under it
- Click a device to open its page and use the toggle

---

## Why "click does nothing" was happening

When entities are loaded via `async_load_platform` they are orphaned — not
linked to any config entry. The integration card in the UI has no entities
to display, so clicking it opens a blank page.

`async_forward_entry_setups` (used in the new `__init__.py`) solves this by
instructing HA to call `async_setup_entry` in each platform file, passing the
config entry. Entities created during that call are automatically registered
under the entry, so they appear when you click the card.

---

## About power/wattage monitoring

The CozyLife local TCP protocol (port 5555) **does not expose energy/wattage
data** in the original integration. Only certain smart plugs with built-in
energy monitoring chips support this at the hardware level.

If your device is an energy-monitoring plug, the raw dp (data-point) values
for power, current, and voltage are typically on dpids 18, 19, and 20
respectively — but reading them requires extending `cozylife_device.py` to
poll those dpids and adding a `sensor.py` platform. That is a separate, larger
project.
