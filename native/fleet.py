"""Live aircraft state, and smooth motion between the server's snapshots.

A port of web/src/planes.js: between snapshots each aircraft is projected along
its track at its ground speed, and when a fresh position disagrees with the
projection the difference is eased out instead of jumping.
"""

import time

from geo import destination

MAX_EXTRAPOLATE_S = 45
CORRECTION_S = 1.2
DROP_AFTER_S = 20


class Fleet:
    def __init__(self):
        self.planes: dict[str, dict] = {}
        self.clock_offset = 0.0  # provider clock minus ours
        self.last_snapshot = 0.0
        self.source = ""

    def now(self):
        return time.time() + self.clock_offset

    def update(self, snap):
        local = time.time()
        offset = snap["now"] - local
        self.clock_offset = self.clock_offset * 0.8 + offset * 0.2 if self.last_snapshot else offset
        self.last_snapshot = local
        self.source = snap.get("source", "")

        now = self.now()
        for a in snap["ac"]:
            prev = self.planes.get(a["hex"])
            plane = dict(a, seen=snap["now"])
            if prev:
                old_lat, old_lon = self.position(prev, now)
                new_lat, new_lon = project(plane, now)
                d_lon = (old_lon - new_lon + 540) % 360 - 180
                # Only ease small corrections; a big jump is a real jump.
                if abs(old_lat - new_lat) < 0.05 and abs(d_lon) < 0.05:
                    plane["corr"] = (old_lat - new_lat, d_lon, now + CORRECTION_S)
            self.planes[a["hex"]] = plane
        cutoff = snap["now"] - DROP_AFTER_S
        for h in [h for h, p in self.planes.items() if p["seen"] < cutoff]:
            del self.planes[h]

    def position(self, p, now=None):
        if now is None:
            now = self.now()
        lat, lon = project(p, now)
        corr = p.get("corr")
        if corr and now < corr[2]:
            f = (corr[2] - now) / CORRECTION_S
            lat += corr[0] * f
            lon += corr[1] * f
        return lat, lon


def project(p, now):
    dt = min(MAX_EXTRAPOLATE_S, max(0.0, now - p["t"]))
    gs, trk = p.get("gs"), p.get("trk")
    if not gs or trk is None or dt == 0 or (p.get("gnd") and gs < 3):
        return p["lat"], p["lon"]
    return destination(p["lat"], p["lon"], trk, gs * dt / 3600)
