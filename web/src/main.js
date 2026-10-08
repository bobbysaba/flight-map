import * as maplibregl from "../vendor/maplibre-gl.mjs";
import { basemapStyle } from "./style.js";
import { addAircraftIcons } from "./icons.js";
import { Fleet } from "./planes.js";
import { Card } from "./card.js";
import { StatusPanel } from "./status.js";
import { distanceNm, destination, greatCircle, fmtAgo } from "./util.js";

const MAX_RADIUS_NM = 250;
const FRAME_MS = 50;        // ~20 fps is plenty for planes and easy on the Pi
const HIT_PX = 16;          // generous tap target around each plane

// Altitude colours (ft), shared by plane icons and trails.
const ALT_COLORS = [
  0, "#ff7b3a",
  2000, "#ffa733",
  6000, "#ffd23f",
  12000, "#c7e04a",
  20000, "#5fd068",
  28000, "#38c9d6",
  36000, "#5b8cff",
  44000, "#b071ff",
];
const altColor = (prop) => ["interpolate", ["linear"], ["to-number", ["get", prop], 0], ...ALT_COLORS];

const params = new URLSearchParams(location.search);
if (params.has("kiosk")) document.body.classList.add("kiosk");
document.addEventListener("contextmenu", (e) => e.preventDefault());

const fleet = new Fleet();
let map, ws, cfg;
let activeArea = null;
let selectedHex = null;
let selectedTrail = [];
let lastStatus = { ok: true };

const ui = {
  status: document.getElementById("status"),
  search: document.getElementById("search-here"),
  home: document.getElementById("home"),
};
const card = new Card(document.getElementById("card"), {
  onClose: () => select(null),
  onRefreshTimes: (plane) => fetchFlight(plane, true),
});
const statusPanel = new StatusPanel(document.getElementById("status-panel"));
ui.status.addEventListener("click", () => statusPanel.open());

// ---------------------------------------------------------------- boot

cfg = await (await fetch("/api/config")).json();

map = new maplibregl.Map({
  container: "map",
  style: basemapStyle,
  center: [cfg.start.lon, cfg.start.lat],
  zoom: cfg.start.zoom,
  attributionControl: { compact: true },
  dragRotate: false,
  pitchWithRotate: false,
  maxPitch: 0,
  renderWorldCopies: true,
});
map.touchZoomRotate.disableRotation();
map.keyboard.disableRotation();
map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-left");
window.flightmap = { map, fleet, card, statusPanel };  // handy from the devtools console

map.on("load", () => {
  // Start with the attribution collapsed to its (i) button.
  document.querySelector(".maplibregl-ctrl-attrib")?.classList.remove("maplibregl-compact-show");
  addAircraftIcons(map);
  addLayers();
  setArea(viewArea());
  connect();
  setInterval(frame, FRAME_MS);
  setInterval(tick, 1000);
});

map.on("moveend", updateSearchButton);
map.on("click", onTap);
ui.search.addEventListener("click", () => setArea(viewArea()));
ui.home.addEventListener("click", () => {
  map.flyTo({ center: [cfg.start.lon, cfg.start.lat], zoom: cfg.start.zoom });
});

// ---------------------------------------------------------------- layers

function addLayers() {
  map.addSource("area", { type: "geojson", data: empty() });
  map.addSource("trail", { type: "geojson", data: empty() });
  map.addSource("route", { type: "geojson", data: empty() });
  map.addSource("planes", { type: "geojson", data: empty(), promoteId: "hex" });

  map.addLayer({
    id: "area", type: "line", source: "area",
    paint: { "line-color": "#5b6b80", "line-width": 1.2, "line-dasharray": [2, 3], "line-opacity": 0.7 },
  });
  map.addLayer({
    id: "route", type: "line", source: "route",
    layout: { "line-cap": "round" },
    paint: { "line-color": "#c9d3df", "line-width": 1.5, "line-dasharray": [2, 3], "line-opacity": 0.6 },
  });
  map.addLayer({
    id: "trail", type: "line", source: "trail",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": altColor("alt"), "line-width": 2.5 },
  });
  map.addLayer({
    id: "planes", type: "symbol", source: "planes",
    layout: {
      "icon-image": ["get", "icon"],
      "icon-size": ["interpolate", ["linear"], ["zoom"],
        3, ["*", 0.45, ["get", "scale"]],
        7, ["*", 0.75, ["get", "scale"]],
        11, ["*", 1.05, ["get", "scale"]]],
      "icon-rotate": ["get", "trk"],
      "icon-rotation-alignment": "map",
      "icon-allow-overlap": true,
      "icon-ignore-placement": true,
      "symbol-sort-key": ["get", "alt"],
    },
    paint: {
      "icon-color": ["case",
        ["get", "alert"], "#ff3b4e",
        ["get", "gnd"], "#8b95a3",
        altColor("alt")],
      "icon-halo-color": ["case", ["boolean", ["feature-state", "selected"], false], "#ffffff", "#06080b"],
      "icon-halo-width": ["case", ["boolean", ["feature-state", "selected"], false], 2.2, 1.2],
    },
  });
  map.addLayer({
    id: "plane-labels", type: "symbol", source: "planes", minzoom: 8.5,
    layout: {
      "text-field": ["get", "label"],
      "text-font": ["Noto Sans Regular"],
      "text-size": 10,
      "text-offset": [0, 1.7],
      "text-optional": true,
    },
    paint: { "text-color": "#a7b1bd", "text-halo-color": "#0e1218", "text-halo-width": 1.2 },
  });
}

const empty = () => ({ type: "FeatureCollection", features: [] });

// ---------------------------------------------------------------- live data

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => {
    if (activeArea) ws.send(JSON.stringify({ type: "area", ...activeArea }));
  };
  ws.onmessage = (e) => {
    const msg = JSON.parse(e.data);
    if (msg.type === "snapshot") {
      fleet.update(msg);
      lastStatus = { ok: true };
    } else if (msg.type === "status") {
      lastStatus = msg;
    }
    renderStatus();
  };
  ws.onclose = () => {
    lastStatus = { ok: false, error: "Lost connection to the flight map service" };
    renderStatus();
    setTimeout(connect, 2000);
  };
}

function frame() {
  const src = map.getSource("planes");
  if (!src) return;
  const b = map.getBounds();
  const padLat = (b.getNorth() - b.getSouth()) * 0.15;
  const padLon = (b.getEast() - b.getWest()) * 0.15;
  const padded = new maplibregl.LngLatBounds(
    [b.getWest() - padLon, b.getSouth() - padLat],
    [b.getEast() + padLon, b.getNorth() + padLat],
  );
  src.setData(fleet.features(padded, selectedHex));
}

function tick() {
  renderStatus();
  if (selectedHex) updateSelected();
}

function renderStatus() {
  const age = fleet.lastSnapshot ? Date.now() / 1000 - fleet.lastSnapshot : Infinity;
  const stale = age > cfg.poll_interval_s * 3 + 2;
  let cls = "ok", text;
  if (!lastStatus.ok && (stale || !fleet.lastSnapshot)) {
    cls = "error";
    text = fleet.lastSnapshot ? `No data · last update ${fmtAgo(age)}` : "No data · retrying";
  } else if (!fleet.lastSnapshot) {
    cls = "stale";
    text = "Connecting…";
  } else {
    cls = stale ? "stale" : "ok";
    text = `${fleet.source} · ${fleet.planes.size} aircraft${stale ? ` · ${fmtAgo(age)}` : ""}`;
  }
  ui.status.className = `pill ${cls}`;
  ui.status.textContent = text;
  ui.status.title = lastStatus.ok ? "" : lastStatus.error || "";
}

// ---------------------------------------------------------------- area / "Search here"

function viewArea() {
  const c = map.getCenter();
  const { width, height } = map.getCanvas().getBoundingClientRect();
  let r = 0;
  for (const [x, y] of [[0, 0], [width, 0], [0, height], [width, height]]) {
    const p = map.unproject([x, y]);
    r = Math.max(r, distanceNm(c.lat, c.lng, p.lat, p.lng));
  }
  return { lat: c.lat, lon: wrapLon(c.lng), radius_nm: Math.min(MAX_RADIUS_NM, Math.ceil(r)) };
}

function setArea(area) {
  activeArea = area;
  if (ws?.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "area", ...area }));
  map.getSource("area").setData(circle(area));
  updateSearchButton();
}

function updateSearchButton() {
  if (!activeArea) return;
  const want = viewArea();
  const covered = viewCorners().every(
    (p) => distanceNm(activeArea.lat, activeArea.lon, p.lat, p.lng) <= activeArea.radius_nm,
  );
  const moved = distanceNm(activeArea.lat, activeArea.lon, want.lat, want.lon) > 0.05 * activeArea.radius_nm;
  const grew = want.radius_nm > activeArea.radius_nm * 1.05;
  const showSearch = !covered && (moved || grew);
  ui.search.classList.toggle("show", showSearch);
  // Outline the fetched area whenever part of the view lies outside it.
  map.setLayoutProperty("area", "visibility", covered ? "none" : "visible");
}

function viewCorners() {
  const { width, height } = map.getCanvas().getBoundingClientRect();
  return [[0, 0], [width, 0], [0, height], [width, height]].map((pt) => map.unproject(pt));
}

function circle({ lat, lon, radius_nm }) {
  const ring = [];
  for (let i = 0; i <= 128; i++) {
    const [la, lo] = destination(lat, lon, (i * 360) / 128, radius_nm);
    ring.push([lo, la]);
  }
  for (let i = 1; i < ring.length; i++) {  // keep it continuous across the antimeridian
    while (ring[i][0] - ring[i - 1][0] > 180) ring[i][0] -= 360;
    while (ring[i][0] - ring[i - 1][0] < -180) ring[i][0] += 360;
  }
  return { type: "Feature", geometry: { type: "LineString", coordinates: ring }, properties: {} };
}

const wrapLon = (lon) => ((((lon + 180) % 360) + 360) % 360) - 180;

// ---------------------------------------------------------------- selection

function onTap(e) {
  const { x, y } = e.point;
  const hits = map.queryRenderedFeatures([[x - HIT_PX, y - HIT_PX], [x + HIT_PX, y + HIT_PX]], { layers: ["planes"] });
  if (!hits.length) {
    if (selectedHex) select(null);
    return;
  }
  const nearest = hits.reduce((best, f) => {
    const p = map.project(f.geometry.coordinates);
    const d = Math.hypot(p.x - x, p.y - y);
    return d < best.d ? { f, d } : best;
  }, { f: null, d: Infinity }).f;
  select(nearest.properties.hex);
}

async function select(hex) {
  if (selectedHex) map.setFeatureState({ source: "planes", id: selectedHex }, { selected: false });
  selectedHex = hex;
  selectedTrail = [];
  map.getSource("trail").setData(empty());
  map.getSource("route").setData(empty());
  if (!hex) {
    card.hide();
    return;
  }
  const plane = fleet.get(hex);
  if (!plane) return;
  map.setFeatureState({ source: "planes", id: hex }, { selected: true });
  // Ground vehicles and aircraft with no callsign or registration have nothing to look up.
  const lookup = !plane.cat.startsWith("C") && Boolean(plane.cs || plane.reg);
  card.show(plane, { loadingTimes: lookup });
  const [lat, lon] = fleet.position(plane);
  card.updateLive(plane, [lat, lon]);

  if (lookup) fetchFlight(plane, false);
  const q = new URLSearchParams({ callsign: plane.cs, lat, lon });
  try {
    const details = await (await fetch(`/api/aircraft/${hex}?${q}`)).json();
    if (selectedHex !== hex) return;
    selectedTrail = details.trail || [];
    card.setDetails(details);
    updateSelected();
  } catch {
    if (selectedHex === hex) card.setDetails({ budget: null });
  }
}

// FlightAware times + filed route. Served from cache unless `refresh` is set.
async function fetchFlight(plane, refresh) {
  const hex = plane.hex;
  if (refresh) card.setTimesLoading();
  let result;
  try {
    const resp = await fetch(`/api/aircraft/${hex}/flight`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ callsign: plane.cs, registration: plane.reg, refresh }),
    });
    result = await resp.json();
  } catch {
    result = { error: "Flight times unavailable: couldn't reach the flight map service.", kind: "error" };
  }
  if (selectedHex !== hex) return;
  card.setTimes(result);
  updateSelected();
}

function updateSelected() {
  const plane = fleet.get(selectedHex);
  if (!plane) {
    card.updateLive(null, null);
    return;
  }
  const [lat, lon] = fleet.position(plane);
  card.updateLive(plane, [lat, lon]);

  const last = selectedTrail[selectedTrail.length - 1];
  if (!last || last[0] < plane.t) selectedTrail.push([plane.t, plane.lat, plane.lon, plane.alt]);

  const pts = [...selectedTrail, [0, lat, lon, plane.alt]];
  const segments = [];
  for (let i = 1; i < pts.length; i++) {
    segments.push({
      type: "Feature",
      properties: { alt: pts[i][3] ?? 0 },
      geometry: { type: "LineString", coordinates: [[pts[i - 1][2], pts[i - 1][1]], [pts[i][2], pts[i][1]]] },
    });
  }
  map.getSource("trail").setData({ type: "FeatureCollection", features: segments });

  const route = card.route;
  const dst = route?.destination;
  if (dst?.lat != null && route.plausible !== false && !plane.gnd) {
    map.getSource("route").setData({
      type: "Feature", properties: {},
      geometry: { type: "LineString", coordinates: greatCircle(lat, lon, dst.lat, dst.lon) },
    });
  } else {
    map.getSource("route").setData(empty());
  }
}
