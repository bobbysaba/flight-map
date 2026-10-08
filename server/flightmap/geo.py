"""Great-circle math in nautical miles."""

from math import asin, atan2, cos, degrees, radians, sin, sqrt

EARTH_NM = 3440.065


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = p2 - p1, radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * EARTH_NM * asin(min(1.0, sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = radians(lat1), radians(lat2)
    dl = radians(lon2 - lon1)
    y = sin(dl) * cos(p2)
    x = cos(p1) * sin(p2) - sin(p1) * cos(p2) * cos(dl)
    return (degrees(atan2(y, x)) + 360) % 360


def off_route_nm(lat: float, lon: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """How far a point is from the great-circle segment a→b (0 if on it).

    Uses the detour distance (via the point vs. direct), which is cheap and
    good enough to pick the right leg of a multi-stop route.
    """
    direct = distance_nm(*a, *b)
    via = distance_nm(*a, lat, lon) + distance_nm(lat, lon, *b)
    return max(0.0, via - direct)
