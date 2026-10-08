"""FlightAware AeroAPI: flight times and the filed route, fetched when a plane is tapped.

Spending is capped below the free $10/month feeder allowance. We count every
call we make, and hourly fetch FlightAware's own usage figure (that call is
free). FlightAware's figure lags by hours, so we trust whichever is higher.
"""

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone

import httpx

from .airports import Airports
from .config import Config
from .store import Store

log = logging.getLogger("flightmap")

BASE = "https://aeroapi.flightaware.com/aeroapi"
SYNC_EVERY_S = 3600
RATE_LIMIT_PER_MIN = 10
AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}\d[A-Z0-9]{0,3}$")

TIME_FIELDS = [f"{kind}_{event}" for event in ("out", "off", "on", "in")
               for kind in ("scheduled", "estimated", "actual")]


class TimesUnavailable(Exception):
    """`kind` lets the card decide how loudly to say it: disabled, limit, no_flight, rate, error."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class AeroAPI:
    def __init__(self, cfg: Config, client: httpx.AsyncClient, store: Store, airports: Airports):
        self.cfg = cfg
        self.client = client
        self.store = store
        self.airports = airports
        self.lock = asyncio.Lock()
        self.recent: list[float] = []
        self.sync_error = ""

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.aeroapi_key)

    # ------------------------------------------------------------ budget

    def budget(self) -> dict:
        month = month_start()
        local_cost = self.store.cost_since(month.timestamp())
        local_calls = self.store.calls_since(month.timestamp())
        spent, calls = local_cost, local_calls
        sync = self.store.get_kv("usage_sync")
        if sync and sync.get("month") == month.strftime("%Y-%m"):
            spent = max(spent, sync["remote_total"] + self.store.cost_since(sync["synced_at"]))
            calls = max(calls, sync.get("remote_calls", 0) + self.store.calls_since(sync["synced_at"]))
        remaining = max(0.0, self.cfg.aeroapi_stop_at - spent)
        return {
            "enabled": self.enabled,
            "spent": round(spent, 4),
            "limit": self.cfg.aeroapi_stop_at,
            "remaining": round(remaining, 4),
            "exhausted": spent >= self.cfg.aeroapi_stop_at,
            "lookups_used": calls,
            "lookups_left": math.floor(remaining / self.cfg.aeroapi_cost_per_call + 1e-9),
            "cost_per_call": self.cfg.aeroapi_cost_per_call,
            "resets": next_month_start().date().isoformat(),
            "flightaware_reported": sync and {
                "spent": sync["remote_total"], "calls": sync.get("remote_calls", 0),
                "at": sync["synced_at"],
            },
            "sync_error": self.sync_error,
        }

    async def sync_usage(self) -> bool:
        """Ask FlightAware what this month has cost so far (a free call)."""
        if not self.enabled:
            return False
        month = month_start()
        try:
            resp = await self.client.get(
                f"{BASE}/account/usage",
                params={"start": month.strftime("%Y-%m-%dT%H:%M:%SZ")},
                headers={"x-apikey": self.cfg.aeroapi_key},
            )
            resp.raise_for_status()
            data = resp.json()
            total, calls = float(data["total_cost"]), int(data.get("total_calls") or 0)
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
            self.sync_error = describe_error(e)
            log.warning("AeroAPI usage sync failed (%s); using local count", self.sync_error)
            return False
        self.sync_error = ""
        self.store.set_kv("usage_sync", {"month": month.strftime("%Y-%m"), "remote_total": total,
                                         "remote_calls": calls, "synced_at": time.time()})
        log.info("AeroAPI usage this month (FlightAware): $%.4f, %d calls", total, calls)
        return True

    async def sync_forever(self):
        while True:
            await self.sync_usage()
            await asyncio.sleep(SYNC_EVERY_S)

    # ------------------------------------------------------------ flights

    def cached(self, hex_: str, callsign: str) -> dict | None:
        return self.store.get("times", cache_key(hex_, callsign))

    async def flight(self, hex_: str, callsign: str, registration: str, refresh: bool) -> dict:
        """Times and filed route for the flight this aircraft is on. Cached until it lands."""
        if not self.enabled:
            raise TimesUnavailable("disabled", "Flight times not set up (no AeroAPI key).")
        callsign, registration = callsign.strip().upper(), registration.strip().upper()
        if not refresh and (hit := self.cached(hex_, callsign)):
            return hit
        if AIRLINE_CALLSIGN.match(callsign):
            ident, ident_type = callsign, "designator"
        elif registration or callsign:
            ident, ident_type = registration or callsign, "registration"
        else:
            raise TimesUnavailable("no_flight", "No flight plan found for this aircraft.")

        async with self.lock:
            b = self.budget()
            if b["exhausted"]:
                raise TimesUnavailable("limit", "Flight times unavailable: monthly lookup limit "
                                                f"reached (resets {format_reset(b['resets'])}).")
            now = time.time()
            self.recent = [t for t in self.recent if t > now - 60]
            if len(self.recent) >= RATE_LIMIT_PER_MIN:
                raise TimesUnavailable("rate", "Too many lookups this minute. Try again shortly.")
            self.recent.append(now)

            try:
                resp = await self.client.get(
                    f"{BASE}/flights/{ident}",
                    params={"ident_type": ident_type, "max_pages": 1},
                    headers={"x-apikey": self.cfg.aeroapi_key},
                )
            except httpx.HTTPError as e:
                log.warning("AeroAPI request failed: %s", describe_error(e))
                raise TimesUnavailable("error", "Flight times unavailable: couldn't reach FlightAware.")
            if resp.status_code in (401, 403):
                raise TimesUnavailable("error", "Flight times unavailable: AeroAPI key was rejected.")
            if resp.status_code == 429:
                raise TimesUnavailable("rate", "FlightAware is rate limiting. Try again in a minute.")
            if resp.status_code == 404:
                raise TimesUnavailable("no_flight", "No flight plan found for this aircraft.")
            if resp.status_code != 200:
                raise TimesUnavailable("error", f"Flight times unavailable (FlightAware HTTP {resp.status_code}).")
            self.store.log_call(ident, self.cfg.aeroapi_cost_per_call)

        flight = pick_flight(resp.json().get("flights") or [], time.time())
        if not flight:
            raise TimesUnavailable("no_flight", "No flight plan found for this aircraft.")
        result = summarize(flight, self.airports)
        self.store.put("times", cache_key(hex_, callsign), result, cache_ttl(flight))
        return result


# ---------------------------------------------------------------- helpers

def describe_error(e: Exception) -> str:
    if isinstance(e, httpx.HTTPStatusError):
        return f"HTTP {e.response.status_code}"
    return e.__class__.__name__


def cache_key(hex_: str, callsign: str) -> str:
    return f"{hex_.lower()}:{callsign.strip().upper()}"


def parse_ts(s) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def pick_flight(flights: list[dict], now: float) -> dict | None:
    """The flight the aircraft is on now, or else the one closest in time."""
    flights = [f for f in flights if not f.get("cancelled")]
    if not flights:
        return None
    airborne = [f for f in flights if f.get("actual_off") and not f.get("actual_on")]
    if airborne:
        return max(airborne, key=lambda f: parse_ts(f["actual_off"]) or 0)

    def when(f):
        for k in ("actual_off", "estimated_off", "scheduled_off", "scheduled_out"):
            if t := parse_ts(f.get(k)):
                return t
        return float("inf")
    return min(flights, key=lambda f: abs(when(f) - now))


def place(p: dict | None, airports: Airports) -> dict:
    """An AeroAPI airport, in the same shape as the free route's, plus timezone."""
    p = p or {}
    hit = airports.lookup(p.get("code_icao"), p.get("code"), p.get("code_iata"), p.get("code_lid"))
    return {
        "icao": p.get("code_icao") or (hit[1] if hit else "") or "",
        "iata": p.get("code_iata") or (hit[2] if hit else "") or "",
        "name": p.get("name") or (hit[3] if hit else ""),
        "city": p.get("city") or (hit[4] if hit else ""),
        "lat": hit[5] if hit else None,
        "lon": hit[6] if hit else None,
        "timezone": p.get("timezone") or "",
        "code": p.get("code_iata") or p.get("code_icao") or p.get("code_lid") or p.get("code") or "",
    }


def summarize(f: dict, airports: Airports) -> dict:
    out = {k: f.get(k) for k in TIME_FIELDS}
    origin, destination = place(f.get("origin"), airports), place(f.get("destination"), airports)
    out.update({
        "ident": f.get("ident_iata") or f.get("ident") or "",
        "status": f.get("status") or "",
        "origin": origin,
        "destination": destination,
        "route": {"origin": origin, "destination": destination, "plausible": True,
                  "source": "FlightAware"} if f.get("origin") and f.get("destination") else None,
        "departure_delay": f.get("departure_delay"),
        "arrival_delay": f.get("arrival_delay"),
        "gate_origin": f.get("gate_origin") or "",
        "gate_destination": f.get("gate_destination") or "",
        "terminal_origin": f.get("terminal_origin") or "",
        "terminal_destination": f.get("terminal_destination") or "",
        "progress_percent": f.get("progress_percent"),
        "diverted": bool(f.get("diverted")),
        "fetched_at": time.time(),
    })
    return out


def cache_ttl(f: dict) -> float:
    """Keep the result until the flight has landed (plus an hour), within sane bounds."""
    now = time.time()
    end = None
    for k in ("actual_in", "actual_on", "estimated_in", "estimated_on", "scheduled_in"):
        if t := parse_ts(f.get(k)):
            end = t
            break
    if end is None:
        return 6 * 3600
    return min(18 * 3600, max(15 * 60, end + 3600 - now))


def month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def next_month_start() -> datetime:
    m = month_start()
    return m.replace(year=m.year + (m.month == 12), month=m.month % 12 + 1)


def format_reset(iso_date: str) -> str:
    d = datetime.fromisoformat(iso_date)
    return f"{d.strftime('%b')} {d.day}"
