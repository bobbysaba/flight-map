"""Airport coordinates from OurAirports, for routes that come without them (AeroAPI).

The CSV (~13 MB) is downloaded next to the database on first start and refreshed
monthly; it's public domain.
"""

import asyncio
import csv
import logging
import os
import time
from pathlib import Path

import httpx

log = logging.getLogger("flightmap")

URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
MAX_AGE_S = 30 * 24 * 3600
SIZE_RANK = {"large_airport": 0, "medium_airport": 1, "small_airport": 2, "heliport": 3,
             "seaplane_base": 4, "balloonport": 5}


class Airports:
    def __init__(self, data_dir: Path):
        self.path = data_dir / "airports.csv"
        self.by_code: dict[str, tuple] = {}

    @property
    def loaded(self) -> bool:
        return bool(self.by_code)

    async def ensure(self, client: httpx.AsyncClient):
        """Load the airport list, downloading it first if missing or a month old."""
        stale = not self.path.exists() or time.time() - self.path.stat().st_mtime > MAX_AGE_S
        if stale:
            try:
                await self._download(client)
            except (httpx.HTTPError, OSError) as e:
                log.warning("airport list download failed (%s)%s", e,
                            "; using the old copy" if self.path.exists() else "")
        if self.path.exists():
            self.by_code = await asyncio.to_thread(self._parse)
            log.info("airport list: %d codes", len(self.by_code))

    async def _download(self, client: httpx.AsyncClient):
        tmp = self.path.with_suffix(".tmp")
        async with client.stream("GET", URL, timeout=120) as resp:
            resp.raise_for_status()
            with open(tmp, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
        os.replace(tmp, self.path)

    def _parse(self) -> dict[str, tuple]:
        best: dict[str, tuple] = {}
        with open(self.path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rank = SIZE_RANK.get(row["type"])
                if rank is None:  # closed airports
                    continue
                try:
                    lat, lon = float(row["latitude_deg"]), float(row["longitude_deg"])
                except ValueError:
                    continue
                icao = row.get("icao_code") or row.get("gps_code") or row["ident"]
                entry = (rank, icao, row["iata_code"], row["name"], row["municipality"], lat, lon)
                for code in {row["ident"], row.get("icao_code"), row.get("gps_code"),
                             row["iata_code"], row.get("local_code")}:
                    if code and (code not in best or rank < best[code][0]):
                        best[code] = entry
        return best

    def lookup(self, *codes) -> tuple | None:
        for code in codes:
            if code and (hit := self.by_code.get(code.upper())):
                return hit
        return None
