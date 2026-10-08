"""The status card shown when an aircraft is tapped: a port of web/src/card.js.

Two lookups start together on tap, both through the flight map service: the free
details (photo, aircraft, airline, crowd-sourced route) and FlightAware AeroAPI
(times and the filed route, cached by the service until the flight lands). The
free route shows first and is replaced by FlightAware's when it arrives.

Lookups run on background threads; the main thread calls `poll()` each frame to
take in results, and `draw()` to paint the card down the right of the screen.
"""

import io
import json
import logging
import queue
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

import pygame

import geo
import ui

log = logging.getLogger("flightmap.native")

WIDTH = 340
HEAD_H = 66
PAD = 16
PHOTO_H = 227        # 3:2 at the card's width
USER_AGENT = "flight-map-native/0.1 (personal kiosk)"


class Card:
    def __init__(self, server: str, height: int):
        self.server = server.rstrip("/")
        self.h = height
        self.results: queue.Queue = queue.Queue()
        self.plane = None
        self.content = None          # rendered body (below the header)
        self.head = None
        self.scroll = 0
        self.buttons = []            # (rect in content coordinates, action)
        self._reset()

    def _reset(self):
        self.details = None
        self.times = None
        self.times_error = None
        self.loading_times = False
        self.budget = None
        self.photo = None
        self.position = None
        self.lost = False
        self.scroll = 0

    @property
    def open(self):
        return self.plane is not None

    @property
    def hex(self):
        return self.plane["hex"] if self.plane else None

    @property
    def route(self):
        """FlightAware's filed route wins over the crowd-sourced one."""
        return (self.times or {}).get("route") or (self.details or {}).get("route")

    @property
    def trail(self):
        return (self.details or {}).get("trail") or []

    # ------------------------------------------------------------ lookups

    def show(self, plane, position):
        self._reset()
        self.plane = plane
        self.position = position
        # Ground vehicles and aircraft with no callsign or registration have nothing to look up.
        lookup = not (plane.get("cat") or "").startswith("C") and bool(plane.get("cs") or plane.get("reg"))
        self.loading_times = lookup
        lat, lon = position
        q = urllib.parse.urlencode({"callsign": plane.get("cs") or "", "lat": lat, "lon": lon})
        self._spawn("details", lambda: self._get_json(f"/api/aircraft/{plane['hex']}?{q}"))
        if lookup:
            self._spawn("times", lambda: self._flight(plane, refresh=False))
        self.render()

    def hide(self):
        self.plane = None

    def refresh_times(self):
        if not self.plane:
            return
        self.loading_times = True
        self.times_error = None
        plane = self.plane
        self._spawn("times", lambda: self._flight(plane, refresh=True))
        self.render()

    def _flight(self, plane, refresh):
        body = json.dumps({"callsign": plane.get("cs") or "", "registration": plane.get("reg") or "",
                           "refresh": refresh}).encode()
        req = urllib.request.Request(f"{self.server}/api/aircraft/{plane['hex']}/flight", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    def _get_json(self, path):
        with urllib.request.urlopen(self.server + path, timeout=30) as r:
            return json.load(r)

    def _photo(self, src):
        req = urllib.request.Request(src, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            img = pygame.image.load(io.BytesIO(r.read()), "photo.jpg")
        # Cover the 3:2 box: scale to fill, crop the overflow.
        w, h = img.get_size()
        s = max(WIDTH / w, PHOTO_H / h)
        img = pygame.transform.smoothscale(img, (max(WIDTH, round(w * s)), max(PHOTO_H, round(h * s))))
        x, y = (img.get_width() - WIDTH) // 2, (img.get_height() - PHOTO_H) // 2
        return img.subsurface((x, y, WIDTH, PHOTO_H)).copy()

    def _spawn(self, kind, fn):
        hex_ = self.hex

        def run():
            try:
                result = fn()
            except Exception as e:
                log.warning("card %s lookup failed: %s", kind, e)
                result = None
            self.results.put((hex_, kind, result))
        threading.Thread(target=run, daemon=True).start()

    def poll(self) -> bool:
        """Take in finished lookups. True if the card changed."""
        changed = False
        while not self.results.empty():
            hex_, kind, result = self.results.get_nowait()
            if hex_ != self.hex:
                continue                       # for a card that's since been closed
            changed = True
            if kind == "details":
                self.details = result or {}
                self.times = self.times or self.details.get("times")
                self.budget = self.details.get("budget")
                src = (self.details.get("photo") or {}).get("src")
                if src:
                    self._spawn("photo", lambda: self._photo(src))
            elif kind == "times":
                self.loading_times = False
                if result is None:
                    self.times_error = {"message": "Flight times unavailable: couldn't reach the "
                                                   "flight map service.", "kind": "error"}
                else:
                    self.budget = result.get("budget") or self.budget
                    if result.get("error"):
                        self.times_error = {"message": result["error"], "kind": result.get("kind")}
                    else:
                        self.times = result.get("times")
            elif kind == "photo" and result is not None:
                self.photo = result.convert()
        if changed:
            self.render()
        return changed

    def update_live(self, plane, position):
        """About once a second, with the latest state (None once the aircraft is gone)."""
        if not self.open:
            return
        self.lost = plane is None
        if plane:
            self.plane = plane
            self.position = position
        self.render()

    # ------------------------------------------------------------ input

    def tap(self, x, y):
        """A tap at card-local (x, y). Returns "close", "refresh" or None."""
        if y < HEAD_H:
            return "close" if x > WIDTH - 60 else None
        cy = y - HEAD_H + self.scroll
        for rect, action in self.buttons:
            if rect.inflate(16, 16).collidepoint(x, cy):
                return action
        return None

    def scroll_by(self, dy):
        if self.content:
            room = max(0, self.content.get_height() - (self.h - HEAD_H))
            self.scroll = max(0, min(room, self.scroll - dy))

    # ------------------------------------------------------------ drawing

    def draw(self, screen):
        if not self.open or self.content is None:
            return
        x = screen.get_width() - WIDTH
        view = pygame.Rect(0, self.scroll, WIDTH, min(self.h - HEAD_H, self.content.get_height() - self.scroll))
        pygame.draw.rect(screen, ui.PANEL, (x, 0, WIDTH, self.h))
        screen.blit(self.content, (x, HEAD_H), view)
        screen.blit(self.head, (x, 0))
        pygame.draw.line(screen, ui.LINE, (x - 1, 0), (x - 1, self.h))

    def render(self):
        if not self.plane:
            return
        p, d = self.plane, self.details
        info = (d or {}).get("aircraft") or {}
        title = p.get("cs") or p.get("reg") or info.get("registration") or p["hex"].upper()
        # The feed's owner is often a leasing bank, so prefer the airline from the callsign.
        subtitle = ((d.get("airline") or info.get("owner") or p.get("op") or p.get("desc") or "")
                    if d is not None else (p.get("desc") or ""))

        head = pygame.Surface((WIDTH, HEAD_H))
        head.fill(ui.PANEL)
        head.blit(ui.text(ui.fit(title, 22, WIDTH - 80, True), 22, ui.TEXT, True), (PAD, 10))
        if subtitle:
            head.blit(ui.text(ui.fit(subtitle, 13, WIDTH - 80), 13, ui.MUTED), (PAD, 40))
        ui.close_icon(head, (WIDTH - 30, 30))
        pygame.draw.line(head, ui.LINE, (0, HEAD_H - 1), (WIDTH, HEAD_H - 1))
        self.head = head

        body = pygame.Surface((WIDTH, 1600))
        body.fill(ui.PANEL)
        self.buttons = []
        y = 0
        if self.photo:
            body.blit(self.photo, (0, 0))
            credit = f"© {(d.get('photo') or {}).get('photographer') or 'unknown'} · planespotters.net"
            body.blit(ui.text(ui.fit(credit, 10, WIDTH - 2 * PAD), 10, ui.MUTED), (PAD, PHOTO_H + 4))
            y = PHOTO_H + 20
        for section in (self._route, self._times, self._live, self._aircraft):
            y = section(body, y)
        if d is None:
            body.blit(ui.text("Loading details…", 12, ui.MUTED), (PAD, y + 12))
            y += 40
        self.content = body.subsurface((0, 0, WIDTH, max(1, min(y + 20, 1600)))).copy()
        self.scroll_by(0)

    def _rule(self, body, y):
        pygame.draw.line(body, ui.LINE, (0, y), (WIDTH, y))
        return y + 1

    def _route(self, body, y):
        route = self.route
        if not route:
            return y
        o, dst = route.get("origin") or {}, route.get("destination") or {}
        y += 12
        code = lambda a: a.get("iata") or a.get("icao") or a.get("code") or "—"
        city = lambda a: ui.fit(a.get("city") or a.get("name") or "", 12, 130)
        left = ui.text(code(o), 26, ui.TEXT, True)
        right = ui.text(code(dst), 26, ui.TEXT, True)
        body.blit(left, (PAD, y))
        body.blit(right, (WIDTH - PAD - right.get_width(), y))
        ui.arrow(body, ui.MUTED, WIDTH / 2 - 12, WIDTH / 2 + 10, y + 18)
        y += 36
        c1, c2 = ui.text(city(o), 12, ui.MUTED), ui.text(city(dst), 12, ui.MUTED)
        body.blit(c1, (PAD, y))
        body.blit(c2, (WIDTH - PAD - c2.get_width(), y))
        y += 18

        if route.get("plausible") is not False:
            pct = labels = None
            if self.position and o.get("lat") is not None and dst.get("lat") is not None:
                lat, lon = self.position
                flown = geo.distance_nm(o["lat"], o["lon"], lat, lon)
                togo = geo.distance_nm(lat, lon, dst["lat"], dst["lon"])
                pct = 100 * flown / (flown + togo or 1)
                labels = (f"{fmt_int(flown)} nm flown", f"{fmt_int(togo)} nm to go")
            elif self.times and self.times.get("route") is route and self.times.get("progress_percent") is not None:
                pct = self.times["progress_percent"]
                labels = (f"{pct}% complete", "")
            if pct is not None:
                y += 8
                pct = max(0, min(100, pct))
                bar = pygame.Rect(PAD, y, WIDTH - 2 * PAD, 4)
                pygame.draw.rect(body, ui.PANEL_2, bar, border_radius=2)
                pygame.draw.rect(body, ui.ACCENT, (bar.x, bar.y, round(bar.w * pct / 100), 4), border_radius=2)
                y += 8
                l, r = ui.text(labels[0], 11, ui.MUTED), ui.text(labels[1], 11, ui.MUTED)
                body.blit(l, (PAD, y))
                body.blit(r, (WIDTH - PAD - r.get_width(), y))
                y += 16
        if route.get("plausible") is False:
            body.blit(ui.text("Route may be out of date for this callsign.", 12, ui.WARN), (PAD, y + 4))
            y += 20
        note = ("Filed route · FlightAware" if route.get("source") == "FlightAware"
                else "Usual route for this flight number · checking FlightAware…" if self.loading_times
                else "Usual route for this flight number")
        body.blit(ui.text(ui.fit(note, 11, WIDTH - 2 * PAD), 11, ui.MUTED), (PAD, y + 6))
        return self._rule(body, y + 30)

    def _times(self, body, y):
        err = self.times_error
        if self.times:
            return self._times_table(body, y)
        if self.loading_times:
            body.blit(ui.text("Getting flight times from FlightAware…", 12, ui.MUTED), (PAD, y + 14))
            return self._rule(body, y + 42)
        if not err or err.get("kind") == "disabled":
            return y
        if err.get("kind") == "no_flight":
            body.blit(ui.text("No FlightAware flight plan found for this aircraft.", 12, ui.MUTED), (PAD, y + 14))
            return self._rule(body, y + 42)
        y = self._notice(body, y + 12, err["message"])
        if err.get("kind") != "limit":
            y = self._link(body, PAD, y + 4, "Try again", "refresh")
        return self._rule(body, y + 12)

    def _times_table(self, body, y):
        t = self.times
        otz = (t.get("origin") or {}).get("timezone")
        dtz = (t.get("destination") or {}).get("timezone")
        y += 12
        body.blit(ui.text(t.get("status") or "—", 14, ui.TEXT, True), (PAD, y))
        if t.get("diverted"):
            w = ui.text("Diverted", 12, ui.WARN)
            body.blit(w, (WIDTH - PAD - w.get_width(), y + 2))
        y += 26

        def delay(s):
            return f" ({'+' if s > 0 else '−'}{round(abs(s) / 60)}m)" if s and abs(s) >= 300 else ""

        def gate(term, g):
            return " · ".join(x for x in (term and f"T{term}", g and f"Gate {g}") if x)

        dep = t.get("actual_out") or t.get("estimated_out")
        arr = t.get("actual_in") or t.get("estimated_in")
        cols = [
            ("DEPARTURE", [("Scheduled", fmt_time(t.get("scheduled_out"), otz)),
                           ("Left gate" if t.get("actual_out") else "Expected",
                            fmt_time(dep, otz) and fmt_time(dep, otz) + delay(t.get("departure_delay"))),
                           ("Takeoff", fmt_time(t.get("actual_off"), otz))],
             gate(t.get("terminal_origin"), t.get("gate_origin"))),
            ("ARRIVAL", [("Scheduled", fmt_time(t.get("scheduled_in"), dtz)),
                         ("Arrived" if t.get("actual_in") else "Expected",
                          fmt_time(arr, dtz) and fmt_time(arr, dtz) + delay(t.get("arrival_delay"))),
                         ("Landing", fmt_time(t.get("actual_on"), dtz))],
             gate(t.get("terminal_destination"), t.get("gate_destination"))),
        ]
        col_w = (WIDTH - 2 * PAD - 14) // 2
        bottom = y
        for i, (head, rows, gates) in enumerate(cols):
            x, cy = PAD + i * (col_w + 14), y
            body.blit(ui.text(head, 11, ui.MUTED), (x, cy))
            cy += 18
            for label, value in rows:
                if value:
                    body.blit(ui.text(label, 11, ui.MUTED), (x, cy))
                    body.blit(ui.text(ui.fit(value, 14, col_w), 14), (x, cy + 14))
                    cy += 36
            if gates:
                body.blit(ui.text(ui.fit(gates, 12, col_w), 12, ui.MUTED), (x, cy))
                cy += 20
            bottom = max(bottom, cy)
        y = bottom + 6
        ago = ui.text(f"Updated {fmt_ago(time.time() - (t.get('fetched_at') or time.time()))}", 12, ui.MUTED)
        body.blit(ago, (PAD, y + 4))
        if not (self.budget or {}).get("exhausted"):
            label = "Updating…" if self.loading_times else "Refresh"
            w = ui.text(label, 14, ui.ACCENT, True).get_width()
            self._link(body, WIDTH - PAD - w, y, label, None if self.loading_times else "refresh")
        y += 26
        if self.times_error:
            y = self._notice(body, y + 4, self.times_error["message"])
        return self._rule(body, y + 10)

    def _live(self, body, y):
        p = self.plane
        vr = p.get("vr")
        vr_text = ("—" if vr is None or p.get("gnd") else
                   f"{'+' if vr > 0 else '−' if vr < 0 else ''}{fmt_int(abs(vr))} ft/min")
        cells = [("ALTITUDE", fmt_alt(p)), ("VERTICAL", vr_text),
                 ("SPEED", "—" if p.get("gs") is None else f"{fmt_int(p['gs'])} kt"),
                 ("TRACK", "—" if p.get("trk") is None else f"{fmt_int(p['trk'])}°"),
                 ("SQUAWK", p.get("sq") or "—"), ("MODE S", p["hex"].upper())]
        y += 12
        if self.lost:
            y = self._notice(body, y, "Signal lost. Showing last known data.") + 10
        col_w = (WIDTH - 2 * PAD) // 3
        value_color = ui.MUTED if self.lost else ui.TEXT
        for i, (label, value) in enumerate(cells):
            x, cy = PAD + (i % 3) * col_w, y + (i // 3) * 46
            body.blit(ui.text(label, 11, ui.MUTED), (x, cy))
            body.blit(ui.text(ui.fit(value, 15, col_w - 6, True), 15, value_color, True), (x, cy + 15))
        return self._rule(body, y + 92)

    def _aircraft(self, body, y):
        p, d = self.plane, self.details or {}
        info = d.get("aircraft") or {}
        operator = d.get("airline") or info.get("owner") or p.get("op")
        rows = [
            ("Aircraft", p.get("desc") or info.get("model") or p.get("type")),
            ("Type code", p.get("type") or info.get("type")),
            ("Registration", p.get("reg") or info.get("registration")),
            ("Operator", operator),
            ("Owner", p.get("op") if p.get("op") and not same_company(p.get("op"), operator) else None),
            ("Country", info.get("country")),
        ]
        rows = [(k, v) for k, v in rows if v]
        if not rows:
            return y
        y += 10
        for k, v in rows:
            body.blit(ui.text(k, 14, ui.MUTED), (PAD, y))
            val = ui.text(ui.fit(str(v), 14, WIDTH - 2 * PAD - 110), 14)
            body.blit(val, (WIDTH - PAD - val.get_width(), y))
            y += 24
        return y + 8

    def _notice(self, body, y, message):
        lines = ui.wrap(message, 13, WIDTH - 2 * PAD - 24)
        box = pygame.Rect(PAD, y, WIDTH - 2 * PAD, 20 + 18 * len(lines))
        pygame.draw.rect(body, ui.PANEL_2, box, border_radius=8)
        pygame.draw.rect(body, ui.LINE, box, 1, border_radius=8)
        for i, line in enumerate(lines):
            body.blit(ui.text(line, 13), (box.x + 12, box.y + 10 + 18 * i))
        return box.bottom

    def _link(self, body, x, y, label, action):
        surf = ui.text(label, 14, ui.ACCENT, True)
        body.blit(surf, (x, y))
        if action:
            self.buttons.append((pygame.Rect(x, y, surf.get_width(), surf.get_height()), action))
        return y + surf.get_height()


# ---------------------------------------------------------------- formatting

def fmt_int(n):
    return "—" if n is None else f"{round(n):,}"


def fmt_alt(p):
    if p.get("gnd"):
        return "Ground"
    return "—" if p.get("alt") is None else f"{fmt_int(p['alt'])} ft"


def fmt_time(iso, tz):
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        t = t.astimezone(ZoneInfo(tz)) if tz else t.astimezone()
    except (ValueError, KeyError):
        return None
    return t.strftime("%H:%M %Z")


def fmt_ago(seconds):
    if seconds < 60:
        return f"{max(0, round(seconds))}s ago"
    if seconds < 3600:
        return f"{round(seconds / 60)}m ago"
    return f"{round(seconds / 3600)}h ago"


def same_company(a, b):
    """"SOUTHWEST AIRLINES CO" and "Southwest Airlines" are the same company."""
    if not a or not b:
        return False
    return a.lower().split()[0] == b.lower().split()[0]
