// The status panel: are the data sources up, and how much AeroAPI allowance is left.
// Opened by tapping the status pill. Every check it runs is free.

import { el, fmtAgo } from "./util.js";

export class StatusPanel {
  constructor(root) {
    this.root = root;
    this.data = null;
    this.checks = null;
    this.checking = false;
    root.addEventListener("click", (e) => {
      if (e.target === root) this.close();  // tap outside the sheet
    });
  }

  get isOpen() {
    return this.root.classList.contains("open");
  }

  async open() {
    this.root.classList.add("open");
    this.render();
    try {
      this.data = await (await fetch("/api/status")).json();
    } catch {
      this.data = null;
    }
    this.render();
    this.runChecks();
  }

  close() {
    this.root.classList.remove("open");
  }

  async runChecks() {
    if (this.checking) return;
    this.checking = true;
    this.render();
    try {
      const result = await (await fetch("/api/status/check", { method: "POST" })).json();
      this.checks = result.checks;
      this.data = { providers: result.providers, budget: result.budget };
    } catch {
      this.checks = [{ name: "Flight map service", group: "Live positions", ok: false, detail: "Not reachable" }];
    }
    this.checking = false;
    this.render();
  }

  // ------------------------------------------------------------ rendering

  render() {
    if (!this.isOpen) return;
    const sheet = el("div", { class: "sheet" },
      el("header", { class: "sheet-head" },
        el("div", { class: "card-title" }, "Status"),
        el("button", { class: "icon-btn", "aria-label": "Close", onclick: () => this.close() }, "✕")),
      el("div", { class: "sheet-body" },
        el("div", { class: "sheet-col" },
          this.renderSources(),
          el("button", { class: "primary-btn", disabled: this.checking, onclick: () => this.runChecks() },
            this.checking ? "Checking…" : "Run checks again")),
        el("div", { class: "sheet-col" }, this.renderBudget())));
    this.root.replaceChildren(sheet);
  }

  renderSources() {
    const providers = this.data?.providers || [];
    const checks = this.checks || [];
    const byName = Object.fromEntries(checks.map((c) => [c.name, c]));
    const now = Date.now() / 1000;

    const liveRows = providers.map((p) => {
      const c = byName[p.name];
      let ok = c ? c.ok : p.last_error_at > (p.last_ok_at || 0) ? false : p.last_ok_at ? true : null;
      let detail;
      if (c && c.ok) detail = `${c.ms} ms · ${c.detail}`;
      else if (c) detail = c.detail;
      else if (p.last_ok_at) detail = `Last answered ${fmtAgo(now - p.last_ok_at)}${p.last_ms ? ` · ${p.last_ms} ms` : ""}`;
      else detail = "Not used yet";
      if (p.cooling_down_s) detail += ` · resting ${p.cooling_down_s}s after errors`;
      if (!ok && p.last_error && !c) detail = `${p.last_error} (${fmtAgo(now - p.last_error_at)})`;
      return row(p.name, ok, detail, p.in_use && "In use");
    });

    const groups = {};
    for (const c of checks) {
      if (c.group === "Live positions") continue;
      (groups[c.group] ||= []).push(row(c.name, c.ok, c.ms ? `${c.ms} ms · ${c.detail}` : c.detail));
    }
    const pending = this.checking && !checks.length;

    return el("div", {},
      el("div", { class: "section-head" }, "Live positions"),
      liveRows.length ? liveRows : el("div", { class: "muted" }, "Loading…"),
      ...Object.entries(groups).flatMap(([g, rows]) => [el("div", { class: "section-head" }, g), ...rows]),
      pending && el("div", { class: "muted pad-y" }, "Checking card and flight-time sources…"));
  }

  renderBudget() {
    const b = this.data?.budget;
    const head = el("div", { class: "section-head" }, "FlightAware AeroAPI");
    if (!b) return el("div", {}, head, el("div", { class: "muted" }, "Loading…"));
    if (!b.enabled) return el("div", {}, head, el("div", { class: "notice" }, "Off: add an AeroAPI key to the config."));

    const pct = Math.min(100, (100 * b.spent) / b.limit);
    const fa = b.flightaware_reported;
    return el("div", {}, head,
      el("div", { class: "big-num" }, `${b.lookups_left.toLocaleString("en-US")}`,
        el("span", {}, " lookups left")),
      el("div", { class: "bar budget-bar" }, el("div", { class: `fill${b.exhausted ? " bad" : ""}`, style: `width:${pct}%` })),
      kv("Used this month", `${plural(b.lookups_used, "lookup")} · $${b.spent.toFixed(3)}`),
      kv("Monthly cap", `$${b.limit.toFixed(2)} (of $10 free)`),
      kv("Price per lookup", `$${b.cost_per_call}`),
      kv("Resets", fmtDate(b.resets)),
      b.exhausted && el("div", { class: "notice" }, "Limit reached. Cards are using the free route sources until it resets."),
      el("div", { class: "muted pad-y" },
        fa ? `FlightAware's own count: ${plural(fa.calls, "lookup")}, $${fa.spent.toFixed(3)} (checked ${fmtAgo(Date.now() / 1000 - fa.at)}). It lags a few hours behind, so the higher of the two counts is used.`
          : "FlightAware's own count hasn't been checked yet.",
        b.sync_error && ` Last check failed: ${b.sync_error}.`));
  }
}

function row(name, ok, detail, tag) {
  const cls = ok === true ? "ok" : ok === false ? "bad" : "off";
  return el("div", { class: "src-row" },
    el("span", { class: `dot ${cls}` }),
    el("div", { class: "src-text" },
      el("div", { class: "src-name" }, name, tag && el("span", { class: "tag" }, tag)),
      el("div", { class: "src-detail" }, detail || "")));
}

const kv = (k, v) => el("div", { class: "trow" }, el("span", {}, k), el("span", {}, v));

function fmtDate(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const plural = (n, word) => `${n.toLocaleString("en-US")} ${word}${n === 1 ? "" : "s"}`;
