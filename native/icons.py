"""Aircraft silhouettes for the native display: a port of web/src/icons.js.

Shapes are drawn nose-up in a 64-unit box centred on (0, 0), the same units as the
browser version. Each icon is drawn at 4x, given its dark halo, rotated, then
shrunk, so edges are smooth and the halo lines up with the fill. Icons are cached:
each (shape, size, colour, heading) is drawn once.
"""

import math
from collections import OrderedDict

import pygame

SS = 4              # supersampling factor
BOX = 80            # canvas size in shape units (64 + padding), as in icons.js
HALO_PX = 1.2       # halo width on screen, as in the browser's icon-halo-width
HEADING_STEP = 3    # degrees
CACHE = 3000

# ---------------------------------------------------------------- drawing

def _mirrored(surf, color, k, c, pts):
    """pts: right half, from the centreline back to the centreline."""
    full = pts + [(-x, y) for x, y in reversed(pts)]
    pygame.draw.polygon(surf, color, [(c + x * k, c + y * k) for x, y in full])


def _capsule(surf, color, k, c, x, y1, y2, w):
    rect = pygame.Rect(0, 0, max(1, round(w * k)), max(1, round((y2 - y1) * k)))
    rect.topleft = (round(c + (x - w / 2) * k), round(c + y1 * k))
    pygame.draw.rect(surf, color, rect, border_radius=rect.width // 2)


def _rect(surf, color, k, c, x, y, w, h):
    pygame.draw.rect(surf, color, (round(c + x * k), round(c + y * k),
                                   max(1, round(w * k)), max(1, round(h * k))))


def _ellipse(surf, color, k, c, x, y, rx, ry):
    pygame.draw.ellipse(surf, color, (round(c + (x - rx) * k), round(c + (y - ry) * k),
                                      round(2 * rx * k), round(2 * ry * k)))


def _engines(surf, color, k, c, x, y, w, h):
    for sx in (x, -x):
        _capsule(surf, color, k, c, sx, y, y + h, w)


def _jet(len, body, span, wingY, sweep, root, tip, tailSpan, tailY, eng=(), rear=False, tTail=False):
    def draw(surf, color, k, c):
        _capsule(surf, color, k, c, 0, -len / 2, len / 2, body)
        _mirrored(surf, color, k, c, [(0, wingY), (span, wingY + sweep),
                                      (span, wingY + sweep + tip), (0, wingY + root)])
        ty = tailY + 1 if tTail else tailY
        _mirrored(surf, color, k, c, [(0, ty), (tailSpan, ty + 5), (tailSpan, ty + 8), (0, ty + 7)])
        for x, y, w, h in eng:
            _engines(surf, color, k, c, x, y, w, h)
        if rear:
            _engines(surf, color, k, c, body / 2 + 2, len / 2 - 20, 4.5, 9)
    return draw


def _straight_wing(len, body, span, wingY, chord, tailSpan, nacelles=(), noseProp=False):
    def draw(surf, color, k, c):
        _capsule(surf, color, k, c, 0, -len / 2, len / 2, body)
        _mirrored(surf, color, k, c, [(0, wingY), (span, wingY + 1),
                                      (span, wingY + chord - 1), (0, wingY + chord)])
        _mirrored(surf, color, k, c, [(0, len / 2 - 8), (tailSpan, len / 2 - 6),
                                      (tailSpan, len / 2 - 3), (0, len / 2 - 2)])
        for x in nacelles:
            _engines(surf, color, k, c, x, wingY - 6, 3.5, chord + 8)
            _rect(surf, color, k, c, x - 6, wingY - 7.5, 12, 1.6)
            _rect(surf, color, k, c, -x - 6, wingY - 7.5, 12, 1.6)
        if noseProp:
            _rect(surf, color, k, c, -6, -len / 2 - 1.5, 12, 1.6)
    return draw


def _heli(surf, color, k, c):
    pygame.draw.circle(surf, color, (c, c - 4 * k), 22 * k, max(1, round(2.2 * k)))
    _ellipse(surf, color, k, c, 0, -4, 6, 11)
    _rect(surf, color, k, c, -1.3, 4, 2.6, 22)
    _rect(surf, color, k, c, -6, 24, 12, 2.2)


def _fighter(surf, color, k, c):
    _mirrored(surf, color, k, c, [(0, -30), (2.5, -18), (4, -6), (20, 12), (20, 16), (5, 16),
                                  (9, 26), (9, 28), (0, 26)])


def _glider(surf, color, k, c):
    _capsule(surf, color, k, c, 0, -22, 22, 4)
    _mirrored(surf, color, k, c, [(0, -6), (31, -5), (31, -2.5), (0, -2)])
    _mirrored(surf, color, k, c, [(0, 17), (8, 18), (8, 20), (0, 20.5)])


def _uav(surf, color, k, c):
    _capsule(surf, color, k, c, 0, -16, 18, 4.5)
    _mirrored(surf, color, k, c, [(0, -3), (28, -1), (28, 2), (0, 3)])
    _mirrored(surf, color, k, c, [(0, 14), (7, 18), (7, 20), (0, 19)])


SHAPES = {
    "narrow": _jet(52, 6, 25, -5, 12, 13, 3.5, 10, 17, eng=[(9.5, -2, 3.8, 8)]),
    "heavy2": _jet(58, 7.5, 29, -7, 14, 15, 4, 12, 19, eng=[(11, -4, 4.8, 10)]),
    "heavy4": _jet(60, 8, 30, -8, 15, 16, 4, 12, 20, eng=[(10, -5, 4.2, 9), (19, 0, 4, 8)]),
    "regional": _jet(50, 5.5, 21, -2, 7, 10, 3, 9, 18, rear=True, tTail=True),
    "bizjet": _jet(46, 5, 19, 0, 7, 9, 2.5, 8, 15, rear=True, tTail=True),
    "turboprop": _straight_wing(50, 5.5, 26, -4, 6, 9, nacelles=[8.5]),
    "turboprop4": _straight_wing(54, 7, 29, -6, 7, 11, nacelles=[9, 18]),
    "light": _straight_wing(40, 5, 24, -9, 6, 8, noseProp=True),
    "twin": _straight_wing(42, 5, 24, -7, 6, 8, nacelles=[8]),
    "heli": _heli,
    "fighter": _fighter,
    "glider": _glider,
    "uav": _uav,
    "balloon": lambda surf, color, k, c: _ellipse(surf, color, k, c, 0, 0, 16, 16),
    "ground": lambda surf, color, k, c: _rect(surf, color, k, c, -8, -12, 16, 24),
}

# Relative on-screen size of each shape.
SHAPE_SCALE = {
    "heavy4": 1.0, "heavy2": 0.92, "narrow": 0.78, "regional": 0.7, "bizjet": 0.62,
    "turboprop": 0.7, "turboprop4": 0.85, "light": 0.58, "twin": 0.62, "heli": 0.62,
    "fighter": 0.7, "glider": 0.62, "uav": 0.55, "balloon": 0.5, "ground": 0.42,
}

# ---------------------------------------------------------------- type -> shape

_TYPES = {
    "heavy4": "A388 A380 A342 A343 A345 A346 B741 B742 B743 B744 B748 B74S B74R BLCF IL96 IL76 A124 A225 C17 C5 C5M K35R K35E B703 E3TF E3CF B52 E6 VC25",
    "heavy2": "A306 A30B A310 A332 A333 A337 A338 A339 A359 A35K B762 B763 B764 B772 B773 B77L B77W B778 B779 B788 B789 B78X MD11 DC10 L101 KC10 K46 KC46 A3ST",
    "narrow": "A318 A319 A320 A321 A19N A20N A21N B731 B732 B733 B734 B735 B736 B737 B738 B739 B37M B38M B39M B3XM B752 B753 B712 B721 B722 MD81 MD82 MD83 MD87 MD88 MD90 E170 E75L E75S E190 E195 E290 E295 BCS1 BCS3 C919 A148 SU95 P8 E737",
    "regional": "CRJ1 CRJ2 CRJ7 CRJ9 CRJX E135 E145 E35L F70 F100 DC93 DC95 B461 B462 B463 RJ70 RJ85 RJ1H",
    "bizjet": "GLF4 GLF5 GLF6 GA5C GA6C GA7C G280 G150 GALX GLEX GL5T GL6T GL7T C25A C25B C25C C25M C500 C501 C510 C525 C550 C560 C56X C650 C680 C68A C700 C750 CL30 CL35 CL60 E50P E55P E545 E550 FA7X FA8X F2TH F900 FA10 FA20 FA50 FA6X H25B H25C LJ31 LJ35 LJ40 LJ45 LJ55 LJ60 LJ70 LJ75 PC24 HDJT PRM1 BE40 SF50 EA50 ASTR WW24",
    "turboprop": "AT43 AT44 AT45 AT46 AT72 AT73 AT75 AT76 DH8A DH8B DH8C DH8D B190 B350 BE20 BE30 BE9L BE9T BE99 SF34 SW3 SW4 D328 JS31 JS32 JS41 E110 E120 DHC6 C212 CN35 C295 F50 SB20 L410 AN26 MA60 P180",
    "turboprop4": "C130 C30J A400 P3 L188 DHC7 AN12 E2",
    "twin": "BE55 BE56 BE58 BE60 BE76 PA23 PA27 PA30 PA31 PA34 PA44 P68 DA42 DA62 C310 C320 C335 C340 C402 C404 C414 C421 AC50 AEST",
    "light": "C150 C152 C162 C170 C172 C175 C177 C180 C182 C185 C195 C205 C206 C207 C208 C210 P210 P28A P28B P28R P28T P32R P32T PA18 PA20 PA22 PA24 PA28 PA32 PA46 P46T PA38 SR20 SR22 S22T DA20 DA40 DA50 M20P M20T M20J BE33 BE35 BE36 BE23 BE24 AA1 AA5 RV4 RV6 RV7 RV8 RV9 RV10 RV12 RV14 PC12 PC6 PC7 PC9 PC21 TBM7 TBM8 TBM9 KODI T6 TEX2 C77R C82R CH60 J3 CUB GLAS EVOT DV20 TOBA",
    "heli": "H60 S70 S76 S92 S61 EC20 EC25 EC30 EC35 EC45 EC55 EC75 AS32 AS50 AS55 AS65 B06 B06T B105 B212 B222 B230 B407 B412 B427 B429 B430 B47G B505 R22 R44 R66 A109 A119 A139 A149 A169 A189 H47 CH47 H53 H53S H64 UH1 UH1Y AH1 MD52 MD60 MD90 NH90 H125 H130 H135 H145 H155 H160 H175 H215 H225 EH10 V22 BK17 G2CA",
    "fighter": "F16 F15 F18 F18S FA18 F35 F22 F14 F4 F5 A10 T38 T45 EUFI RFAL TOR HAWK MIG29 SU27 SU30 SU35 J10 F117 B1 B2 GRIF",
    "glider": "GLID ASK21 ASK13 DG1T DISC LS8 ASW2 ARCP DUOD JANU NIMB",
    "balloon": "BALL",
    "uav": "Q9 Q4 RQ4 MQ9 HRON",
}
TYPE_SHAPE = {t: shape for shape, types in _TYPES.items() for t in types.split()}

# ADS-B emitter category, for types we don't know.
CATEGORY_SHAPE = {
    "A1": "light", "A2": "bizjet", "A3": "narrow", "A4": "narrow", "A5": "heavy2", "A6": "fighter",
    "A7": "heli", "B1": "glider", "B2": "balloon", "B4": "light", "B6": "uav",
    "C1": "ground", "C2": "ground", "C3": "ground",
}


def shape_for(type_, category):
    return TYPE_SHAPE.get(type_) or CATEGORY_SHAPE.get(category) or "narrow"


def icon_size(z):
    """The browser's icon-size curve: 0.45 at zoom 3, 0.75 at 7, 1.05 at 11 (linear)."""
    if z <= 7:
        return 0.45 + 0.3 * (max(3.0, z) - 3) / 4
    return 0.75 + 0.3 * (min(11.0, z) - 7) / 4


# ---------------------------------------------------------------- rendering

class Icons:
    def __init__(self, halo_color):
        self.halo = halo_color
        self.upright: dict = {}                 # (shape, k, color) -> 4x surface, nose up
        self.cache: OrderedDict = OrderedDict()  # (shape, k, color, step) -> final sprite

    def get(self, shape, px_per_unit, color, track):
        """The icon for `shape` at `px_per_unit` screen pixels per shape unit, heading `track`."""
        k = round(px_per_unit * 32) / 32          # quantise sizes so zooming reuses icons
        step = round((track or 0) / HEADING_STEP) % (360 // HEADING_STEP)
        key = (shape, k, color, step)
        sprite = self.cache.get(key)
        if sprite is not None:
            self.cache.move_to_end(key)
            return sprite
        big = self._upright(shape, k, color)
        rotated = pygame.transform.rotozoom(big, -step * HEADING_STEP, 1.0)
        w, h = rotated.get_size()
        sprite = pygame.transform.smoothscale(rotated, (max(1, w // SS), max(1, h // SS)))
        self.cache[key] = sprite
        if len(self.cache) > CACHE:
            self.cache.popitem(last=False)
        return sprite

    def _upright(self, shape, k, color):
        key = (shape, k, color)
        surf = self.upright.get(key)
        if surf is not None:
            return surf
        kk = k * SS                                       # supersampled px per unit
        size = math.ceil(BOX * kk + 2 * HALO_PX * SS) | 1  # odd, so (0, 0) is a pixel centre
        c = size // 2
        mask = pygame.Surface((size, size), pygame.SRCALPHA)
        SHAPES[shape](mask, (255, 255, 255, 255), kk, c)

        out = pygame.Surface((size, size), pygame.SRCALPHA)
        halo = mask.copy()
        halo.fill((*self.halo, 255), special_flags=pygame.BLEND_RGBA_MIN)
        r = HALO_PX * SS
        for i in range(16):                               # dilate: the halo around the shape
            a = i * math.pi / 8
            out.blit(halo, (round(r * math.cos(a)), round(r * math.sin(a))))
        body = mask.copy()
        body.fill((*color, 255), special_flags=pygame.BLEND_RGBA_MIN)
        out.blit(body, (0, 0))
        if len(self.upright) > 400:
            self.upright.clear()
        self.upright[key] = out
        return out
