// The status card that slides in when an aircraft is tapped.
//
// Two lookups start together on tap: free details (photo, aircraft, airline,
// crowd-sourced route) and FlightAware AeroAPI (times and the filed route).
// The free route shows first and is replaced by FlightAware's when it lands.
// AeroAPI results are cached until the flight lands, so re-taps cost nothing.

import { el, fmtAgo, fmtAlt, fmtInt, fmtTime, distanceNm } from "./util.js";

export class Card {
  constructor(root, { onClose, onRefreshTimes }) {
    this.root = root;
    this.onClose = onClose;
    this.onRefreshTimes = onRefreshTimes;
    this.plane = null;
    this.details = null;
    this.times = null;
    this.timesError = null;
    this.loadingTimes = false;
    this.budget = null;
  }

  get open() {
    return this.plane !== null;
  }

  show(plane, { loadingTimes = false } = {}) {
    this.plane = plane;
    this.details = null;
    this.times = null;
    this.timesError = null;
    this.loadingTimes = loadingTimes;
    this.lost = false;
    this.root.classList.add("open");
    this.root.scrollTop = 0;
    this.render();
  }

  hide() {
    this.plane = null;
    this.root.classList.remove("open");
  }

  setDetails(details) {
    this.details = details;
    this.times ||= details.times || null;
    this.budget = details.budget;
    this.render();
  }

  setTimesLoading() {
    this.loadingTimes = true;
    this.timesError = null;
    this.render();
  }

  setTimes(result) {
    this.loadingTimes = false;
    this.budget = result.budget || this.budget;
    if (result.error) this.timesError = { message: result.error, kind: result.kind };
    else this.times = result.times;
    this.render();
  }

  // FlightAware's filed route wins over the crowd-sourced one.
  get route() {
    return this.times?.route || this.details?.route || null;
  }

  // Called about once a second with the latest state for the shown aircraft.
  updateLive(plane, position) {
    if (!this.open) return;
    this.lost = !plane;
    if (plane) this.plane = plane;
    this.position = position;
    const live = this.root.querySelector(".live");
    if (live) live.replaceWith(this.renderLive());
    const prog = this.root.querySelector(".route");
    if (prog && this.route) prog.replaceWith(this.renderRoute(this.route));
  }

  // ------------------------------------------------------------ rendering

  render() {
    if (!this.plane) return;
    const p = this.plane;
    const d = this.details;
    const info = d?.aircraft || {};
    const title = p.cs || p.reg || info.registration || p.hex.toUpperCase();
    // The feed's owner is often a leasing bank, so prefer the airline from the callsign.
    const subtitle = d ? (d.airline || info.owner || p.op || p.desc || "") : (p.desc || "");

    const parts = [
      el("header", { class: "card-head" },
        el("div", {},
          el("div", { class: "card-title" }, title),
          subtitle && el("div", { class: "card-sub" }, subtitle)),
        el("button", { class: "icon-btn", "aria-label": "Close", onclick: () => this.onClose() }, "✕")),
      this.renderPhoto(d),
      this.route && this.renderRoute(this.route),
      this.renderTimes(),
      this.renderLive(),
      this.renderAircraft(info),
      !d && el("div", { class: "muted pad" }, "Loading details…"),
    ];
    this.root.replaceChildren(...parts.filter(Boolean));
  }

  renderPhoto(d) {
    if (!d?.photo?.src) return null;
    return el("figure", { class: "photo" },
      el("img", { src: d.photo.src, alt: "" }),
      el("figcaption", {}, `© ${d.photo.photographer || "unknown"} · planespotters.net`));
  }

  renderRoute(route) {
    const { origin: o, destination: dst } = route;
    let progress = null;
    if (route.plausible !== false) {
      if (this.position && o.lat != null && dst.lat != null) {
        const [lat, lon] = this.position;
        const flown = distanceNm(o.lat, o.lon, lat, lon);
        const togo = distanceNm(lat, lon, dst.lat, dst.lon);
        progress = progressBar((100 * flown) / (flown + togo || 1),
          `${fmtInt(flown)} nm flown`, `${fmtInt(togo)} nm to go`);
      } else if (this.times?.route === route && this.times.progress_percent != null) {
        progress = progressBar(this.times.progress_percent, `${this.times.progress_percent}% complete`, "");
      }
    }
    const end = (a) => el("div", { class: "route-end" },
      el("div", { class: "code" }, a.iata || a.icao || a.code || "—"),
      el("div", { class: "city" }, a.city || a.name));
    const fromFA = route.source === "FlightAware";
    return el("section", { class: "route" },
      el("div", { class: "route-row" }, end(o), el("div", { class: "route-arrow" }, "→"), end(dst)),
      progress,
      route.plausible === false && el("div", { class: "warn" }, "Route may be out of date for this callsign."),
      el("div", { class: "source-note" },
        fromFA ? "Filed route · FlightAware"
          : this.loadingTimes ? "Usual route for this flight number · checking FlightAware…"
            : "Usual route for this flight number"));
  }

  renderTimes() {
    const section = (...children) => el("section", { class: "times" }, ...children);
    const err = this.timesError;

    if (this.times) return section(...this.renderTimesTable(this.times));
    if (this.loadingTimes) return section(el("div", { class: "muted" }, "Getting flight times from FlightAware…"));
    if (!err || err.kind === "disabled") return null;
    if (err.kind === "no_flight") return section(el("div", { class: "muted" }, "No FlightAware flight plan found for this aircraft."));
    if (err.kind === "limit") return section(el("div", { class: "notice" }, err.message));
    return section(
      el("div", { class: "notice" }, err.message),
      el("button", { class: "link-btn retry", onclick: () => this.onRefreshTimes(this.plane) }, "Try again"));
  }

  renderTimesTable(t) {
    const otz = t.origin?.timezone, dtz = t.destination?.timezone;
    const row = (label, value, cls = "") =>
      value ? el("div", { class: `trow ${cls}` }, el("span", {}, label), el("span", {}, value)) : null;
    const delay = (s) => (s && Math.abs(s) >= 300 ? ` (${s > 0 ? "+" : "−"}${Math.round(Math.abs(s) / 60)}m)` : "");

    const depActual = t.actual_out || t.estimated_out;
    const arrActual = t.actual_in || t.estimated_in;
    const gate = (term, g) => [term && `T${term}`, g && `Gate ${g}`].filter(Boolean).join(" · ");
    const err = this.timesError;

    return [
      el("div", { class: "status-line" },
        el("span", {}, t.status || "—"),
        t.diverted && el("span", { class: "warn" }, "Diverted")),
      el("div", { class: "tcols" },
        el("div", { class: "tcol" },
          el("div", { class: "tcol-head" }, "Departure"),
          row("Scheduled", fmtTime(t.scheduled_out, otz)),
          row(t.actual_out ? "Left gate" : "Expected", fmtTime(depActual, otz) && fmtTime(depActual, otz) + delay(t.departure_delay)),
          row("Takeoff", fmtTime(t.actual_off, otz)),
          row("", gate(t.terminal_origin, t.gate_origin), "gate")),
        el("div", { class: "tcol" },
          el("div", { class: "tcol-head" }, "Arrival"),
          row("Scheduled", fmtTime(t.scheduled_in, dtz)),
          row(t.actual_in ? "Arrived" : "Expected", fmtTime(arrActual, dtz) && fmtTime(arrActual, dtz) + delay(t.arrival_delay)),
          row("Landing", fmtTime(t.actual_on, dtz)),
          row("", gate(t.terminal_destination, t.gate_destination), "gate"))),
      el("div", { class: "times-foot" },
        el("span", { class: "muted" }, `Updated ${fmtAgo(Date.now() / 1000 - t.fetched_at)}`),
        this.budget?.exhausted ? null : el("button", {
          class: "link-btn", disabled: this.loadingTimes, onclick: () => this.onRefreshTimes(this.plane),
        }, this.loadingTimes ? "Updating…" : "Refresh")),
      err && el("div", { class: "notice" }, err.message),
    ];
  }

  renderLive() {
    const p = this.plane;
    const vr = p.vr == null || p.gnd ? "—" : `${p.vr > 0 ? "↑" : p.vr < 0 ? "↓" : ""} ${fmtInt(Math.abs(p.vr))} ft/min`;
    const cell = (label, value) => el("div", { class: "cell" },
      el("div", { class: "label" }, label), el("div", { class: "value" }, value));
    return el("section", { class: "live" + (this.lost ? " lost" : "") },
      this.lost && el("div", { class: "notice" }, "Signal lost. Showing last known data."),
      el("div", { class: "grid" },
        cell("Altitude", fmtAlt(p)),
        cell("Vertical", vr),
        cell("Speed", p.gs == null ? "—" : `${fmtInt(p.gs)} kt`),
        cell("Track", p.trk == null ? "—" : `${fmtInt(p.trk)}°`),
        cell("Squawk", p.sq || "—"),
        cell("Mode S", p.hex.toUpperCase())));
  }

  renderAircraft(info) {
    const p = this.plane;
    const d = this.details;
    const rows = [
      ["Aircraft", p.desc || info.model || p.type],
      ["Type code", p.type || info.type],
      ["Registration", p.reg || info.registration],
      ["Operator", d?.airline || info.owner || p.op],
      ["Owner", p.op && !sameCompany(p.op, d?.airline || info.owner) ? p.op : null],
      ["Country", info.country],
    ].filter(([, v]) => v);
    if (!rows.length) return null;
    return el("section", { class: "aircraft" },
      ...rows.map(([k, v]) => el("div", { class: "trow" }, el("span", {}, k), el("span", {}, v))));
  }
}

// "SOUTHWEST AIRLINES CO" and "Southwest Airlines" are the same company.
function sameCompany(a, b) {
  if (!a || !b) return false;
  const first = (s) => s.toLowerCase().split(/\s+/)[0];
  return first(a) === first(b);
}

function progressBar(pct, left, right) {
  pct = Math.max(0, Math.min(100, pct));
  return el("div", { class: "progress" },
    el("div", { class: "bar" }, el("div", { class: "fill", style: `width:${pct}%` })),
    el("div", { class: "progress-text" }, el("span", {}, left), el("span", {}, right)));
}
