#!/usr/bin/env python3
"""
Haal alle BLE GATT-writes uit een Android btsnoop_hci.log.

Zo leg je vast wat de Hello Fairy-app precies stuurt (bv. in de DIY/plaatjes-
modus), zonder Wireshark:

  1. Android: Instellingen > Ontwikkelaarsopties > "Bluetooth HCI-snoop-logboek
     inschakelen". Bluetooth uit en weer aan.
  2. Open de Hello Fairy-app, verbind, en doe precies één ding (bv. een
     plaatje van 1 rode pixel linksboven versturen).
  3. Haal het log op:   adb bugreport bugreport.zip   (log zit in
     FS/data/misc/bluetooth/logs/btsnoop_hci.log)  of  adb pull /sdcard/btsnoop_hci.log
  4. python tools/btsnoop_writes.py btsnoop_hci.log

Uitvoer: per write de tijd, ATT-handle en de payload in hex, plus een
samenvatting per commandobyte. Vergelijk met fairycurtain/protocol.py.
"""
from __future__ import annotations

import struct
import sys
from collections import Counter, defaultdict

ATT_WRITE_REQ = 0x12
ATT_WRITE_CMD = 0x52
ATT_NOTIFY = 0x1B


def parse(path: str):
    with open(path, "rb") as f:
        data = f.read()
    if not data.startswith(b"btsnoop\x00"):
        sys.exit("Geen btsnoop-bestand")
    version, datalink = struct.unpack(">II", data[8:16])
    pos = 16
    frag: dict[int, bytearray] = defaultdict(bytearray)
    while pos + 24 <= len(data):
        orig, incl, flags, drops, ts = struct.unpack(">IIIIq", data[pos:pos + 24])
        pos += 24
        pkt = data[pos:pos + incl]
        pos += incl
        # H4 (1002): eerste byte = type; H1 (1001): flags bit0 = richting, geen type-byte
        if datalink == 1002:
            if not pkt or pkt[0] != 0x02:
                continue
            acl = pkt[1:]
        elif datalink == 1001:
            if (flags & 0x02) == 0:   # 0 = commando/ACL, 1 = event/HCI
                acl = pkt
            else:
                continue
        else:
            sys.exit(f"Onbekende datalink {datalink}")
        if len(acl) < 4:
            continue
        hf, alen = struct.unpack("<HH", acl[:4])
        handle, pb = hf & 0x0FFF, (hf >> 12) & 0x3
        body = acl[4:4 + alen]
        if pb == 0x02 or pb == 0x00:         # start van L2CAP-pakket
            frag[handle] = bytearray(body)
        else:                                # vervolg
            frag[handle] += body
        buf = frag[handle]
        if len(buf) < 4:
            continue
        l2len, cid = struct.unpack("<HH", buf[:4])
        if len(buf) < 4 + l2len:
            continue                          # nog niet compleet
        att = bytes(buf[4:4 + l2len])
        frag[handle] = bytearray()
        if cid != 0x0004 or not att:
            continue
        yield ts, flags & 1, att


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    counts: Counter = Counter()
    first_ts = None
    print(f"{'t(s)':>8}  {'dir':4} {'hnd':>5}  payload")
    for ts, direction, att in parse(sys.argv[1]):
        op = att[0]
        if op in (ATT_WRITE_REQ, ATT_WRITE_CMD, ATT_NOTIFY) and len(att) >= 3:
            h = struct.unpack("<H", att[1:3])[0]
            val = att[3:]
            first_ts = first_ts or ts
            t = (ts - first_ts) / 1e6
            d = "notif" if op == ATT_NOTIFY else ("req" if op == ATT_WRITE_REQ else "cmd")
            print(f"{t:8.3f}  {d:5} 0x{h:04x}  {val.hex()}")
            if val and val[0] == 0xAA and len(val) > 1 and op != ATT_NOTIFY:
                counts[f"cmd 0x{val[1]:02x}"] += 1
    if counts:
        print("\nSamenvatting writes per commandobyte:")
        for k, v in counts.most_common():
            print(f"  {k}: {v}x")


if __name__ == "__main__":
    main()
