"""The web server: serves the map page, the live WebSocket and the card endpoints."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .aeroapi import AeroAPI, TimesUnavailable
from .airports import Airports
from .config import Config
from .enrich import Enricher
from .live import Live
from .store import Store

log = logging.getLogger("flightmap")

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class FlightRequest(BaseModel):
    callsign: str = ""
    registration: str = ""
    refresh: bool = False  # True skips the cache (costs a lookup)


def create_app(cfg: Config, web_dir: Path = WEB_DIR) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = httpx.AsyncClient(
            timeout=cfg.request_timeout_s,
            headers={"User-Agent": cfg.user_agent},
        )
        store = Store(cfg.db_path)
        app.state.live = Live(cfg, client)
        app.state.enricher = Enricher(client, store, photos_enabled=bool(cfg.photo_contact))
        airports = Airports(cfg.db_path.parent)
        app.state.aero = AeroAPI(cfg, client, store, airports)
        if not cfg.photo_contact:
            log.warning("photos disabled: set [photos] contact (planespotters requires it)")
        tasks = [asyncio.create_task(app.state.live.run()),
                 asyncio.create_task(app.state.aero.sync_forever()),
                 asyncio.create_task(airports.ensure(client))]
        yield
        for t in tasks:
            t.cancel()
        await client.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

    @app.get("/api/config")
    async def get_config():
        return {
            "start": {"lat": cfg.start_lat, "lon": cfg.start_lon, "zoom": cfg.start_zoom},
            "poll_interval_s": cfg.poll_interval_s,
            # For the native display's raster tiles (the browser uses keyless vector tiles).
            "carto_key": cfg.carto_key,
        }

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        live: Live = app.state.live
        await socket.accept()
        await live.connect(socket)
        try:
            while True:
                msg = json.loads(await socket.receive_text())
                if msg.get("type") == "area":
                    live.set_area(float(msg["lat"]), float(msg["lon"]), float(msg["radius_nm"]))
        except (WebSocketDisconnect, ValueError, KeyError, TypeError):
            pass
        finally:
            live.disconnect(socket)

    @app.get("/api/aircraft/{hex_}")
    async def aircraft(hex_: str, callsign: str = "", lat: float | None = None,
                       lon: float | None = None):
        hex_ = hex_.lower()
        details = await app.state.enricher.details(hex_, callsign, lat, lon)
        details["trail"] = app.state.live.trail(hex_)
        details["times"] = app.state.aero.cached(hex_, callsign)
        details["budget"] = app.state.aero.budget()
        return details

    @app.post("/api/aircraft/{hex_}/flight")
    async def flight(hex_: str, req: FlightRequest):
        """AeroAPI times + filed route. Cached per flight, so re-taps are free."""
        aero: AeroAPI = app.state.aero
        try:
            result = await aero.flight(hex_.lower(), req.callsign, req.registration, req.refresh)
        except TimesUnavailable as e:
            return {"error": str(e), "kind": e.kind, "budget": aero.budget()}
        return {"times": result, "budget": aero.budget()}

    @app.get("/api/status")
    async def status():
        return {"providers": app.state.live.status(), "budget": app.state.aero.budget()}

    @app.post("/api/status/check")
    async def check():
        """Actively test every source. All of these requests are free."""
        aero: AeroAPI = app.state.aero
        live, cards, synced = await asyncio.gather(
            app.state.live.check(), app.state.enricher.check(), aero.sync_usage())
        aero_row = {"name": "FlightAware AeroAPI", "group": "Flight times", "ok": synced,
                    "detail": "Key accepted" if synced else (aero.sync_error or "Not set up")}
        if not aero.enabled:
            aero_row.update(ok=None, detail="Off: no API key in the config")
        return {"checks": [*live, *cards, aero_row], "providers": app.state.live.status(),
                "budget": aero.budget()}

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")
    return app
