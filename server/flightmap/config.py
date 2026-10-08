"""Load and validate config.toml (on the Pi: /etc/flight-map/config.toml)."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path("/etc/flight-map/config.toml")
DEFAULT_DB = Path("/var/lib/flight-map/flightmap.db")
PROVIDERS = ("airplaneslive", "adsbfi", "adsblol")


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # [server]
    host: str = "127.0.0.1"
    port: int = 8080
    db_path: Path = DEFAULT_DB
    # [map]
    start_lat: float = 39.83
    start_lon: float = -98.58
    start_zoom: float = 7.0
    carto_key: str = ""
    # [live]
    poll_interval_s: float = 5.0
    request_timeout_s: float = 8.0
    providers: list[str] = field(default_factory=lambda: list(PROVIDERS))
    # [photos]
    photo_contact: str = ""
    # [aeroapi]
    aeroapi_key: str = ""
    aeroapi_stop_at: float = 9.50
    aeroapi_cost_per_call: float = 0.005

    @property
    def user_agent(self) -> str:
        contact = f" (+{self.photo_contact})" if self.photo_contact else ""
        return f"flightmap/0.1{contact}"


def load(path: Path = DEFAULT_PATH) -> Config:
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (copy config.example.toml there)")
    except PermissionError:
        raise ConfigError(f"{path} is not readable by this user (it holds the AeroAPI key)")
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}")

    server, map_, live = raw.get("server", {}), raw.get("map", {}), raw.get("live", {})
    photos, aero = raw.get("photos", {}), raw.get("aeroapi", {})
    d = Config()
    cfg = Config(
        host=server.get("host", d.host),
        port=int(server.get("port", d.port)),
        db_path=Path(server.get("db_path", d.db_path)),
        start_lat=float(map_.get("start_lat", d.start_lat)),
        start_lon=float(map_.get("start_lon", d.start_lon)),
        start_zoom=float(map_.get("start_zoom", d.start_zoom)),
        carto_key=map_.get("carto_key", d.carto_key).strip(),
        poll_interval_s=float(live.get("poll_interval_s", d.poll_interval_s)),
        request_timeout_s=float(live.get("request_timeout_s", d.request_timeout_s)),
        providers=list(live.get("providers", d.providers)),
        photo_contact=photos.get("contact", d.photo_contact).strip(),
        aeroapi_key=aero.get("api_key", d.aeroapi_key).strip(),
        aeroapi_stop_at=float(aero.get("stop_at", d.aeroapi_stop_at)),
        aeroapi_cost_per_call=float(aero.get("cost_per_call", d.aeroapi_cost_per_call)),
    )

    if not -90 <= cfg.start_lat <= 90 or not -180 <= cfg.start_lon <= 180:
        raise ConfigError("map.start_lat / start_lon out of range")
    if cfg.poll_interval_s < 2:
        raise ConfigError("live.poll_interval_s must be at least 2 (be nice to the free APIs)")
    if bad := [p for p in cfg.providers if p not in PROVIDERS]:
        raise ConfigError(f"live.providers: unknown {bad}; choose from {list(PROVIDERS)}")
    if not cfg.providers:
        raise ConfigError("live.providers must list at least one provider")
    if not 0 < cfg.aeroapi_stop_at <= 10:
        raise ConfigError("aeroapi.stop_at must be between 0 and 10 (the free feeder allowance)")
    return cfg
