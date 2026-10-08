// Geo math (nautical miles, degrees) and display formatting.

const R_NM = 3440.065;
const rad = (d) => (d * Math.PI) / 180;
const deg = (r) => (r * 180) / Math.PI;

export function distanceNm(lat1, lon1, lat2, lon2) {
  const p1 = rad(lat1), p2 = rad(lat2);
  const a = Math.sin((p2 - p1) / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * R_NM * Math.asin(Math.min(1, Math.sqrt(a)));
}

export function destination(lat, lon, bearingDeg, distNm) {
  const d = distNm / R_NM, b = rad(bearingDeg), p1 = rad(lat), l1 = rad(lon);
  const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(b));
  const l2 = l1 + Math.atan2(Math.sin(b) * Math.sin(d) * Math.cos(p1),
    Math.cos(d) - Math.sin(p1) * Math.sin(p2));
  return [deg(p2), ((deg(l2) + 540) % 360) - 180];
}

// Points along the great circle from a to b ([lon, lat] pairs), split at the
// antimeridian is left to MapLibre (we unwrap longitudes so lines stay continuous).
export function greatCircle(lat1, lon1, lat2, lon2, steps = 64) {
  const p1 = rad(lat1), l1 = rad(lon1), p2 = rad(lat2), l2 = rad(lon2);
  const d = 2 * Math.asin(Math.sqrt(Math.sin((p2 - p1) / 2) ** 2 +
    Math.cos(p1) * Math.cos(p2) * Math.sin((l2 - l1) / 2) ** 2));
  if (d === 0) return [[lon1, lat1]];
  const pts = [];
  let prevLon = null;
  for (let i = 0; i <= steps; i++) {
    const f = i / steps;
    const A = Math.sin((1 - f) * d) / Math.sin(d), B = Math.sin(f * d) / Math.sin(d);
    const x = A * Math.cos(p1) * Math.cos(l1) + B * Math.cos(p2) * Math.cos(l2);
    const y = A * Math.cos(p1) * Math.sin(l1) + B * Math.cos(p2) * Math.sin(l2);
    const z = A * Math.sin(p1) + B * Math.sin(p2);
    let lon = deg(Math.atan2(y, x));
    if (prevLon !== null) {
      while (lon - prevLon > 180) lon -= 360;
      while (lon - prevLon < -180) lon += 360;
    }
    prevLon = lon;
    pts.push([lon, deg(Math.atan2(z, Math.hypot(x, y)))]);
  }
  return pts;
}

// ---------------------------------------------------------------- formatting

export const fmtInt = (n) => (n == null ? "—" : Math.round(n).toLocaleString("en-US"));

export function fmtAlt(a) {
  if (a.gnd) return "Ground";
  return a.alt == null ? "—" : `${fmtInt(a.alt)} ft`;
}

export function fmtTime(iso, tz) {
  if (!iso) return null;
  const d = new Date(iso);
  const opts = { hour: "2-digit", minute: "2-digit", hour12: false, timeZoneName: "short" };
  try {
    return d.toLocaleTimeString("en-US", { ...opts, timeZone: tz || undefined });
  } catch {
    return d.toLocaleTimeString("en-US", opts);
  }
}

export function fmtAgo(seconds) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  return `${Math.round(seconds / 3600)}h ago`;
}

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
