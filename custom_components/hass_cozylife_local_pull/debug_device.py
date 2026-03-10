#!/usr/bin/env python3
"""
Run this from your HA host (or any machine on the same network) to see
exactly what your CozyLife device sends back.

Usage:
  python3 debug_device.py 192.168.68.74
  python3 debug_device.py 192.168.68.60
"""
import json, socket, sys, time

IP   = sys.argv[1] if len(sys.argv) > 1 else "192.168.68.74"
PORT = 5555

def sn():
    return str(int(time.time() * 1000))

def send_recv(sock, cmd, msg):
    pkt = json.dumps({"pv":0,"cmd":cmd,"sn":sn(),"msg":msg}, separators=(",",":")) + "\r\n"
    print(f"\n>>> SEND cmd={cmd}: {pkt.strip()}")
    sock.sendall(pkt.encode())
    time.sleep(0.5)
    # read everything available
    sock.settimeout(3)
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            break
    raw = buf.decode("utf-8", errors="replace").strip()
    print(f"<<< RECV: {raw}")
    try:
        return json.loads(raw)
    except:
        return {}

with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
    s.settimeout(5)
    print(f"Connecting to {IP}:{PORT} ...")
    s.connect((IP, PORT))
    print("Connected.")

    # CMD_INFO = 0
    r0 = send_recv(s, 0, {})

    # CMD_QUERY all = 2 with attr [0]
    r2 = send_recv(s, 2, {"attr": [0]})

    # CMD_QUERY explicit energy dpids
    r2e = send_recv(s, 2, {"attr": [18, 19, 20]})

    # CMD_QUERY with ALL dpids explicitly
    r2a = send_recv(s, 2, {"attr": [1, 2, 3, 4, 5, 6, 18, 19, 20]})

    print("\n\nSUMMARY")
    print("CMD_INFO response msg:", r0.get("msg"))
    print("CMD_QUERY [0] data:   ", r2.get("msg", {}).get("data"))
    print("CMD_QUERY energy data:", r2e.get("msg", {}).get("data"))
    print("CMD_QUERY all data:   ", r2a.get("msg", {}).get("data"))
