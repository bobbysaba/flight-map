"""Geo math (nautical miles, degrees) and Web Mercator, shared by the native display."""

import math

R_NM = 3440.065
TILE = 256


def distance_nm(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * R_NM * math.asin(min(1.0, math.sqrt(a)))


def destination(lat, lon, bearing_deg, dist_nm):
    d, b = dist_nm / R_NM, math.radians(bearing_deg)
    p1, l1 = math.radians(lat), math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(b))
    l2 = l1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), (math.degrees(l2) + 540) % 360 - 180


# Mercator in "world units": the whole world is 0..1 on both axes, y down.
MAX_LAT = 85.0511

def to_world(lat, lon):
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    s = math.sin(math.radians(lat))
    return (lon + 180) / 360, 0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)


def from_world(x, y):
    lon = x * 360 - 180
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
    return lat, (lon + 540) % 360 - 180


def wrap_lon(lon):
    return (lon + 540) % 360 - 180
