"""CozyLife local TCP device driver.

Communicates with CozyLife / DoHome devices on TCP port 5555 using their
JSON command protocol.

Packet format (newline-terminated JSON):
  {"cmd": <int>, "pv": 0, "sn": "<epoch_ms>", "msg": <dict>}

Response format:
  {"res": 0, "msg": {"did": "...", "pid": "...", "dmn": "...",
                      "dpid": [...], "dps": {"1": 1, "4": 500, ...}}}

cmd codes (from public protocol documentation):
  0  -> query device info + current state  (sends back did/pid/dmn/dpid/dps)
  3  -> set datapoints                     (msg contains {"dps": {...}})
"""
from __future__ import annotations

import json
import logging
import socket
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

_PORT = 5555
_TIMEOUT = 5       # seconds for connect + read
_RECV_CHUNK = 4096


class CozyLifeDevice:
    """Represents a single CozyLife local-network device."""

    def __init__(self, ip: str) -> None:
        self.ip: str = ip

        # Populated by the first successful query()
        self.did: str = ""
        self.pid: str = ""
        self.dmn: str = ""
        self.dpid: list[int] = []

        # Last known raw datapoints state, e.g. {"1": True, "4": 500}
        self._state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self) -> dict[str, Any]:
        """Open a TCP connection, request device info + state, return dps dict.

        Populates self.did / self.pid / self.dmn / self.dpid from the response.
        Returns the dps dict (e.g. {"1": True, "4": 500}) or {} on failure.
        """
        try:
            raw = self._send_cmd(0, {})
        except Exception as exc:
            _LOGGER.debug("CozyLife %s: query failed: %s", self.ip, exc)
            return {}

        if not raw:
            return {}

        msg = raw.get("msg", {})

        # Update device metadata if present
        if "did" in msg:
            self.did = msg["did"]
        if "pid" in msg:
            self.pid = msg["pid"]
        if "dmn" in msg:
            self.dmn = msg["dmn"]
        if "dpid" in msg:
            raw_dpid = msg["dpid"]
            # dpid may be a list of ints or a comma-separated string
            if isinstance(raw_dpid, list):
                self.dpid = [int(d) for d in raw_dpid]
            elif isinstance(raw_dpid, str):
                self.dpid = [int(d.strip()) for d in raw_dpid.split(",") if d.strip()]

        dps: dict[str, Any] = msg.get("dps", {})
        if dps:
            self._state = dps
        return dict(self._state)

    def apply_state(self, dp: dict) -> None:
        """Send a set-datapoints command.

        Args:
            dp: dict of dpid (int or str) -> value, e.g. {"1": True, "4": 500}
        """
        # Normalise keys to strings (protocol uses string keys)
        payload = {str(k): v for k, v in dp.items()}
        try:
            self._send_cmd(3, {"dps": payload})
            # Optimistically update local state
            self._state.update(payload)
        except Exception as exc:
            _LOGGER.warning("CozyLife %s: apply_state %s failed: %s", self.ip, dp, exc)
            raise

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------

    def _send_cmd(self, cmd: int, msg: dict) -> dict | None:
        """Open a fresh TCP connection, send one command, read one response.

        Returns the parsed JSON response dict, or None on failure.
        """
        packet = json.dumps(
            {
                "cmd": cmd,
                "pv": 0,
                "sn": str(int(time.time() * 1000)),
                "msg": msg,
            },
            separators=(",", ":"),
        ) + "\n"

        data = packet.encode("utf-8")

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(_TIMEOUT)
            sock.connect((self.ip, _PORT))
            sock.sendall(data)

            # Read until we have a complete JSON object (newline or parse success)
            buf = b""
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                try:
                    chunk = sock.recv(_RECV_CHUNK)
                except socket.timeout:
                    break
                if not chunk:
                    break
                buf += chunk
                # Try to parse – some firmware doesn't send a trailing newline
                text = buf.decode("utf-8", errors="replace").strip()
                if text:
                    try:
                        result = json.loads(text)
                        _LOGGER.debug(
                            "CozyLife %s cmd=%d response: %s", self.ip, cmd, result
                        )
                        return result
                    except json.JSONDecodeError:
                        # Incomplete – keep reading
                        pass

        _LOGGER.debug("CozyLife %s cmd=%d: no valid response in buffer: %r", self.ip, cmd, buf)
        return None

    def get_energy_state(self) -> dict:
        """Return raw energy dpid values for dpids 18 (current), 19 (power), 20 (voltage).

        Returns a dict such as {"18": 102, "19": 450, "20": 2301}.
        Values are raw integers from the device:
        dpid 18 → milliamps  (divide by 1000 for Amperes)
        dpid 19 → 0.1-watt units (divide by 10 for Watts)
        dpid 20 → 0.1-volt units (divide by 10 for Volts)

        Returns an empty dict if the device does not support energy monitoring
        or if communication fails.
        """
        ENERGY_DPIDS = [18, 19, 20]

        # Filter to only dpids this device actually advertises
        supported = [d for d in ENERGY_DPIDS if d in (self.dpid or [])]
        if not supported:
            return {}

        try:
            state = self.query()          # existing method, returns full state dict
            if not state:
                return {}
            return {
                str(d): state[str(d)]
                for d in supported
                if str(d) in state
            }
        except Exception:                 # noqa: BLE001
            return {}