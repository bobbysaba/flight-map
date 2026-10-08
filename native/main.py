"""Flight map drawn straight to the screen with pygame: no browser, no compositor.

A lightweight alternative to the Chromium display (web/) for small boards like the
Pi Zero 2 W. It talks to the same flight map service over the same WebSocket.

    python3 native/main.py                      # full screen (on the Pi: SDL_VIDEODRIVER=kmsdrm)
    python3 native/main.py --windowed 1024x600  # in a window, for development
"""

import argparse
import logging
import math
import os
import sys
import time

if os.environ.get("SDL_VIDEODRIVER") == "kmsdrm":
    # SDL otherwise picks its desktop OpenGL renderer, whose calls all fail on the Pi's
    # OpenGL ES-only GPU: the screen silently keeps showing the console.
    os.environ.setdefault("SDL_RENDER_DRIVER", "opengles2")
    os.environ.setdefault("SDL_FRAMEBUFFER_ACCELERATION", "opengles2")

import pygame

import card as card_mod
import geo
import icons
import ui
from card import Card
from fleet import CORRECTION_S, Fleet
from link import Link
from status import StatusPanel
from tiles import Tiles

log = logging.getLogger("flightmap.native")

FPS = 20                 # while the map is moving; planes alone redraw only as often as they move
MAX_SPEED_KT = 650       # fastest likely aircraft, for how often planes need redrawing
MOVE_PX = 0.5            # ...redraw once the fastest could have moved this far on screen
MAX_DRIFT_NM = 12        # how far projection + correction can move a plane from its report
MIN_ZOOM = 3
MAX_RADIUS_NM = 250
SETTLE_S = 0.3           # after the view stops moving, check whether to offer "Search here"
TAP_PX = 12
HIT_PX = 16              # generous tap target around each plane
STATS_EVERY_S = 10

BG = ui.BG
HALO = (6, 8, 11)
GROUND = (139, 149, 163)
ALERT = (255, 59, 78)
ALT_STOPS = [(0, "#ff7b3a"), (2000, "#ffa733"), (6000, "#ffd23f"), (12000, "#c7e04a"),
             (20000, "#5fd068"), (28000, "#38c9d6"), (36000, "#5b8cff"), (44000, "#b071ff")]



def _rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _alt_lut(step=500):
    stops = [(a, _rgb(c)) for a, c in ALT_STOPS]
    lut = []
    for alt in range(0, stops[-1][0] + step, step):
        for (a0, c0), (a1, c1) in zip(stops, stops[1:]):
            if alt <= a1:
                f = (alt - a0) / (a1 - a0)
                lut.append(tuple(round(c0[i] + (c1[i] - c0[i]) * f) for i in range(3)))
                break
        else:
            lut.append(stops[-1][1])
    return lut


ALT_LUT = _alt_lut()


def plane_color(p):
    if p.get("emerg") or p.get("sq") in ("7500", "7600", "7700"):
        return ALERT
    if p.get("gnd"):
        return GROUND
    alt = p.get("alt") or 0
    return ALT_LUT[max(0, min(len(ALT_LUT) - 1, int(alt) // 500))]


def rss_mb():
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * 4096 / 2**20
    except OSError:  # macOS, during development
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20


class View:
    """Where the map is looking: centre in world units (0..1) and a fractional zoom."""

    max_zoom = 16   # set from the tile source

    def __init__(self, w, h, lat, lon, zoom):
        self.w, self.h = w, h
        self.cx, self.cy = geo.to_world(lat, lon)
        self.z = zoom

    @property
    def scale(self):
        return geo.TILE * 2 ** self.z

    def key(self):
        return (round(self.cx, 9), round(self.cy, 9), round(self.z, 4))

    def to_screen(self, wx, wy):
        dx = (wx - self.cx + 0.5) % 1 - 0.5   # nearest copy of the world, across the antimeridian
        s = self.scale
        return self.w / 2 + dx * s, self.h / 2 + (wy - self.cy) * s

    def to_world(self, sx, sy):
        s = self.scale
        return self.cx + (sx - self.w / 2) / s, self.cy + (sy - self.h / 2) / s

    def pan(self, dx, dy):
        s = self.scale
        self.cx -= dx / s
        self.cy -= dy / s
        self.clamp()

    def zoom_about(self, z, sx, sy, anchor=None):
        """Zoom to `z` keeping world point `anchor` (default: what's under sx, sy) at sx, sy."""
        ax, ay = anchor or self.to_world(sx, sy)
        self.z = max(MIN_ZOOM, min(self.max_zoom, z))
        s = self.scale
        self.cx = ax - (sx - self.w / 2) / s
        self.cy = ay - (sy - self.h / 2) / s
        self.clamp()

    def clamp(self):
        self.cx %= 1.0
        half = self.h / 2 / self.scale
        self.cy = min(max(self.cy, half), 1 - half) if half < 0.5 else 0.5

    def latlon(self, sx=None, sy=None):
        wx, wy = self.to_world(self.w / 2 if sx is None else sx, self.h / 2 if sy is None else sy)
        return geo.from_world(wx % 1.0, min(max(wy, 0.0), 1.0))


class App:
    def __init__(self, args):
        self.link = Link(args.server)
        self.cfg = self.link.config()

        pygame.display.init()   # not pygame.init(): that also starts audio, which we don't use
        pygame.font.init()
        pygame.display.set_caption("Flight map")
        if args.windowed:
            w, h = (int(v) for v in args.windowed.lower().split("x"))
            self.screen = pygame.display.set_mode((w, h))
        else:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        w, h = self.screen.get_size()
        log.info("screen %dx%d, driver %s", w, h, pygame.display.get_driver())

        self.tiles = Tiles("flight-map-native/0.1 (personal kiosk)", self.cfg.get("carto_key", ""))
        View.max_zoom = self.tiles.max_zoom
        start = self.cfg["start"]
        self.view = View(w, h, start["lat"], start["lon"], round(start["zoom"]))
        self.fleet = Fleet()
        self.status = {"ok": True}
        self.clock = pygame.time.Clock()
        self.map = pygame.Surface((w, h)).convert()
        self.map_key = None
        self.scaled: dict = {}       # tiles resized for the current fractional zoom
        self.scaled_size = None
        self.icons = icons.Icons(HALO)
        self.icons_selected = icons.Icons((255, 255, 255), halo_px=2.2)
        self.card = Card(args.server, h)
        self.status_panel = StatusPanel(args.server, (w, h))
        self.pill_rect = pygame.Rect(0, 0, 0, 0)
        self.selected = None         # hex of the tapped aircraft
        self.trail = []              # its track: (t, world x, world y, alt)
        self.trail_loaded = False    # merged in the service's history yet?
        self.visible = []            # (x, y, plane) drawn last frame, for tap hit-testing
        self.dirty = True            # something besides the map or planes changed
        self.tick_at = 0.0
        self.drag_card = False
        self.search_shown = False
        self.area_shown = False
        self.area_ring = []
        self.drawn_at = 0.0
        self.snapshot_at = 0.0

        self.pointers: dict = {}     # finger id -> (x, y)
        self.pinch = None            # (start distance, start zoom, world anchor)
        self.tap = None              # (x, y) of a press that hasn't moved yet
        self.moved_at = time.monotonic()
        self.area = None

        bw = 44
        self.btn_in = pygame.Rect(12, h - 2 * bw - 20, bw, bw)
        self.btn_out = pygame.Rect(12, h - bw - 12, bw, bw)
        self.btn_home = pygame.Rect(12, h - 3 * bw - 40, bw, bw)
        self.btn_search = pygame.Rect(0, 12, 0, 0)
        self.attribution = ui.text(self.tiles.attribution, 10, ui.MUTED)

        self.reset_stats()
        self.running = True
        self.link.start()
        self.set_area(self.view_area())

    # ------------------------------------------------------------ loop

    def run(self):
        while self.running:
            self.step()
            self.clock.tick(FPS)

    def step(self):
        started = time.perf_counter()
        for e in pygame.event.get():
            self.handle(e)
        self.drain_inbox()
        self.maybe_offer_search()
        if self.card.poll() | self.status_panel.poll():
            self.dirty = True
        now = time.monotonic()
        if now - self.tick_at >= 1:
            self.tick_at = now
            if self.selected:
                self.update_selected()

        arrived = self.tiles.collect()
        moved = self.view.key() != self.map_key
        if arrived or moved:
            self.render_map()
        # Planes move slowly on screen except when zoomed far in, so between gestures
        # only redraw once one could have shifted by MOVE_PX.
        if not (arrived or moved or self.dirty or now - self.drawn_at >= self.plane_interval(now)):
            return
        self.drawn_at = now
        self.dirty = False
        self.screen.blit(self.map, (0, 0))
        if self.area_shown:
            ui.dashed(self.screen, (73, 85, 102), self.ring_points(), 1, 3, 5)
        if self.selected:
            self.draw_selection()
        drawn = self.draw_planes()
        self.draw_ui()
        self.card.draw(self.screen)
        self.status_panel.draw(self.screen)
        pygame.display.flip()
        self.record(time.perf_counter() - started, drawn)

    def plane_interval(self, now):
        nm_per_s = MAX_SPEED_KT / 3600
        if now - self.snapshot_at < CORRECTION_S:   # corrections easing in move planes too
            nm_per_s += self.fleet.max_correction_nm / CORRECTION_S
        return max(1 / FPS, min(1.0, MOVE_PX / (nm_per_s * self.px_per_nm())))

    def px_per_nm(self):
        lat, _ = self.view.latlon()
        return self.view.scale / (21600 * max(0.05, math.cos(math.radians(lat))))

    def reset_stats(self):
        self.stats = {"since": time.monotonic(), "cpu": time.process_time(),
                      "frames": 0, "work": 0.0, "worst": 0.0}

    def record(self, work, drawn):
        s = self.stats
        s["frames"] += 1
        s["work"] += work
        s["worst"] = max(s["worst"], work)
        elapsed = time.monotonic() - s["since"]
        if elapsed >= STATS_EVERY_S:
            log.info("stats: %.1f frames/s drawn, frame %.1f ms avg / %.1f ms worst, CPU %.0f%%, "
                     "%d planes drawn of %d, %d tiles in memory, %.0f MB RSS",
                     s["frames"] / elapsed, 1000 * s["work"] / s["frames"], 1000 * s["worst"],
                     100 * (time.process_time() - s["cpu"]) / elapsed,
                     drawn, len(self.fleet.planes), len(self.tiles.mem), rss_mb())
            self.reset_stats()

    def drain_inbox(self):
        while not self.link.inbox.empty():
            msg = self.link.inbox.get_nowait()
            if msg.get("type") == "snapshot":
                self.fleet.update(msg)
                self.status = {"ok": True}
                self.snapshot_at = time.monotonic()
            elif msg.get("type") == "status":
                self.status = msg

    # ------------------------------------------------------------ map

    def render_map(self):
        v = self.view
        self.map_key = v.key()
        self.map.fill(BG)
        zi = max(MIN_ZOOM, min(v.max_zoom, round(v.z)))
        n = 2 ** zi
        size = geo.TILE * 2 ** (v.z - zi)          # a tile's size on screen
        exact = abs(size - geo.TILE) < 0.01
        px = math.ceil(size) + (0 if exact else 1)  # +1 hides seams between scaled tiles
        if px != self.scaled_size or len(self.scaled) > 400:
            self.scaled, self.scaled_size = {}, px

        x0, y0 = v.to_world(0, 0)
        x1, y1 = v.to_world(v.w, v.h)
        keys = []
        for j in range(max(0, math.floor(y0 * n)), min(n, math.floor(y1 * n) + 1)):
            for i in range(math.floor(x0 * n), math.floor(x1 * n) + 1):
                key = (zi, i % n, j)
                keys.append(key)
                sx = v.w / 2 + (i / n - v.cx) * v.scale
                sy = v.h / 2 + (j / n - v.cy) * v.scale
                surf = self.tile_surface(key, px, exact)
                if surf is not None:
                    self.map.blit(surf, (round(sx), round(sy)))
        self.tiles.want(keys)

    def tile_surface(self, key, px, exact):
        surf = self.tiles.get(key)
        if surf is not None:
            if exact:
                return surf
            cached = self.scaled.get(key)
            if cached is None:
                cached = self.scaled[key] = pygame.transform.scale(surf, (px, px))
            return cached
        # Not loaded yet: stretch the matching part of a lower-zoom tile that is.
        z, x, y = key
        for dz in range(1, 4):
            parent = self.tiles.peek((z - dz, x >> dz, y >> dz))
            if parent is None:
                continue
            sub = geo.TILE >> dz
            fb_key = (key, "fallback", dz)
            cached = self.scaled.get(fb_key)
            if cached is None:
                rect = pygame.Rect((x & ((1 << dz) - 1)) * sub, (y & ((1 << dz) - 1)) * sub, sub, sub)
                cached = self.scaled[fb_key] = pygame.transform.scale(parent.subsurface(rect), (px, px))
            return cached
        return None

    # ------------------------------------------------------------ planes

    def draw_planes(self):
        v, screen = self.view, self.screen
        now = self.fleet.now()
        # Icons keep their size during a pinch (redrawing them all every frame would
        # stutter) and take the new zoom's size once it settles.
        icon_z = self.pinch[1] if self.pinch else v.z
        px_per_unit = 0.5 * icons.icon_size(icon_z)   # the browser draws them at pixelRatio 2
        show_labels = v.z >= 8.5
        # Where a plane is drawn is never far from where it was reported, so skip the
        # projection maths for those well off screen.
        margin = 40 + MAX_DRIFT_NM * self.px_per_nm()
        visible = []
        for p in self.fleet.planes.values():
            rx, ry = v.to_screen(*p["world"])
            if not (-margin < rx < v.w + margin and -margin < ry < v.h + margin):
                continue
            lat, lon = self.fleet.position(p, now)
            sx, sy = v.to_screen(*geo.to_world(lat, lon))
            if -40 < sx < v.w + 40 and -40 < sy < v.h + 40:
                visible.append((p.get("alt") or 0, sx, sy, p))
        visible.sort(key=lambda t: t[0])   # higher aircraft on top
        chosen = next((t for t in visible if t[3]["hex"] == self.selected), None)
        if chosen:   # the selected aircraft goes on top of everything
            visible.remove(chosen)
            visible.append(chosen)
        self.visible = [(sx, sy, p) for _, sx, sy, p in visible]

        blit = screen.blit
        for _, sx, sy, p in visible:
            color = p.get("color")
            if color is None:   # fixed until the next snapshot replaces the plane
                color = p["color"] = plane_color(p)
            shape = p.get("shape")
            if shape is None:
                shape = p["shape"] = icons.shape_for(p.get("type"), p.get("cat"))
            source = self.icons_selected if p["hex"] == self.selected else self.icons
            sprite = source.get(shape, px_per_unit * icons.SHAPE_SCALE[shape], color, p.get("trk"))
            w, h = sprite.get_size()
            blit(sprite, (sx - w / 2, sy - h / 2))
            if show_labels:
                text = p.get("cs") or p.get("reg") or ""
                if text:
                    label = self.label(text)
                    screen.blit(label, (sx - label.get_width() / 2, sy + 11))
        return len(visible)

    def label(self, text):
        return ui.text(text, 10, ui.MUTED)

    # ------------------------------------------------------------ selected aircraft

    def select(self, hex_):
        self.selected = hex_
        self.trail = []
        self.trail_loaded = False
        self.dirty = True
        plane = self.fleet.planes.get(hex_) if hex_ else None
        if not plane:
            self.selected = None
            self.card.hide()
            return
        self.card.show(plane, self.fleet.position(plane))

    def update_selected(self):
        """Once a second: refresh the card's live values and extend the trail."""
        plane = self.fleet.planes.get(self.selected)
        if not plane:
            self.card.update_live(None, None)
            self.dirty = True
            return
        lat, lon = self.fleet.position(plane)
        self.card.update_live(plane, (lat, lon))
        if not self.trail_loaded and self.card.details is not None:
            # The service's history arrives with the card details; keep any points seen since.
            history = [(t, *geo.to_world(la, lo), alt) for t, la, lo, alt in self.card.trail]
            last = history[-1][0] if history else -math.inf
            self.trail = history + [pt for pt in self.trail if pt[0] > last]
            self.trail_loaded = True
        if not self.trail or self.trail[-1][0] < plane["t"]:
            self.trail.append((plane["t"], *plane["world"], plane.get("alt")))
        self.dirty = True

    def draw_selection(self):
        """The selected aircraft's trail (coloured by altitude) and the route still to fly."""
        plane = self.fleet.planes.get(self.selected)
        if not plane:
            return
        v, screen = self.view, self.screen
        lat, lon = self.fleet.position(plane)
        here = v.to_screen(*geo.to_world(lat, lon))
        pts = [(v.to_screen(wx, wy), alt) for _, wx, wy, alt in self.trail] + [(here, plane.get("alt"))]
        for (a, _), (b, alt) in zip(pts, pts[1:]):
            color = ALT_LUT[max(0, min(len(ALT_LUT) - 1, int(alt or 0) // 500))]
            pygame.draw.line(screen, color, a, b, 3)

        route = self.card.route
        dst = (route or {}).get("destination") or {}
        if dst.get("lat") is not None and route.get("plausible") is not False and not plane.get("gnd"):
            line = [v.to_screen(*geo.to_world(la, lo)) for la, lo in
                    geo.great_circle(lat, lon, dst["lat"], dst["lon"])]
            ui.dashed(screen, (150, 158, 170), line, 2, 3, 5)

    # ------------------------------------------------------------ status and buttons

    def draw_ui(self):
        screen = self.screen
        state, text = self.status_text()
        surf = ui.text(text, 13)
        box = self.pill_rect = pygame.Rect(12, 12, surf.get_width() + 38, 30)
        pygame.draw.rect(screen, ui.PANEL, box, border_radius=15)
        dot = {"ok": ui.OK, "stale": ui.WARN, "error": ui.BAD}[state]
        pygame.draw.circle(screen, dot, (box.x + 15, box.centery), 4)
        screen.blit(surf, (box.x + 26, box.centery - surf.get_height() // 2))

        for rect, kind in ((self.btn_in, "+"), (self.btn_out, "-"), (self.btn_home, "home")):
            pygame.draw.rect(screen, ui.PANEL, rect, border_radius=22 if kind == "home" else 8)
            pygame.draw.rect(screen, ui.LINE, rect, 1, border_radius=22 if kind == "home" else 8)
            cx, cy = rect.center
            if kind == "home":
                pygame.draw.polygon(screen, ui.TEXT, [(cx, cy - 11), (cx + 11, cy), (cx + 7, cy),
                                                      (cx + 7, cy + 10), (cx - 7, cy + 10),
                                                      (cx - 7, cy), (cx - 11, cy)])
            else:
                pygame.draw.line(screen, ui.TEXT, (cx - 9, cy), (cx + 9, cy), 3)
                if kind == "+":
                    pygame.draw.line(screen, ui.TEXT, (cx, cy - 9), (cx, cy + 9), 3)

        map_w = self.view.w - (card_mod.WIDTH if self.card.open else 0)
        if self.search_shown:
            label = ui.text("Search here", 14, (255, 255, 255), True)
            self.btn_search = pygame.Rect(0, 12, label.get_width() + 44, 40)
            self.btn_search.centerx = map_w // 2
            pygame.draw.rect(screen, ui.ACCENT, self.btn_search, border_radius=20)
            screen.blit(label, label.get_rect(center=self.btn_search.center))

        a = self.attribution
        screen.blit(a, (map_w - a.get_width() - 6, self.view.h - a.get_height() - 4))

    def status_text(self):
        """("ok" | "stale" | "error", text) for the pill, as in the browser."""
        last = self.fleet.last_snapshot
        age = time.time() - last if last else math.inf
        stale = age > self.cfg["poll_interval_s"] * 3 + 2
        if not self.status.get("ok") and (stale or not last):
            return "error", (f"No data · last update {fmt_ago(age)}" if last else "No data · retrying")
        if not last:
            return "stale", "Connecting…"
        text = f"{self.fleet.source} · {len(self.fleet.planes)} aircraft"
        return ("stale", f"{text} · {fmt_ago(age)}") if stale else ("ok", text)

    # ------------------------------------------------------------ live area

    def view_area(self):
        v = self.view
        lat, lon = v.latlon()
        r = max(geo.distance_nm(lat, lon, *v.latlon(x, y))
                for x, y in ((0, 0), (v.w, 0), (0, v.h), (v.w, v.h)))
        return lat, geo.wrap_lon(lon), min(MAX_RADIUS_NM, math.ceil(r))

    def set_area(self, area):
        self.area = area
        self.link.set_area(*area)
        lat, lon, r = area
        self.area_ring = [geo.to_world(*geo.destination(lat, lon, i * 360 / 128, r)) for i in range(129)]
        self.search_shown = self.area_shown = False
        self.dirty = True

    def maybe_offer_search(self):
        """Once the view settles, offer "Search here" if it has left the live area
        (the old area stays live until it's tapped), as the browser does."""
        if self.pointers or time.monotonic() - self.moved_at < SETTLE_S or self.moved_at == 0:
            return
        self.moved_at = 0
        lat, lon, r = self.area
        v = self.view
        want = self.view_area()
        covered = all(geo.distance_nm(lat, lon, *v.latlon(x, y)) <= r
                      for x, y in ((0, 0), (v.w, 0), (0, v.h), (v.w, v.h)))
        moved = geo.distance_nm(lat, lon, want[0], want[1]) > 0.05 * r
        grew = want[2] > r * 1.05
        self.search_shown = not covered and (moved or grew)
        self.area_shown = not covered
        self.dirty = True

    def ring_points(self):
        """The live area's outline on screen, kept continuous across the antimeridian."""
        pts = [self.view.to_screen(wx, wy) for wx, wy in self.area_ring]
        s = self.view.scale
        for i in range(1, len(pts)):
            x, y = pts[i]
            while x - pts[i - 1][0] > s / 2:
                x -= s
            while x - pts[i - 1][0] < -s / 2:
                x += s
            pts[i] = (x, y)
        return pts

    # ------------------------------------------------------------ input

    def handle(self, e):
        v = self.view
        if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key in (pygame.K_ESCAPE, pygame.K_q)):
            self.running = False
        elif e.type == pygame.FINGERDOWN:
            self.pointer_down(e.finger_id, e.x * v.w, e.y * v.h)
        elif e.type == pygame.FINGERMOTION:
            self.pointer_move(e.finger_id, e.x * v.w, e.y * v.h)
        elif e.type == pygame.FINGERUP:
            self.pointer_up(e.finger_id, e.x * v.w, e.y * v.h)
        elif getattr(e, "touch", False):
            return  # SDL's mouse events copied from touches; handled as fingers above
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self.pointer_down("mouse", *e.pos)
        elif e.type == pygame.MOUSEMOTION and "mouse" in self.pointers:
            self.pointer_move("mouse", *e.pos)
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1 and "mouse" in self.pointers:
            self.pointer_up("mouse", *e.pos)
        elif e.type == pygame.MOUSEWHEEL and e.y and not self.status_panel.is_open:
            self.zoom_step(1 if e.y > 0 else -1, *pygame.mouse.get_pos())

    def pointer_down(self, pid, x, y):
        self.pointers[pid] = (x, y)
        if len(self.pointers) == 1:
            self.tap = (x, y)
            self.drag_card = self.card.open and x >= self.view.w - card_mod.WIDTH
        elif len(self.pointers) == 2:
            self.tap = None
            self.drag_card = False
            (ax, ay), (bx, by) = list(self.pointers.values())[:2]
            mid = ((ax + bx) / 2, (ay + by) / 2)
            self.pinch = (max(1.0, math.hypot(ax - bx, ay - by)), self.view.z, self.view.to_world(*mid))

    def pointer_move(self, pid, x, y):
        if pid not in self.pointers:
            return
        px, py = self.pointers[pid]
        self.pointers[pid] = (x, y)
        if self.tap and math.hypot(x - self.tap[0], y - self.tap[1]) > TAP_PX:
            px, py = self.tap   # it's a drag after all: pan from where it started
            self.tap = None
        if self.status_panel.is_open:
            return              # the panel covers the map: no panning or zooming under it
        if self.pinch and len(self.pointers) >= 2:
            (ax, ay), (bx, by) = list(self.pointers.values())[:2]
            d0, z0, anchor = self.pinch
            z = z0 + math.log2(max(1.0, math.hypot(ax - bx, ay - by)) / d0)
            self.view.zoom_about(z, (ax + bx) / 2, (ay + by) / 2, anchor)
        elif self.drag_card:
            if not self.tap:
                self.card.scroll_by(y - py)
                self.dirty = True
            return
        elif not self.tap:
            self.view.pan(x - px, y - py)
        self.moved_at = time.monotonic()

    def pointer_up(self, pid, x, y):
        self.pointers.pop(pid, None)
        if self.pinch and len(self.pointers) < 2:
            self.pinch = None
            if not self.status_panel.is_open:
                # Settle on a whole zoom level so tiles are drawn crisp, at their real size.
                self.view.zoom_about(round(self.view.z), x, y)
                self.moved_at = time.monotonic()
        if not self.pointers and self.tap:
            self.on_tap(*self.tap)
        if not self.pointers:
            self.tap = None

    def on_tap(self, x, y):
        v = self.view
        self.dirty = True
        if self.status_panel.is_open:
            self.status_panel.tap(x, y)
            return
        if self.pill_rect.collidepoint(x, y):
            self.status_panel.open()
            return
        if self.card.open and x >= v.w - card_mod.WIDTH:
            action = self.card.tap(x - (v.w - card_mod.WIDTH), y)
            if action == "close":
                self.select(None)
            elif action == "refresh":
                self.card.refresh_times()
            return
        if self.search_shown and self.btn_search.collidepoint(x, y):
            self.set_area(self.view_area())
        elif self.btn_in.collidepoint(x, y):
            self.zoom_step(1, v.w / 2, v.h / 2)
        elif self.btn_out.collidepoint(x, y):
            self.zoom_step(-1, v.w / 2, v.h / 2)
        elif self.btn_home.collidepoint(x, y):
            start = self.cfg["start"]
            self.view = View(v.w, v.h, start["lat"], start["lon"], round(start["zoom"]))
            self.moved_at = time.monotonic()
        else:
            near = [(math.hypot(px - x, py - y), p["hex"]) for px, py, p in self.visible
                    if abs(px - x) <= HIT_PX and abs(py - y) <= HIT_PX]
            if near:
                self.select(min(near)[1])
            elif self.selected:
                self.select(None)

    def zoom_step(self, dz, x, y):
        self.view.zoom_about(round(self.view.z) + dz, x, y)
        self.moved_at = time.monotonic()


def fmt_ago(seconds):
    if seconds < 60:
        return f"{max(0, round(seconds))}s ago"
    if seconds < 3600:
        return f"{round(seconds / 60)}m ago"
    return f"{round(seconds / 3600)}h ago"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://127.0.0.1:8080", help="flight map service URL")
    ap.add_argument("--windowed", metavar="WxH", help="run in a window instead of full screen")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        stream=sys.stdout)
    App(args).run()


if __name__ == "__main__":
    main()
