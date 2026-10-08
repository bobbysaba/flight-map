// A deliberately minimal dark basemap on OpenFreeMap's free vector tiles
// (OpenMapTiles schema). Muted so the coloured planes carry the screen.

const C = {
  land: "#0e1218",
  water: "#141c27",
  border: "#3a4350",
  state: "#232a34",
  road: "#1c222b",
  roadMajor: "#232a35",
  runway: "#2c3440",
  airportText: "#6f7a89",
  cityText: "#8c96a3",
  townText: "#66707d",
  countryText: "#596371",
  halo: "#0e1218",
};

const FONT = ["Noto Sans Regular"];
const FONT_BOLD = ["Noto Sans Bold"];

export const basemapStyle = {
  version: 8,
  glyphs: "https://tiles.openfreemap.org/fonts/{fontstack}/{range}.pbf",
  sources: {
    omt: {
      type: "vector",
      url: "https://tiles.openfreemap.org/planet",
      attribution:
        '<a href="https://openfreemap.org">OpenFreeMap</a> © <a href="https://www.openmaptiles.org/">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    },
  },
  layers: [
    { id: "background", type: "background", paint: { "background-color": C.land } },
    {
      id: "water", type: "fill", source: "omt", "source-layer": "water",
      paint: { "fill-color": C.water },
    },
    {
      id: "boundary-state", type: "line", source: "omt", "source-layer": "boundary",
      filter: ["all", ["==", ["get", "admin_level"], 4], ["!=", ["get", "maritime"], 1]],
      minzoom: 4,
      paint: { "line-color": C.state, "line-width": 0.8, "line-dasharray": [3, 2] },
    },
    {
      id: "boundary-country", type: "line", source: "omt", "source-layer": "boundary",
      filter: ["all", ["==", ["get", "admin_level"], 2], ["!=", ["get", "maritime"], 1],
        ["!=", ["get", "disputed"], 1]],
      paint: { "line-color": C.border, "line-width": ["interpolate", ["linear"], ["zoom"], 2, 0.6, 8, 1.4] },
    },
    {
      id: "road-major", type: "line", source: "omt", "source-layer": "transportation",
      filter: ["match", ["get", "class"], ["motorway", "trunk"], true, false],
      minzoom: 5,
      paint: {
        "line-color": C.roadMajor,
        "line-width": ["interpolate", ["linear"], ["zoom"], 5, 0.5, 12, 2.5],
      },
    },
    {
      id: "road-primary", type: "line", source: "omt", "source-layer": "transportation",
      filter: ["match", ["get", "class"], ["primary", "secondary"], true, false],
      minzoom: 9,
      paint: { "line-color": C.road, "line-width": ["interpolate", ["linear"], ["zoom"], 9, 0.5, 14, 2] },
    },
    {
      id: "runway-area", type: "fill", source: "omt", "source-layer": "aeroway",
      filter: ["all", ["==", ["geometry-type"], "Polygon"],
        ["match", ["get", "class"], ["runway", "taxiway"], true, false]],
      minzoom: 10,
      paint: { "fill-color": C.runway },
    },
    {
      id: "runway", type: "line", source: "omt", "source-layer": "aeroway",
      filter: ["all", ["==", ["geometry-type"], "LineString"], ["==", ["get", "class"], "runway"]],
      minzoom: 8,
      paint: { "line-color": C.runway, "line-width": ["interpolate", ["exponential", 2], ["zoom"], 8, 1, 14, 24] },
    },
    {
      id: "airport-label", type: "symbol", source: "omt", "source-layer": "aerodrome_label",
      filter: ["has", "iata"],
      minzoom: 7,
      layout: {
        "text-field": ["get", "iata"],
        "text-font": FONT_BOLD,
        "text-size": 11,
        "text-offset": [0, 1.2],
      },
      paint: { "text-color": C.airportText, "text-halo-color": C.halo, "text-halo-width": 1.2 },
    },
    {
      id: "place-town", type: "symbol", source: "omt", "source-layer": "place",
      filter: ["match", ["get", "class"], ["town"], true, false],
      minzoom: 9,
      layout: { "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]], "text-font": FONT, "text-size": 11 },
      paint: { "text-color": C.townText, "text-halo-color": C.halo, "text-halo-width": 1.2 },
    },
    {
      id: "place-city", type: "symbol", source: "omt", "source-layer": "place",
      filter: ["==", ["get", "class"], "city"],
      minzoom: 4,
      layout: {
        "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
        "text-font": FONT,
        "text-size": ["interpolate", ["linear"], ["zoom"], 4, 11, 10, 14],
        "symbol-sort-key": ["get", "rank"],
      },
      paint: { "text-color": C.cityText, "text-halo-color": C.halo, "text-halo-width": 1.4 },
    },
    {
      id: "place-country", type: "symbol", source: "omt", "source-layer": "place",
      filter: ["==", ["get", "class"], "country"],
      maxzoom: 7,
      layout: {
        "text-field": ["coalesce", ["get", "name:en"], ["get", "name"]],
        "text-font": FONT_BOLD,
        "text-size": 12,
        "text-transform": "uppercase",
        "text-letter-spacing": 0.15,
      },
      paint: { "text-color": C.countryText, "text-halo-color": C.halo, "text-halo-width": 1.4 },
    },
  ],
};
