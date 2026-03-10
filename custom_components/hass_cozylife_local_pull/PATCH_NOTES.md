# CozyLife Local Pull – UI Config Flow Patch

## What's new

This patch adds **UI-based initialisation** to `hass_cozylife_local_pull` so you
can set it up from **Settings → Devices & Services → Add Integration** without
touching `configuration.yaml`.

---

## Files added / modified

| File | Status | Purpose |
|---|---|---|
| `config_flow.py` | **NEW** | UI config flow + options flow |
| `strings.json` | **NEW** | UI string definitions |
| `translations/en.json` | **NEW** | English UI labels |
| `manifest.json` | **MODIFIED** | Added `"config_flow": true` |
| `__init__.py` | **MODIFIED** | Dual-path setup (config entry + legacy yaml) |

---

## Installation (fresh install)

1. Copy the entire `hass_cozylife_local_pull` folder into your
   `/config/custom_components/` directory.
2. Restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration**.
4. Search for **CozyLife Local Pull**.
5. Enter your device IP(s), one per line (e.g. `192.168.1.10`), and pick a
   language (`en` or `zh`).
6. Hit **Submit** – your lights and switches will appear automatically.

---

## Migrating from configuration.yaml

If you previously used:

```yaml
# configuration.yaml
hass_cozylife_local_pull:
  lang: en
  ip:
    - "192.168.1.99"
```

1. Drop the new files in (see above).
2. Restart Home Assistant.
3. Set up the integration via the UI as described above.
4. Once the config entry is working, **remove** the `hass_cozylife_local_pull:`
   block from `configuration.yaml` and restart again.

> The legacy yaml path still works if you prefer it – the integration detects
> whether a config entry exists and skips yaml to avoid loading devices twice.

---

## Editing IPs later (Options flow)

After setup you can add or remove IPs without restarting:

1. **Settings → Devices & Services** → find your CozyLife entry.
2. Click **Configure**.
3. Edit the IP list and save – the integration reloads automatically.

---

## How it works

### `config_flow.py`

- **`CozyLifeConfigFlow`** – `async_step_user` shows a form asking for:
  - `ip_input` – newline- or comma-separated IP addresses
  - `lang` – `en` or `zh`
  
  It validates each IP with Python's `ipaddress` module, then tries a 3-second
  TCP connection to port 5555 on each device to confirm at least one is reachable.
  A `unique_id` (sorted IPs joined with `_`) prevents duplicate config entries.

- **`CozyLifeOptionsFlow`** – same form, pre-filled with current values.
  On save it calls `async_update_entry` + `async_reload` so changes take effect
  immediately.

### `__init__.py`

- `async_setup` – **legacy yaml path**: runs only when no config entry exists,
  so there's no double-loading.
- `async_setup_entry` – **config entry path**: called by HA when the UI-created
  entry is loaded. Delegates to `_async_init_devices`, then forwards platform
  setup to `light.py` / `switch.py` via `async_forward_entry_setups`.
- `async_unload_entry` – clean unload for reloads.
- `_async_update_listener` – triggers a reload when options are saved.
