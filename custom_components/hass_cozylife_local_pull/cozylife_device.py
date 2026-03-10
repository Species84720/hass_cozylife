"""CozyLife local TCP device driver.

Based on the original tcp_client.py protocol, corrected for HA integration use.

Real protocol (from tcp_client.py source):
  CMD_INFO  = 0  ->  {"cmd":0,"pv":0,"sn":"...","msg":{}}
                     response: {"msg":{"did":"...","pid":"...","mac":"...","ip":"..."},"res":0}

  CMD_QUERY = 2  ->  {"cmd":2,"pv":0,"sn":"...","msg":{"attr":[0]}}
                     response: {"msg":{"attr":[1,2,3,4,5,6],"data":{"1":0,"3":1000,"4":1000,...}},"res":0}

  CMD_SET   = 3  ->  {"cmd":3,"pv":0,"sn":"...","msg":{"attr":[1],"data":{"1":0}}}
                     response: {"msg":{"attr":[1],"data":{"1":0}},"res":0}

Packet terminator is \\r\\n (NOT \\n).
State lives in msg["data"] (NOT msg["dps"]).
dpid list comes from the "attr" array in a CMD_QUERY response.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

_PORT = 5555
_CONNECT_TIMEOUT = 5   # seconds
_RECV_TIMEOUT = 5      # seconds
_RECV_SIZE = 4096
_RECONNECT_DELAY = 30  # seconds between reconnect attempts

CMD_INFO  = 0
CMD_QUERY = 2
CMD_SET   = 3


def _get_sn() -> str:
    return str(int(time.time() * 1000))


class CozyLifeDevice:
    """One CozyLife local-network device with a persistent TCP connection."""

    def __init__(self, ip: str) -> None:
        self.ip: str = ip

        # Populated after first successful CMD_INFO + CMD_QUERY
        self.did: str = ""
        self.pid: str = ""
        self.dmn: str = ""        # model name (from cloud API or fallback)
        self.dpid: list[int] = [] # populated from CMD_QUERY attr list

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API (called from HA executor thread)
    # ------------------------------------------------------------------

    def query(self) -> dict[str, Any]:
        """Return current device state as a dict of string-keyed dpid values.

        e.g. {"1": True, "3": 500, "4": 800, "18": 102, "19": 450, "20": 2301}

        Also populates self.dpid from the response attr list on first call,
        and self.did / self.pid from CMD_INFO if not already set.
        """
        with self._lock:
            self._ensure_connected()
            if self._sock is None:
                return dict(self._state)

            # Get device identity on first connect
            if not self.did:
                self._fetch_info()

            data = self._send_recv(CMD_QUERY, {"attr": [0]})
            if data is not None:
                self._state = data
                _LOGGER.debug("CozyLife %s state: %s", self.ip, data)
            return dict(self._state)

    def apply_state(self, dp: dict) -> None:
        """Send a CMD_SET command.

        Args:
            dp: {dpid_str_or_int: value} e.g. {"1": True} or {1: True}
        """
        # Keys must be strings in the packet, and attr list needs int keys
        str_dp = {str(k): v for k, v in dp.items()}
        int_keys = [int(k) for k in str_dp]

        with self._lock:
            self._ensure_connected()
            if self._sock is None:
                _LOGGER.warning("CozyLife %s: no connection, cannot set state", self.ip)
                return

            msg = {"attr": int_keys, "data": str_dp}
            # Fire-and-forget (same as original _only_send for control)
            try:
                self._send(CMD_SET, msg)
                # Optimistic local update
                self._state.update(str_dp)
                _LOGGER.debug("CozyLife %s: set %s", self.ip, str_dp)
            except Exception as exc:
                _LOGGER.warning("CozyLife %s: apply_state failed: %s", self.ip, exc)
                self._disconnect()
                raise

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        """Open a new TCP connection if not already connected."""
        if self._sock is not None:
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(_CONNECT_TIMEOUT)
            s.connect((self.ip, _PORT))
            s.settimeout(_RECV_TIMEOUT)
            self._sock = s
            _LOGGER.debug("CozyLife %s: connected", self.ip)
        except Exception as exc:
            _LOGGER.debug("CozyLife %s: connect failed: %s", self.ip, exc)
            self._sock = None

    def _disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    # ------------------------------------------------------------------
    # Protocol helpers
    # ------------------------------------------------------------------

    def _build_packet(self, cmd: int, msg: dict) -> bytes:
        payload = json.dumps(
            {"pv": 0, "cmd": cmd, "sn": _get_sn(), "msg": msg},
            separators=(",", ":"),
        )
        # Original uses \r\n as terminator
        return (payload + "\r\n").encode("utf-8")

    def _send(self, cmd: int, msg: dict) -> None:
        """Send a packet. Raises on socket error."""
        self._sock.sendall(self._build_packet(cmd, msg))

    def _recv(self) -> dict | None:
        """Read one JSON response from the socket. Returns parsed dict or None."""
        buf = b""
        deadline = time.monotonic() + _RECV_TIMEOUT
        while time.monotonic() < deadline:
            try:
                chunk = self._sock.recv(_RECV_SIZE)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            text = buf.decode("utf-8", errors="replace").strip()
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    pass  # incomplete – keep reading
        return None

    def _send_recv(self, cmd: int, msg: dict) -> dict[str, Any] | None:
        """Send a command and return msg["data"] from the response, or None."""
        sn = _get_sn()
        packet = json.dumps(
            {"pv": 0, "cmd": cmd, "sn": sn, "msg": msg},
            separators=(",", ":"),
        ) + "\r\n"

        try:
            self._sock.sendall(packet.encode("utf-8"))
        except Exception as exc:
            _LOGGER.debug("CozyLife %s: send error: %s", self.ip, exc)
            self._disconnect()
            return None

        # Read responses until we find one matching our sn
        # (device may push unsolicited cmd=10 updates)
        for _ in range(10):
            try:
                raw = self._sock.recv(_RECV_SIZE)
            except socket.timeout:
                break
            except Exception as exc:
                _LOGGER.debug("CozyLife %s: recv error: %s", self.ip, exc)
                self._disconnect()
                return None

            if not raw:
                self._disconnect()
                return None

            # Responses may be concatenated; split on \r\n or \n
            for line in raw.replace(b"\r\n", b"\n").split(b"\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    resp = json.loads(line)
                except json.JSONDecodeError:
                    continue

                resp_sn = resp.get("sn", "")
                resp_msg = resp.get("msg", {})

                if resp_sn != sn:
                    continue  # unsolicited push, ignore

                if not isinstance(resp_msg, dict):
                    return {}

                # Extract data dict
                data = resp_msg.get("data")
                if isinstance(data, dict):
                    # Also capture dpid list from attr if present
                    attr = resp_msg.get("attr")
                    if isinstance(attr, list) and attr:
                        self.dpid = [int(a) for a in attr if a != 0]
                    return data

                return {}

        return None

    def _fetch_info(self) -> None:
        """Send CMD_INFO and populate did/pid from response."""
        sn = _get_sn()
        packet = json.dumps(
            {"pv": 0, "cmd": CMD_INFO, "sn": sn, "msg": {}},
            separators=(",", ":"),
        ) + "\r\n"

        try:
            self._sock.sendall(packet.encode("utf-8"))
            raw = self._sock.recv(_RECV_SIZE)
            resp = json.loads(raw.strip())
            msg = resp.get("msg", {})
            if isinstance(msg, dict):
                self.did = msg.get("did", "") or ""
                self.pid = msg.get("pid", "") or ""
                # dmn not in CMD_INFO response - use pid as fallback
                if not self.dmn:
                    self.dmn = msg.get("dmn", "") or f"CozyLife {self.pid or self.ip}"
                _LOGGER.info(
                    "CozyLife %s: did=%s pid=%s", self.ip, self.did, self.pid
                )
        except Exception as exc:
            _LOGGER.debug("CozyLife %s: _fetch_info failed: %s", self.ip, exc)
