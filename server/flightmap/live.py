"""Polls the active area and pushes snapshots to connected screens.

Only one area is live at a time: whatever the screen last asked for with
"Search here". Nothing is fetched while no screen is connected.
"""

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass

import httpx

from . import providers
from .config import Config

log = logging.getLogger("flightmap")

TRAIL_MAX_AGE_S = 90 * 60
FORGET_AFTER_S = 15 * 60
MIN_GAP_S = 1.1  # never hit one provider more than about once a second


@dataclass
class Area:
    lat: float
    lon: float
    radius_nm: float

    def as_dict(self):
        return {"lat": self.lat, "lon": self.lon, "radius_nm": self.radius_nm}


@dataclass
class ProviderState:
    failures: int = 0
    cooldown_until: float = 0.0
    last_request: float = 0.0
    last_error: str = ""
    last_error_at: float = 0.0  # wall clock
    last_ok_at: float = 0.0     # wall clock
    last_ms: int | None = None


class Live:
    def __init__(self, cfg: Config, client: httpx.AsyncClient):
        self.cfg = cfg
        self.client = client
        self.order = [providers.ALL[name] for name in cfg.providers]
        self.state = {p.name: ProviderState() for p in self.order}
        self.area = Area(cfg.start_lat, cfg.start_lon, 100)
        self.sockets: set = set()
        self.trails: dict[str, deque] = {}
        self.last_seen: dict[str, float] = {}
        self.latest: dict[str, dict] = {}
        self.source = ""
        self._wake = asyncio.Event()
        self._last_status: dict | None = None

    # ------------------------------------------------------------ screen side

    async def connect(self, ws):
        self.sockets.add(ws)
        self._wake.set()
        if self._last_status:
            await ws.send_text(json.dumps(self._last_status))

    def disconnect(self, ws):
        self.sockets.discard(ws)

    def set_area(self, lat: float, lon: float, radius_nm: float):
        radius_nm = max(1.0, min(providers.MAX_RADIUS_NM, radius_nm))
        self.area = Area(lat, lon, radius_nm)
        log.info("area: %.3f,%.3f r=%.0fnm", lat, lon, radius_nm)
        self._wake.set()

    def trail(self, hex_: str) -> list:
        return list(self.trails.get(hex_, ()))

    # ------------------------------------------------------------ health

    def status(self) -> list[dict]:
        now = time.monotonic()
        return [{
            "name": p.title,
            "in_use": p.title == self.source,
            "cooling_down_s": max(0, round(self.state[p.name].cooldown_until - now)),
            "last_ok_at": self.state[p.name].last_ok_at or None,
            "last_ms": self.state[p.name].last_ms,
            "last_error": self.state[p.name].last_error,
            "last_error_at": self.state[p.name].last_error_at or None,
        } for p in self.order]

    async def check(self) -> list[dict]:
        """Test every provider now with a tiny query (they're free, but rate limited)."""
        tiny = Area(self.area.lat, self.area.lon, 5)

        async def one(p):
            st = self.state[p.name]
            if p.title == self.source and time.time() - st.last_ok_at < 2 * self.cfg.poll_interval_s:
                # Already proven by live polling; an extra request would only trip its rate limit.
                return {"name": p.title, "group": "Live positions", "ok": True, "ms": st.last_ms,
                        "detail": "Responding (live polling)"}
            started = time.monotonic()
            try:
                await self._fetch(p, tiny)
            except providers.ProviderError as e:
                return {"name": p.title, "group": "Live positions", "ok": False, "detail": str(e)}
            return {"name": p.title, "group": "Live positions", "ok": True,
                    "ms": round((time.monotonic() - started) * 1000), "detail": "Responding"}
        return list(await asyncio.gather(*(one(p) for p in self.order)))

    # ------------------------------------------------------------ polling

    async def run(self):
        while True:
            self._wake.clear()
            started = time.monotonic()
            if self.sockets:
                await self._poll_once()
            wait = self.cfg.poll_interval_s - (time.monotonic() - started)
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=max(0.1, wait))
            except asyncio.TimeoutError:
                pass

    async def _fetch(self, p: providers.Provider, area: Area):
        """One request to one provider, keeping its health record up to date."""
        st = self.state[p.name]
        gap = MIN_GAP_S - (time.monotonic() - st.last_request)
        if gap > 0:
            await asyncio.sleep(gap)
        st.last_request = started = time.monotonic()
        try:
            result = await p.fetch(self.client, area.lat, area.lon, area.radius_nm)
        except providers.ProviderError as e:
            st.failures += 1
            st.cooldown_until = time.monotonic() + min(300, 15 * 2 ** (st.failures - 1))
            st.last_error, st.last_error_at = str(e), time.time()
            raise
        st.failures, st.cooldown_until = 0, 0.0
        st.last_ok_at, st.last_ms = time.time(), round((time.monotonic() - started) * 1000)
        return result

    async def _poll_once(self):
        area = self.area
        errors = []
        for p in self._candidates():
            try:
                now, aircraft = await self._fetch(p, area)
            except providers.ProviderError as e:
                errors.append(f"{p.title}: {e}")
                log.warning("%s failed (%s), trying next", p.title, e)
                continue
            self.source = p.title
            if area is not self.area:
                return  # the screen moved on while we were fetching; refetch right away
            self._record(now, aircraft)
            await self._broadcast({
                "type": "snapshot",
                "source": p.title,
                "now": now,
                "area": area.as_dict(),
                "ac": aircraft,
            })
            return
        await self._broadcast({
            "type": "status",
            "ok": False,
            "error": "; ".join(errors) or "all sources cooling down after errors",
        })

    def _candidates(self):
        """Healthy providers in configured order; if none, the one that recovers first."""
        now = time.monotonic()
        ready = [p for p in self.order if self.state[p.name].cooldown_until <= now]
        if ready:
            return ready
        return [min(self.order, key=lambda p: self.state[p.name].cooldown_until)]

    def _record(self, now: float, aircraft: list[dict]):
        for a in aircraft:
            h = a["hex"]
            self.latest[h] = a
            self.last_seen[h] = now
            trail = self.trails.setdefault(h, deque())
            point = [a["t"], round(a["lat"], 5), round(a["lon"], 5), a["alt"]]
            if not trail or trail[-1][0] < point[0]:
                trail.append(point)
            while trail and trail[0][0] < now - TRAIL_MAX_AGE_S:
                trail.popleft()
        for h in [h for h, seen in self.last_seen.items() if seen < now - FORGET_AFTER_S]:
            del self.last_seen[h]
            self.trails.pop(h, None)
            self.latest.pop(h, None)

    async def _broadcast(self, msg: dict):
        if msg["type"] == "status":
            self._last_status = msg
        else:
            self._last_status = None
        text = json.dumps(msg, separators=(",", ":"))
        dead = []
        for ws in list(self.sockets):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.sockets.discard(ws)
