"""Live aircraft state, and smooth motion between the server's snapshots.

A port of web/src/planes.js: between snapshots each aircraft is projected along
its track at its ground speed, and when a fresh position disagrees with the
projection the difference is eased out instead of jumping.
"""

import math
import time

from geo import to_world

MAX_EXTRAPOLATE_S = 45
CORRECTION_S = 1.2
DROP_AFTER_S = 20


class Fleet:
    def __init__(self):
        self.planes: dict[str, dict] = {}
        self.clock_offset = 0.0  # provider clock minus ours
        self.last_snapshot = 0.0
        self.source = ""
        self.max_correction_nm = 0.0   # largest correction eased in by the last snapshot

    def now(self):
        return time.time() + self.clock_offset

    def update(self, snap):
        local = time.time()
        offset = snap["now"] - local
        self.clock_offset = self.clock_offset * 0.8 + offset * 0.2 if self.last_snapshot else offset
        self.last_snapshot = local
        self.source = snap.get("source", "")

        now = self.now()
        self.max_correction_nm = 0.0
        for a in snap["ac"]:
            prev = self.planes.get(a["hex"])
            plane = dict(a, seen=snap["now"], world=to_world(a["lat"], a["lon"]))
            if prev:
                old_lat, old_lon = self.position(prev, now)
                new_lat, new_lon = project(plane, now)
                d_lon = (old_lon - new_lon + 540) % 360 - 180
                # Only ease small corrections; a big jump is a real jump.
                if abs(old_lat - new_lat) < 0.05 and abs(d_lon) < 0.05:
                    plane["corr"] = (old_lat - new_lat, d_lon, now + CORRECTION_S)
                    nm = 60 * max(abs(old_lat - new_lat), abs(d_lon) * math.cos(math.radians(new_lat)))
                    self.max_correction_nm = max(self.max_correction_nm, nm)
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
    # Flat-earth step: over the few nm between reports it matches the great circle to
    # within metres, at a fraction of the cost (this runs for every plane, every frame).
    d = gs * dt / 3600 / 60          # degrees of latitude travelled
    t = math.radians(trk)
    lat = p["lat"] + d * math.cos(t)
    lon = p["lon"] + d * math.sin(t) / max(0.01, math.cos(math.radians(p["lat"])))
    return lat, (lon + 540) % 360 - 180
