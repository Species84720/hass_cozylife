"""CozyLife local TCP device driver.

Protocol (from tcp_client.py source):
  CMD_INFO  = 0  ->  {"cmd":0,"pv":0,"sn":"...","msg":{}}
  CMD_QUERY = 2  ->  {"cmd":2,"pv":0,"sn":"...","msg":{"attr":[0]}}
  CMD_SET   = 3  ->  {"cmd":3,"pv":0,"sn":"...","msg":{"attr":[1],"data":{"1":1}}}

Terminator: \\r\\n
State lives in msg["data"].
dpid list from "attr" array in CMD_QUERY response.
On/off: integer 1/0.

IMPORTANT - device behaviour:
  - On connect it immediately pushes a cmd=10 unsolicited state packet.
  - Every query response is followed by another unsolicited push with a
    different sn.
  - _recv_lines() drains all pending data and returns a list of parsed
    dicts so callers can filter by sn.
"""
from __future__ import annotations

import json
import logging
import socket
import threading
import time
from typing import Any

_LOGGER = logging.getLogger(__name__)

_PORT             = 5555
_CONNECT_TIMEOUT  = 5    # seconds
_RECV_TIMEOUT     = 3    # seconds per recv call
_RECV_SIZE        = 4096
_CACHE_TTL        = 8    # seconds


CMD_INFO  = 0
CMD_QUERY = 2
CMD_SET   = 3


def _get_sn() -> str:
    return str(int(time.time() * 1000))


class CozyLifeDevice:
    """One CozyLife local-network device with a persistent TCP connection."""

    def __init__(self, ip: str) -> None:
        self.ip: str = ip
        self.did: str = ""
        self.pid: str = ""
        self.dmn: str = ""
        self.dpid: list[int] = []

        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {}
        self._cache_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def query(self) -> dict[str, Any]:
        with self._lock:
            self._ensure_connected()
            if self._sock is None:
                return dict(self._state)

            if not self.did:
                self._fetch_info()

            data = self._send_recv(CMD_QUERY, {"attr": [0]})
            if data is not None:
                self._state = data
            return dict(self._state)

    def query_cached(self) -> dict[str, Any]:
        now = time.monotonic()
        if now - self._cache_time < _CACHE_TTL and self._state:
            return dict(self._state)
        result = self.query()
        self._cache_time = time.monotonic()
        return result

    def apply_state(self, dp: dict) -> None:
        str_dp   = {str(k): v for k, v in dp.items()}
        int_keys = [int(k) for k in str_dp]
        with self._lock:
            self._ensure_connected()
            if self._sock is None:
                _LOGGER.warning("CozyLife %s: no connection for set", self.ip)
                return
            try:
                self._send(CMD_SET, {"attr": int_keys, "data": str_dp})
                self._state.update(str_dp)
            except Exception as exc:
                _LOGGER.warning("CozyLife %s: apply_state error: %s", self.ip, exc)
                self._disconnect()
                raise

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _ensure_connected(self) -> None:
        if self._sock is not None:
            return
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(_CONNECT_TIMEOUT)
            s.connect((self.ip, _PORT))
            s.settimeout(_RECV_TIMEOUT)
            self._sock = s
            _LOGGER.debug("CozyLife %s: connected", self.ip)
            # Device pushes an unsolicited cmd=10 state packet immediately on
            # connect. Drain it so it doesn't corrupt subsequent recv buffers.
            self._drain()
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

    def _drain(self) -> None:
        """Read and discard any data already waiting in the socket buffer.

        Called once after connect to consume the initial cmd=10 push.
        We also parse any cmd=10 data and seed self._state from it so the
        first query_cached() call already has values.
        """
        self._sock.settimeout(0.5)
        try:
            raw = self._sock.recv(_RECV_SIZE)
            for pkt in self._split_packets(raw):
                msg = pkt.get("msg", {})
                data = msg.get("data") if isinstance(msg, dict) else None
                if isinstance(data, dict) and data:
                    self._state = data
                    attr = msg.get("attr")
                    if isinstance(attr, list):
                        self.dpid = [int(a) for a in attr if a != 0]
                    _LOGGER.debug(
                        "CozyLife %s: seeded state from initial push: %s",
                        self.ip, data,
                    )
        except socket.timeout:
            pass
        except Exception as exc:
            _LOGGER.debug("CozyLife %s: drain error: %s", self.ip, exc)
        finally:
            self._sock.settimeout(_RECV_TIMEOUT)

    # ------------------------------------------------------------------
    # Protocol helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_packets(raw: bytes) -> list[dict]:
        """Split a recv buffer that may contain multiple \\r\\n-terminated
        JSON packets and return all successfully parsed dicts."""
        results = []
        for line in raw.replace(b"\r\n", b"\n").split(b"\n"):
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return results

    def _send(self, cmd: int, msg: dict) -> None:
        sn = _get_sn()
        packet = json.dumps(
            {"pv": 0, "cmd": cmd, "sn": sn, "msg": msg},
            separators=(",", ":"),
        ) + "\r\n"
        self._sock.sendall(packet.encode("utf-8"))

    def _send_recv(self, cmd: int, msg: dict) -> dict[str, Any] | None:
        """Send a command and return msg["data"] from the matching response."""
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

        # Read up to 3 recv() calls — response may arrive in multiple chunks
        # and there will be at least one unsolicited push alongside it.
        buf = b""
        for _ in range(3):
            try:
                chunk = self._sock.recv(_RECV_SIZE)
                if not chunk:
                    self._disconnect()
                    break
                buf += chunk
            except socket.timeout:
                break
            except Exception as exc:
                _LOGGER.debug("CozyLife %s: recv error: %s", self.ip, exc)
                self._disconnect()
                break

        for pkt in self._split_packets(buf):
            # Accept packet if sn matches OR if it contains our data
            # (some firmware echoes with a new sn)
            resp_msg = pkt.get("msg", {})
            if not isinstance(resp_msg, dict):
                continue

            data = resp_msg.get("data")
            if not isinstance(data, dict):
                continue

            # Update dpid list if attr present
            attr = resp_msg.get("attr")
            if isinstance(attr, list) and attr:
                self.dpid = [int(a) for a in attr if a != 0]

            # Prefer the packet whose sn matches ours
            if pkt.get("sn") == sn:
                _LOGGER.debug("CozyLife %s: matched sn, data=%s", self.ip, data)
                return data

        # No sn match - return data from any packet that had a data dict
        # (handles firmware that ignores our sn)
        for pkt in self._split_packets(buf):
            resp_msg = pkt.get("msg", {})
            if isinstance(resp_msg, dict):
                data = resp_msg.get("data")
                if isinstance(data, dict) and data:
                    _LOGGER.debug("CozyLife %s: no sn match, fallback data=%s", self.ip, data)
                    return data

        _LOGGER.debug("CozyLife %s: no data in buf: %s", self.ip, buf)
        return None

    def _fetch_info(self) -> None:
        """Send CMD_INFO to populate did/pid. Uses line-by-line parsing."""
        sn = _get_sn()
        packet = json.dumps(
            {"pv": 0, "cmd": CMD_INFO, "sn": sn, "msg": {}},
            separators=(",", ":"),
        ) + "\r\n"
        try:
            self._sock.sendall(packet.encode("utf-8"))
            raw = self._sock.recv(_RECV_SIZE)
            for pkt in self._split_packets(raw):
                msg = pkt.get("msg", {})
                if not isinstance(msg, dict):
                    continue
                did = msg.get("did", "")
                if did:
                    self.did = did
                    self.pid = msg.get("pid", "") or ""
                    self.dmn = msg.get("dmn", "") or f"CozyLife {self.pid or self.ip}"
                    _LOGGER.info(
                        "CozyLife %s: did=%s pid=%s", self.ip, self.did, self.pid
                    )
                    return
        except Exception as exc:
            _LOGGER.debug("CozyLife %s: _fetch_info error: %s", self.ip, exc)
        # Fallback so we don't retry on every query() call
        if not self.did:
            self.did = self.ip
            self.dmn = f"CozyLife {self.ip}"