// Live aircraft state, and smooth motion between the server's snapshots.
//
// Each snapshot gives every aircraft's position at the moment it was received
// (`t`). Between snapshots we project each one forward along its track at its
// ground speed. When a fresh position arrives that disagrees with our
// projection, the difference is eased out over a second instead of jumping.

import { destination } from "./util.js";
import { shapeFor, SHAPE_SCALE } from "./icons.js";

const MAX_EXTRAPOLATE_S = 45;
const CORRECTION_S = 1.2;
const DROP_AFTER_S = 20;

export class Fleet {
  constructor() {
    this.planes = new Map();
    this.clockOffset = 0;   // provider clock minus ours, so projection matches their timestamps
    this.lastSnapshot = 0;  // our clock, seconds
    this.source = "";
  }

  now() {
    return Date.now() / 1000 + this.clockOffset;
  }

  update(snapshot) {
    const local = Date.now() / 1000;
    const offset = snapshot.now - local;
    this.clockOffset = this.lastSnapshot ? this.clockOffset * 0.8 + offset * 0.2 : offset;
    this.lastSnapshot = local;
    this.source = snapshot.source;

    const now = this.now();
    for (const a of snapshot.ac) {
      const prev = this.planes.get(a.hex);
      const plane = { ...a, seen: snapshot.now, shape: shapeFor(a.type, a.cat) };
      if (prev) {
        const [oldLat, oldLon] = this.position(prev, now);
        const [newLat, newLon] = project(plane, now);
        let dLon = oldLon - newLon;
        if (dLon > 180) dLon -= 360;
        if (dLon < -180) dLon += 360;
        // Only ease small corrections; a big jump is a real jump.
        if (Math.abs(oldLat - newLat) < 0.05 && Math.abs(dLon) < 0.05) {
          plane.corr = { dLat: oldLat - newLat, dLon, until: now + CORRECTION_S };
        }
      }
      this.planes.set(a.hex, plane);
    }
    for (const [hex, p] of this.planes) {
      if (p.seen < snapshot.now - DROP_AFTER_S) this.planes.delete(hex);
    }
  }

  get(hex) {
    return this.planes.get(hex);
  }

  position(p, now = this.now()) {
    let [lat, lon] = project(p, now);
    if (p.corr && now < p.corr.until) {
      const f = (p.corr.until - now) / CORRECTION_S;
      lat += p.corr.dLat * f;
      lon += p.corr.dLon * f;
    }
    return [lat, lon];
  }

  // GeoJSON for planes inside `bounds` (a padded LngLatBounds), plus `alwaysHex`.
  features(bounds, alwaysHex) {
    const now = this.now();
    const out = [];
    for (const p of this.planes.values()) {
      const [lat, lon] = this.position(p, now);
      if (p.hex !== alwaysHex && !bounds.contains([lon, lat])) continue;
      out.push({
        type: "Feature",
        id: p.hex,
        geometry: { type: "Point", coordinates: [lon, lat] },
        properties: {
          hex: p.hex,
          icon: `ac-${p.shape}`,
          scale: SHAPE_SCALE[p.shape] ?? 0.75,
          alt: p.alt ?? 0,
          gnd: p.gnd,
          trk: p.trk ?? 0,
          label: p.cs || p.reg || "",
          alert: Boolean(p.emerg) || ["7500", "7600", "7700"].includes(p.sq),
        },
      });
    }
    return { type: "FeatureCollection", features: out };
  }
}

function project(p, now) {
  const dt = Math.min(MAX_EXTRAPOLATE_S, Math.max(0, now - p.t));
  if (!p.gs || p.trk == null || dt === 0 || (p.gnd && p.gs < 3)) return [p.lat, p.lon];
  return destination(p.lat, p.lon, p.trk, (p.gs * dt) / 3600);
}
