"""Free details for the status card: aircraft info, route, photo. All cached."""

import asyncio
import logging
import re
import time

import httpx

from . import geo
from .store import Store

log = logging.getLogger("flightmap")

DAY = 24 * 3600
ROUTE_TTL = DAY
MISS_TTL = 6 * 3600       # remember "no data" for a while so we don't keep asking
AIRCRAFT_TTL = 30 * DAY
PHOTO_TTL = 7 * DAY

VRS_ROUTE = "https://vrs-standing-data.adsb.lol/routes/{prefix}/{callsign}.json"
ADSBDB_CALLSIGN = "https://api.adsbdb.com/v0/callsign/{callsign}"
ADSBDB_AIRCRAFT = "https://api.adsbdb.com/v0/aircraft/{hex}"
ADSBDB_AIRLINE = "https://api.adsbdb.com/v0/airline/{icao}"
PLANESPOTTERS = "https://api.planespotters.net/pub/photos/hex/{hex}"

# Airline-style callsigns (UAL1234, BAW12K). Registrations like N572JA have no route.
AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}\d[A-Z0-9]{0,3}$")


class Enricher:
    def __init__(self, client: httpx.AsyncClient, store: Store, photos_enabled: bool):
        self.client = client
        self.store = store
        self.photos_enabled = photos_enabled

    async def details(self, hex_: str, callsign: str, lat: float | None, lon: float | None):
        aircraft, airline, route, photo = await asyncio.gather(
            self.aircraft(hex_), self.airline(callsign), self.route(callsign, lat, lon),
            self.photo(hex_),
        )
        return {"aircraft": aircraft, "airline": airline, "route": route, "photo": photo}

    # ------------------------------------------------------------ aircraft

    async def aircraft(self, hex_: str) -> dict | None:
        cached = self.store.get("aircraft", hex_)
        if cached is not None:
            return cached or None
        data = await self._get_json(ADSBDB_AIRCRAFT.format(hex=hex_))
        a = ((data or {}).get("response") or {})
        a = a.get("aircraft") if isinstance(a, dict) else None
        info = {}
        if a:
            info = {
                "registration": a.get("registration") or "",
                "type": a.get("icao_type") or "",
                "model": " ".join(x for x in (a.get("manufacturer"), a.get("type")) if x),
                "owner": a.get("registered_owner") or "",
                "country": a.get("registered_owner_country_name") or "",
            }
        self.store.put("aircraft", hex_, info, AIRCRAFT_TTL if info else MISS_TTL)
        return info or None

    # ------------------------------------------------------------ airline

    async def airline(self, callsign: str) -> str | None:
        """Airline name from the callsign prefix (UAL1234 → United Airlines)."""
        callsign = callsign.strip().upper()
        if not AIRLINE_CALLSIGN.match(callsign):
            return None
        icao = callsign[:3]
        cached = self.store.get("airline", icao)
        if cached is not None:
            return cached or None
        data = await self._get_json(ADSBDB_AIRLINE.format(icao=icao))
        rows = (data or {}).get("response")
        name = rows[0].get("name", "") if isinstance(rows, list) and rows else ""
        if data is not None:
            self.store.put("airline", icao, name, AIRCRAFT_TTL if name else MISS_TTL)
        return name or None

    # ------------------------------------------------------------ route

    async def route(self, callsign: str, lat, lon) -> dict | None:
        callsign = callsign.strip().upper()
        if not AIRLINE_CALLSIGN.match(callsign):
            return None
        airports = self.store.get("route", callsign)
        if airports is None:
            airports = await self._route_vrs(callsign) or await self._route_adsbdb(callsign) or []
            self.store.put("route", callsign, airports, ROUTE_TTL if airports else MISS_TTL)
        if len(airports) < 2:
            return None
        return pick_leg(airports, lat, lon)

    async def _route_vrs(self, callsign: str) -> list | None:
        data = await self._get_json(VRS_ROUTE.format(prefix=callsign[:2], callsign=callsign))
        if not data or not data.get("_airports"):
            return None
        return [airport(a.get("icao"), a.get("iata"), a.get("name"), a.get("location"),
                        a.get("lat"), a.get("lon")) for a in data["_airports"]]

    async def _route_adsbdb(self, callsign: str) -> list | None:
        data = await self._get_json(ADSBDB_CALLSIGN.format(callsign=callsign))
        r = (data or {}).get("response")
        fr = r.get("flightroute") if isinstance(r, dict) else None
        if not fr:
            return None
        return [airport(a.get("icao_code"), a.get("iata_code"), a.get("name"),
                        a.get("municipality"), a.get("latitude"), a.get("longitude"))
                for a in (fr.get("origin"), fr.get("destination")) if a]

    # ------------------------------------------------------------ photo

    async def photo(self, hex_: str) -> dict | None:
        if not self.photos_enabled:
            return None
        cached = self.store.get("photo", hex_)
        if cached is not None:
            return cached or None
        data = await self._get_json(PLANESPOTTERS.format(hex=hex_))
        photos = (data or {}).get("photos") or []
        info = {}
        if photos:
            p = photos[0]
            img = p.get("thumbnail_large") or p.get("thumbnail") or {}
            info = {"src": img.get("src"), "link": p.get("link"),
                    "photographer": p.get("photographer")}
        if data is not None:
            self.store.put("photo", hex_, info, PHOTO_TTL if info else MISS_TTL)
        return info or None

    # ------------------------------------------------------------ health

    async def check(self) -> list[dict]:
        """Hit each free card source once with a known-good request."""
        tests = [
            ("Routes (adsb.lol)", VRS_ROUTE.format(prefix="AA", callsign="AAL100")),
            ("Routes, aircraft & airlines (adsbdb)", ADSBDB_AIRLINE.format(icao="AAL")),
        ]
        if self.photos_enabled:
            tests.append(("Photos (planespotters)", PLANESPOTTERS.format(hex="a8ebbd")))

        async def one(name, url):
            started = time.monotonic()
            try:
                resp = await self.client.get(url, follow_redirects=True)
            except httpx.HTTPError as e:
                return {"name": name, "group": "Status card", "ok": False, "detail": e.__class__.__name__}
            ms = round((time.monotonic() - started) * 1000)
            ok = resp.status_code == 200
            return {"name": name, "group": "Status card", "ok": ok, "ms": ms,
                    "detail": "OK" if ok else f"HTTP {resp.status_code}"}
        results = list(await asyncio.gather(*(one(n, u) for n, u in tests)))
        if not self.photos_enabled:
            results.append({"name": "Photos (planespotters)", "group": "Status card", "ok": None,
                            "detail": "Off: set [photos] contact in the config"})
        return results

    # ------------------------------------------------------------

    async def _get_json(self, url: str):
        """GET JSON; None on 404 or any failure (the card just shows less)."""
        try:
            resp = await self.client.get(url, follow_redirects=True)
        except httpx.HTTPError as e:
            log.warning("GET %s failed: %s", url, e.__class__.__name__)
            return None
        if resp.status_code != 200:
            if resp.status_code != 404:
                log.warning("GET %s: HTTP %s", url, resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            return None


def airport(icao, iata, name, city, lat, lon) -> dict:
    return {"icao": icao or "", "iata": iata or "", "name": name or "", "city": city or "",
            "lat": lat, "lon": lon}


def pick_leg(airports: list[dict], lat, lon) -> dict:
    """Multi-stop routes (KJFK-KORD-KSFO) list every stop; pick the leg we're on."""
    legs = list(zip(airports, airports[1:]))
    if lat is None or lon is None or any(a["lat"] is None for a in airports):
        a, b = legs[0]
        return {"origin": a, "destination": b, "plausible": None, "source": "crowd"}

    def off(leg):
        a, b = leg
        return geo.off_route_nm(lat, lon, (a["lat"], a["lon"]), (b["lat"], b["lon"]))

    a, b = min(legs, key=off)
    length = geo.distance_nm(a["lat"], a["lon"], b["lat"], b["lon"])
    # Callsign routes are crowd-sourced and sometimes stale; flag obvious mismatches.
    plausible = off((a, b)) <= max(100.0, 0.25 * length)
    return {"origin": a, "destination": b, "plausible": plausible, "source": "crowd"}
