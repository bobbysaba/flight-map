"""Raster map tiles: fetched in the background, cached on disk and (a few) in memory.

Workers download the base map and its label layer, combine them and cache the
result; the main thread only converts finished tiles to the screen's pixel
format, so drawing never waits on the network.
"""

import io
import logging
import os
import queue
import threading
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path

import pygame

log = logging.getLogger("flightmap.native")

# Esri's Dark Gray Canvas: a base layer plus a transparent label layer on top. No key
# needed. (CARTO's dark tiles, the usual free choice, now require an API key.)
_ESRI = "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/{}/MapServer/tile/{{z}}/{{y}}/{{x}}"
LAYERS = [_ESRI.format("World_Dark_Gray_Base"), _ESRI.format("World_Dark_Gray_Reference")]
ATTRIBUTION = "Esri, HERE, Garmin, © OpenStreetMap contributors"
MAX_ZOOM = 16
MEMORY_TILES = 96          # 256×256×4 bytes each: ~24 MB
WORKERS = 4
RETRY_AFTER_S = 20


class Tiles:
    def __init__(self, user_agent: str, cache_dir: Path | None = None):
        base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
        self.dir = cache_dir or Path(base) / "flightmap" / "tiles"
        self.user_agent = user_agent
        self.mem: OrderedDict[tuple, pygame.Surface] = OrderedDict()
        self.wanted: set[tuple] = set()
        self.pending: set[tuple] = set()
        self.failed: dict[tuple, float] = {}
        self.todo: queue.LifoQueue = queue.LifoQueue()   # newest view first
        self.done: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        for _ in range(WORKERS):
            threading.Thread(target=self._worker, daemon=True).start()

    # ------------------------------------------------------------ main thread

    def get(self, key):
        """The tile's surface if it's in memory; otherwise None (and it gets fetched)."""
        surf = self.mem.get(key)
        if surf is not None:
            self.mem.move_to_end(key)
            return surf
        if key not in self.pending and time.monotonic() - self.failed.get(key, 0) > RETRY_AFTER_S:
            with self.lock:
                self.pending.add(key)
            self.todo.put(key)
        return None

    def peek(self, key):
        """In memory only; never triggers a fetch (used for low-res stand-ins)."""
        return self.mem.get(key)

    def want(self, keys):
        with self.lock:
            self.wanted = set(keys)

    def collect(self) -> bool:
        """Move finished downloads into memory. True if anything new arrived."""
        got = False
        while True:
            try:
                key, surf = self.done.get_nowait()
            except queue.Empty:
                return got
            with self.lock:
                self.pending.discard(key)
            if surf is None:
                self.failed[key] = time.monotonic()
                continue
            self.mem[key] = surf.convert()
            while len(self.mem) > MEMORY_TILES:
                self.mem.popitem(last=False)
            got = True

    # ------------------------------------------------------------ workers

    def _worker(self):
        while True:
            key = self.todo.get()
            with self.lock:
                stale = key not in self.wanted
            if stale:  # panned away before we got to it
                with self.lock:
                    self.pending.discard(key)
                continue
            try:
                surf = self._load(key)
            except Exception as e:  # network, HTTP or decode error: retried later
                log.debug("tile %s failed: %s", key, e)
                surf = None
            self.done.put((key, surf))

    def _load(self, key) -> pygame.Surface:
        z, x, y = key
        path = self.dir / str(z) / str(x) / f"{y}.png"
        if path.exists():
            return pygame.image.load(str(path))
        surf = None
        for url in LAYERS:
            req = urllib.request.Request(url.format(z=z, x=x, y=y),
                                         headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=10) as resp:
                kind = "tile.jpg" if "jpeg" in resp.headers.get("Content-Type", "") else "tile.png"
                layer = pygame.image.load(io.BytesIO(resp.read()), kind)
            if surf is None:
                surf = layer
            else:
                surf.blit(layer, (0, 0))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{y}.tmp.png")
        pygame.image.save(surf, str(tmp))
        tmp.replace(path)
        return surf
