"""miniSEED 2 records → samples, and a minimal SeedLink 3 client (what EarthScope's ring server speaks).

Small on purpose (obspy would pull in scipy and matplotlib for two functions): fixed header + blockette 1000,
Steim-1, Steim-2, int16/int32/float encodings — what the CN, UW and CC networks send.
"""

from __future__ import annotations

import socket
import struct
import time
from datetime import datetime, timezone

import numpy as np


class Record:
    __slots__ = ("net", "sta", "loc", "cha", "start", "rate", "data")

    def __init__(self, net, sta, loc, cha, start, rate, data):
        self.net, self.sta, self.loc, self.cha, self.start, self.rate, self.data = net, sta, loc, cha, start, rate, data

    @property
    def sid(self):
        return f"{self.net}.{self.sta}.{self.loc}.{self.cha}"

    @property
    def end(self):
        return self.start + len(self.data) / self.rate

    def __repr__(self):
        return f"<{self.sid} {datetime.fromtimestamp(self.start, timezone.utc):%H:%M:%S.%f} {len(self.data)}@{self.rate:g}>"


def _rate(fact, mult):
    if fact == 0:
        return 0.0
    r = float(fact) if fact > 0 else -1.0 / fact
    if mult > 0:
        r *= mult
    elif mult < 0:
        r /= -mult
    return r


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _steim(buf, n, level, bo):
    """Steim-1/2 frames → int samples (first difference ignored: X0 is the first sample)."""
    words = np.frombuffer(buf[: len(buf) // 64 * 64], dtype=">u4" if bo == ">" else "<u4").astype(np.int64)
    diffs = []
    x0 = None
    for f in range(len(words) // 16):
        fw = words[f * 16: f * 16 + 16]
        nib = int(fw[0])
        for w in range(1, 16):
            c = (nib >> (30 - 2 * w)) & 3
            v = int(fw[w])
            if f == 0 and w == 1:
                x0 = _sext(v, 32)
                continue
            if f == 0 and w == 2:
                continue                                   # Xn (reverse constant)
            if c == 0:
                continue
            if level == 1:
                if c == 1:
                    diffs += [_sext((v >> s) & 0xFF, 8) for s in (24, 16, 8, 0)]
                elif c == 2:
                    diffs += [_sext((v >> 16) & 0xFFFF, 16), _sext(v & 0xFFFF, 16)]
                else:
                    diffs.append(_sext(v, 32))
                continue
            dn = (v >> 30) & 3
            if c == 1:
                diffs += [_sext((v >> s) & 0xFF, 8) for s in (24, 16, 8, 0)]
            elif c == 2:
                if dn == 1:
                    diffs.append(_sext(v & 0x3FFFFFFF, 30))
                elif dn == 2:
                    diffs += [_sext((v >> 15) & 0x7FFF, 15), _sext(v & 0x7FFF, 15)]
                elif dn == 3:
                    diffs += [_sext((v >> s) & 0x3FF, 10) for s in (20, 10, 0)]
            else:
                if dn == 0:
                    diffs += [_sext((v >> s) & 0x3F, 6) for s in (24, 18, 12, 6, 0)]
                elif dn == 1:
                    diffs += [_sext((v >> s) & 0x1F, 5) for s in (25, 20, 15, 10, 5, 0)]
                elif dn == 2:
                    diffs += [_sext((v >> s) & 0xF, 4) for s in (24, 20, 16, 12, 8, 4, 0)]
        if len(diffs) >= n:
            break
    if x0 is None or n == 0:
        return np.zeros(0, np.float64)
    d = np.array(diffs[:n], dtype=np.int64)
    d[0] = 0
    return (x0 + np.cumsum(d)).astype(np.float64)


def parse(rec):
    """One miniSEED 2 record (bytes) → Record, or None for non-data records."""
    if len(rec) < 48 or rec[6:7] not in b"DRQM":
        return None
    bo = ">"
    year = struct.unpack(">H", rec[20:22])[0]
    if not 1900 < year < 2200:
        bo = "<"
    yr, doy, hh, mi, ss, _, frac = struct.unpack(bo + "HHBBBBH", rec[20:30])
    ns, fact, mult = struct.unpack(bo + "Hhh", rec[30:36])
    nblk = rec[39]
    tcorr = struct.unpack(bo + "i", rec[40:44])[0]
    doff, boff = struct.unpack(bo + "HH", rec[44:48])
    act = rec[36]
    enc, reclen, wbo, usec = None, len(rec), bo, 0
    for _ in range(nblk):
        if not 48 <= boff < len(rec) - 4:
            break
        btype, nxt = struct.unpack(bo + "HH", rec[boff:boff + 4])
        if btype == 1000:
            enc, wo, ex = rec[boff + 4], rec[boff + 5], rec[boff + 6]
            wbo = ">" if wo == 1 else "<"
            reclen = 1 << ex
        elif btype == 1001:
            usec = struct.unpack("b", rec[boff + 5:boff + 6])[0]
        if not nxt:
            break
        boff = nxt
    rate = _rate(fact, mult)
    if enc is None or rate <= 0 or ns == 0:
        return None
    start = datetime(yr, 1, 1, hh, mi, ss, tzinfo=timezone.utc).timestamp() + (doy - 1) * 86400 + frac / 1e4 + usec / 1e6
    if not act & 0x02 and tcorr:
        start += tcorr / 1e4
    body = rec[doff:reclen]
    if enc == 11:
        data = _steim(body, ns, 2, wbo)
    elif enc == 10:
        data = _steim(body, ns, 1, wbo)
    elif enc == 1:
        data = np.frombuffer(body[: ns * 2], dtype=wbo + "i2").astype(np.float64)
    elif enc == 3:
        data = np.frombuffer(body[: ns * 4], dtype=wbo + "i4").astype(np.float64)
    elif enc == 4:
        data = np.frombuffer(body[: ns * 4], dtype=wbo + "f4").astype(np.float64)
    elif enc == 5:
        data = np.frombuffer(body[: ns * 8], dtype=wbo + "f8").astype(np.float64)
    else:
        return None
    return Record(rec[18:20].decode().strip(), rec[8:13].decode().strip(), rec[13:15].decode().strip(),
                  rec[15:18].decode().strip(), start, rate, data)


def read_file(blob):
    """A miniSEED file (bytes; records of any power-of-two length) → [Record]."""
    out, i = [], 0
    while i + 48 <= len(blob):
        n = 4096
        bo = ">" if 1900 < struct.unpack(">H", blob[i + 20:i + 22])[0] < 2200 else "<"
        nblk, boff = blob[i + 39], struct.unpack(bo + "H", blob[i + 46:i + 48])[0]
        for _ in range(nblk):
            bt, nxt = struct.unpack(bo + "HH", blob[i + boff:i + boff + 4])
            if bt == 1000:
                n = 1 << blob[i + boff + 6]
                break
            if not nxt:
                break
            boff = nxt
        r = parse(blob[i:i + n])
        if r:
            out.append(r)
        i += n
    return out


# ------------------------------------------------------------------------------------------------ SeedLink
class SeedLink:
    """Multi-station SeedLink 3 session: ``streams`` = [(net, sta, [selectors…])], e.g. ("CN", "QEPB", ["HNZ"])."""

    def __init__(self, host, port, streams, ua="BigScreen/2.0", start=None):
        self.host, self.port, self.streams, self.ua, self.start = host, port, streams, ua, start
        self.sock = None
        self.buf = b""

    def _cmd(self, line, expect=True):
        self.sock.sendall(line.encode() + b"\r\n")
        if not expect:
            return ""
        t0 = time.time()
        while b"\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError(f"closed after {line!r}")
            self.buf += chunk
            if time.time() - t0 > 30:
                raise TimeoutError(line)
        resp, self.buf = self.buf.split(b"\r\n", 1)
        return resp.decode(errors="replace")

    def connect(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=30)
        self.buf = b""
        hello = self._cmd("HELLO")
        self._cmd("")                                         # HELLO answers two lines
        self._cmd(f"USERAGENT {self.ua}") if "SLPROTO:4" in hello else None
        ok = 0
        for net, sta, sels in self.streams:
            if self._cmd(f"STATION {sta} {net}") != "OK":
                continue
            for s in sels:
                self._cmd(f"SELECT {s}")
            if self.start:
                t = datetime.fromtimestamp(self.start, timezone.utc)
                r = self._cmd(f"TIME {t:%Y,%m,%d,%H,%M,%S}")
            else:
                r = self._cmd("DATA")
            ok += r == "OK"
        self._cmd("END", expect=False)
        self.sock.settimeout(90)
        return hello.strip(), ok

    def records(self):
        """Yield Records forever (raises on a dead connection)."""
        while True:
            while len(self.buf) < 520:
                chunk = self.sock.recv(65536)
                if not chunk:
                    raise ConnectionError("server closed")
                self.buf += chunk
            if self.buf[:2] != b"SL":
                i = self.buf.find(b"SL", 1)                   # resync (shouldn't happen)
                self.buf = self.buf[i:] if i > 0 else b""
                continue
            if self.buf[2:8] == b"INFO  " or self.buf[2:6] == b"INFO":
                self.buf = self.buf[520:]
                continue
            rec, self.buf = self.buf[8:520], self.buf[520:]
            r = parse(rec)
            if r:
                yield r

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass
