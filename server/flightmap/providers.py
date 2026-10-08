"""The free position APIs. All three serve readsb-style JSON for a point + radius.

Differences we smooth over: adsb.fi calls the list "aircraft" (the others "ac")
and gives "now" in seconds (the others in milliseconds).
"""

from dataclasses import dataclass

import httpx

MAX_RADIUS_NM = 250  # airplanes.live and adsb.fi reject anything larger


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    title: str
    url_format: str

    def url(self, lat: float, lon: float, radius_nm: float) -> str:
        r = max(1, min(MAX_RADIUS_NM, round(radius_nm)))
        return self.url_format.format(lat=round(lat, 4), lon=round(lon, 4), r=r)

    async def fetch(self, client: httpx.AsyncClient, lat: float, lon: float,
                    radius_nm: float) -> tuple[float, list[dict]]:
        """Return (data timestamp in epoch seconds, normalized aircraft)."""
        try:
            resp = await client.get(self.url(lat, lon, radius_nm))
        except httpx.TimeoutException:
            raise ProviderError("timed out")
        except httpx.HTTPError as e:
            raise ProviderError(f"network error: {e.__class__.__name__}")
        if resp.status_code == 429:
            raise ProviderError("rate limited")
        if resp.status_code != 200:
            raise ProviderError(f"HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise ProviderError("bad JSON")
        now = float(data.get("now") or 0)
        if now > 1e11:
            now /= 1000
        raw = data.get("ac", data.get("aircraft"))
        if raw is None:
            raise ProviderError("no aircraft list in response")
        return now, [a for a in (normalize(x, now) for x in raw) if a]


def normalize(a: dict, now: float) -> dict | None:
    """Trim a readsb aircraft record to what the map needs."""
    lat, lon = a.get("lat"), a.get("lon")
    if lat is None or lon is None:
        return None
    alt = a.get("alt_baro")
    ground = alt == "ground"
    if ground or not isinstance(alt, (int, float)):
        alt = a.get("alt_geom") if not ground else 0
    track = a.get("track", a.get("true_heading", a.get("mag_heading")))
    return {
        "hex": a.get("hex", "").lower(),
        "cs": (a.get("flight") or "").strip(),
        "reg": a.get("r") or "",
        "type": a.get("t") or "",
        "desc": a.get("desc") or "",
        "op": a.get("ownOp") or "",
        "cat": a.get("category") or "",
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "gnd": ground,
        "gs": a.get("gs"),
        "trk": track,
        "vr": a.get("baro_rate", a.get("geom_rate")),
        "sq": a.get("squawk") or "",
        "emerg": a.get("emergency") if a.get("emergency") not in (None, "none") else "",
        "mil": bool((a.get("dbFlags") or 0) & 1),
        # When the position was actually received, so the browser can extrapolate exactly.
        "t": round(now - float(a.get("seen_pos") or 0), 2),
    }


ALL = {
    p.name: p for p in [
        Provider("airplaneslive", "airplanes.live",
                 "https://api.airplanes.live/v2/point/{lat}/{lon}/{r}"),
        Provider("adsbfi", "adsb.fi",
                 "https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{r}"),
        Provider("adsblol", "adsb.lol",
                 "https://api.adsb.lol/v2/point/{lat}/{lon}/{r}"),
    ]
}
