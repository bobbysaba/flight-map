"""Flight map drawn straight to the screen with pygame: no browser, no compositor.

A lightweight alternative to the Chromium display (web/) for small boards like the
Pi Zero 2 W. It talks to the same flight map service over the same WebSocket.

    python3 native/main.py                      # full screen (on the Pi: SDL_VIDEODRIVER=kmsdrm)
    python3 native/main.py --windowed 1024x600  # in a window, for development
"""

import argparse
import logging
import math
import sys
import time

import pygame

import geo
from fleet import Fleet
from link import Link
from tiles import ATTRIBUTION, MAX_ZOOM, Tiles

log = logging.getLogger("flightmap.native")

FPS = 20                 # same as the browser version: plenty for planes, easy on the CPU
MIN_ZOOM = 3
MAX_RADIUS_NM = 250
SETTLE_S = 0.8           # after the view stops moving, watch the new area if needed
TAP_PX = 12
STATS_EVERY_S = 10

BG = (14, 18, 24)
HALO = (6, 8, 11)
TEXT = (199, 211, 223)
MUTED = (140, 150, 163)
PANEL = (22, 27, 35)
GROUND = (139, 149, 163)
ALERT = (255, 59, 78)
ALT_STOPS = [(0, "#ff7b3a"), (2000, "#ffa733"), (6000, "#ffd23f"), (12000, "#c7e04a"),
             (20000, "#5fd068"), (28000, "#38c9d6"), (36000, "#5b8cff"), (44000, "#b071ff")]

# Nose-up airliner silhouette, right half from nose to tail (fractions of its length).
_HALF = [(0, -0.5), (0.055, -0.42), (0.06, -0.12), (0.5, 0.1), (0.5, 0.18), (0.06, 0.07),
         (0.05, 0.33), (0.19, 0.44), (0.19, 0.5), (0, 0.46)]
SILHOUETTE = _HALF + [(-x, y) for x, y in reversed(_HALF[1:-1])]
SIZE_BY_CAT = {"A1": 0.75, "B1": 0.7, "B4": 0.7, "A2": 0.85, "A3": 1.0, "A4": 1.05, "A5": 1.2,
               "A7": 0.8}


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
        self.z = max(MIN_ZOOM, min(MAX_ZOOM, z))
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

        pygame.init()
        pygame.display.set_caption("Flight map")
        if args.windowed:
            w, h = (int(v) for v in args.windowed.lower().split("x"))
            self.screen = pygame.display.set_mode((w, h))
        else:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            pygame.mouse.set_visible(False)
        w, h = self.screen.get_size()
        log.info("screen %dx%d, driver %s", w, h, pygame.display.get_driver())

        start = self.cfg["start"]
        self.view = View(w, h, start["lat"], start["lon"], round(start["zoom"]))
        self.tiles = Tiles(user_agent="flight-map-native/0.1 (personal kiosk)")
        self.fleet = Fleet()
        self.status = {"ok": True}
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 22)
        self.small = pygame.font.Font(None, 16)
        self.map = pygame.Surface((w, h)).convert()
        self.map_key = None
        self.scaled: dict = {}       # tiles resized for the current fractional zoom
        self.scaled_size = None
        self.labels: dict = {}
        self.pill = (None, None)     # (text, surface)

        self.pointers: dict = {}     # finger id -> (x, y)
        self.pinch = None            # (start distance, start zoom, world anchor)
        self.tap = None              # (x, y) of a press that hasn't moved yet
        self.moved_at = time.monotonic()
        self.area = None

        bw = 44
        self.btn_in = pygame.Rect(12, h - 2 * bw - 20, bw, bw)
        self.btn_out = pygame.Rect(12, h - bw - 12, bw, bw)
        self.btn_home = pygame.Rect(w - bw - 12, 12, bw, bw)
        self.attribution = self.small.render(ATTRIBUTION, True, MUTED)

        self.stats = {"since": time.monotonic(), "frames": 0, "work": 0.0, "worst": 0.0, "drawn": 0}
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
        self.maybe_set_area()

        arrived = self.tiles.collect()
        if arrived or self.view.key() != self.map_key:
            self.render_map()
        self.screen.blit(self.map, (0, 0))
        drawn = self.draw_planes()
        self.draw_ui()
        pygame.display.flip()
        self.record(time.perf_counter() - started, drawn)

    def record(self, work, drawn):
        s = self.stats
        s["frames"] += 1
        s["work"] += work
        s["worst"] = max(s["worst"], work)
        s["drawn"] = drawn
        elapsed = time.monotonic() - s["since"]
        if elapsed >= STATS_EVERY_S:
            log.info("stats: %.1f fps, frame %.1f ms avg / %.1f ms worst, %d planes drawn of %d, "
                     "%d tiles in memory, %.0f MB RSS",
                     s["frames"] / elapsed, 1000 * s["work"] / s["frames"], 1000 * s["worst"],
                     drawn, len(self.fleet.planes), len(self.tiles.mem), rss_mb())
            self.stats = {"since": time.monotonic(), "frames": 0, "work": 0.0, "worst": 0.0,
                          "drawn": drawn}

    def drain_inbox(self):
        while not self.link.inbox.empty():
            msg = self.link.inbox.get_nowait()
            if msg.get("type") == "snapshot":
                self.fleet.update(msg)
                self.status = {"ok": True}
            elif msg.get("type") == "status":
                self.status = msg

    # ------------------------------------------------------------ map

    def render_map(self):
        v = self.view
        self.map_key = v.key()
        self.map.fill(BG)
        zi = max(MIN_ZOOM, min(MAX_ZOOM, round(v.z)))
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
        length = max(14.0, min(40.0, 22 + 3.5 * (v.z - 6)))
        show_labels = v.z >= 9
        visible = []
        for p in self.fleet.planes.values():
            lat, lon = self.fleet.position(p, now)
            sx, sy = v.to_screen(*geo.to_world(lat, lon))
            if -40 < sx < v.w + 40 and -40 < sy < v.h + 40:
                visible.append((p.get("alt") or 0, sx, sy, p))
        visible.sort(key=lambda t: t[0])   # higher aircraft on top

        for _, sx, sy, p in visible:
            color = plane_color(p)
            if (p.get("cat") or "").startswith("C"):   # ground vehicles and obstacles
                pygame.draw.circle(screen, HALO, (sx, sy), 5)
                pygame.draw.circle(screen, color, (sx, sy), 4)
                continue
            size = length * SIZE_BY_CAT.get(p.get("cat"), 0.9)
            t = math.radians(p.get("trk") or 0)
            c, s = math.cos(t) * size, math.sin(t) * size
            pts = [(sx + x * c - y * s, sy + x * s + y * c) for x, y in SILHOUETTE]
            pygame.draw.polygon(screen, color, pts)
            pygame.draw.aalines(screen, HALO, True, pts)
            if show_labels:
                text = p.get("cs") or p.get("reg") or ""
                if text:
                    label = self.label(text)
                    screen.blit(label, (sx - label.get_width() / 2, sy + size * 0.6))
        return len(visible)

    def label(self, text):
        surf = self.labels.get(text)
        if surf is None:
            if len(self.labels) > 600:
                self.labels.clear()
            surf = self.labels[text] = self.small.render(text, True, MUTED)
        return surf

    # ------------------------------------------------------------ status and buttons

    def draw_ui(self):
        screen = self.screen
        text = self.status_text()
        if text != self.pill[0]:
            self.pill = (text, self.font.render(text, True, TEXT))
        surf = self.pill[1]
        box = pygame.Rect(12, 12, surf.get_width() + 24, surf.get_height() + 14)
        pygame.draw.rect(screen, PANEL, box, border_radius=box.height // 2)
        screen.blit(surf, (box.x + 12, box.y + 7))

        for rect, kind in ((self.btn_in, "+"), (self.btn_out, "-"), (self.btn_home, "home")):
            pygame.draw.rect(screen, PANEL, rect, border_radius=8)
            cx, cy = rect.center
            if kind == "home":
                pygame.draw.polygon(screen, TEXT, [(cx, cy - 11), (cx + 11, cy), (cx + 7, cy),
                                                   (cx + 7, cy + 10), (cx - 7, cy + 10),
                                                   (cx - 7, cy), (cx - 11, cy)])
            else:
                pygame.draw.line(screen, TEXT, (cx - 9, cy), (cx + 9, cy), 3)
                if kind == "+":
                    pygame.draw.line(screen, TEXT, (cx, cy - 9), (cx, cy + 9), 3)

        a = self.attribution
        screen.blit(a, (self.view.w - a.get_width() - 6, self.view.h - a.get_height() - 4))

    def status_text(self):
        last = self.fleet.last_snapshot
        age = time.time() - last if last else math.inf
        stale = age > self.cfg["poll_interval_s"] * 3 + 2
        if not self.status.get("ok") and (stale or not last):
            return f"No data · last update {fmt_ago(age)}" if last else "No data · retrying"
        if not last:
            return "Connecting…"
        text = f"{self.fleet.source} · {len(self.fleet.planes)} aircraft"
        return text + (f" · {fmt_ago(age)}" if stale else "")

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

    def maybe_set_area(self):
        """Once the view settles, watch it if it's no longer covered (no "Search here" yet)."""
        if self.pointers or time.monotonic() - self.moved_at < SETTLE_S or self.moved_at == 0:
            return
        self.moved_at = 0
        lat, lon, r = self.area
        v = self.view
        covered = all(geo.distance_nm(lat, lon, *v.latlon(x, y)) <= r
                      for x, y in ((0, 0), (v.w, 0), (0, v.h), (v.w, v.h)))
        if not covered:
            self.set_area(self.view_area())

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
        elif e.type == pygame.MOUSEWHEEL and e.y:
            self.zoom_step(1 if e.y > 0 else -1, *pygame.mouse.get_pos())

    def pointer_down(self, pid, x, y):
        self.pointers[pid] = (x, y)
        if len(self.pointers) == 1:
            self.tap = (x, y)
        elif len(self.pointers) == 2:
            self.tap = None
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
        if self.pinch and len(self.pointers) >= 2:
            (ax, ay), (bx, by) = list(self.pointers.values())[:2]
            d0, z0, anchor = self.pinch
            z = z0 + math.log2(max(1.0, math.hypot(ax - bx, ay - by)) / d0)
            self.view.zoom_about(z, (ax + bx) / 2, (ay + by) / 2, anchor)
        elif not self.tap:
            self.view.pan(x - px, y - py)
        self.moved_at = time.monotonic()

    def pointer_up(self, pid, x, y):
        self.pointers.pop(pid, None)
        if self.pinch and len(self.pointers) < 2:
            # Settle on a whole zoom level so tiles are drawn crisp, at their real size.
            self.pinch = None
            self.view.zoom_about(round(self.view.z), x, y)
            self.moved_at = time.monotonic()
        if not self.pointers and self.tap:
            self.on_tap(*self.tap)
        if not self.pointers:
            self.tap = None

    def on_tap(self, x, y):
        v = self.view
        if self.btn_in.collidepoint(x, y):
            self.zoom_step(1, v.w / 2, v.h / 2)
        elif self.btn_out.collidepoint(x, y):
            self.zoom_step(-1, v.w / 2, v.h / 2)
        elif self.btn_home.collidepoint(x, y):
            start = self.cfg["start"]
            self.view = View(v.w, v.h, start["lat"], start["lon"], round(start["zoom"]))
            self.moved_at = time.monotonic()
        # Tapping a plane opens the status card in the full version (not in this prototype).

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
