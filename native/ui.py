"""Shared look for the native display: colours, fonts, text and small drawing helpers.

Colours match the browser's CSS variables (web/styles.css).
"""

import math
import os

import pygame

BG = (14, 18, 24)
PANEL = (21, 27, 35)
PANEL_2 = (28, 36, 48)
LINE = (39, 49, 64)
TEXT = (227, 232, 239)
MUTED = (133, 146, 163)
ACCENT = (79, 157, 255)
OK = (67, 194, 107)
WARN = (240, 180, 41)
BAD = (255, 77, 94)

# Noto Sans on the Pi (fonts-noto-core), DejaVu if that's missing, pygame's own last.
_FONT_PATHS = {
    False: ["/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/System/Library/Fonts/Supplemental/Arial.ttf"],
    True: ["/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
           "/System/Library/Fonts/Supplemental/Arial Bold.ttf"],
}
_fonts: dict = {}
_text_cache: dict = {}


def font(size, bold=False):
    key = (size, bold)
    f = _fonts.get(key)
    if f is None:
        path = next((p for p in _FONT_PATHS[bold] if os.path.exists(p)), None)
        f = _fonts[key] = pygame.font.Font(path, size)
    return f


def text(s, size, color=TEXT, bold=False):
    """A rendered line of text (cached: the same strings come up every second)."""
    key = (s, size, color, bold)
    surf = _text_cache.get(key)
    if surf is None:
        if len(_text_cache) > 2000:
            _text_cache.clear()
        surf = _text_cache[key] = font(size, bold).render(s, True, color)
    return surf


def fit(s, size, width, bold=False):
    """`s`, cut down with an ellipsis until it fits in `width` pixels."""
    f = font(size, bold)
    if f.size(s)[0] <= width:
        return s
    while s and f.size(s + "…")[0] > width:
        s = s[:-1]
    return s.rstrip() + "…"


def wrap(s, size, width, bold=False):
    f = font(size, bold)
    lines, line = [], ""
    for word in s.split():
        trial = f"{line} {word}".strip()
        if f.size(trial)[0] <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    return lines + ([line] if line else [])


def dashed(surf, color, points, width=1, dash=3, gap=4):
    """A dashed polyline through screen `points`."""
    on, left = True, dash
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        seg = math.hypot(x1 - x0, y1 - y0)
        pos = 0.0
        while pos < seg:
            step = min(left, seg - pos)
            if on:
                a, b = pos / seg, (pos + step) / seg
                pygame.draw.line(surf, color, (x0 + (x1 - x0) * a, y0 + (y1 - y0) * a),
                                 (x0 + (x1 - x0) * b, y0 + (y1 - y0) * b), width)
            pos += step
            left -= step
            if left <= 0:
                on = not on
                left = dash if on else gap


def close_icon(surf, center, color=MUTED, r=7):
    cx, cy = center
    pygame.draw.line(surf, color, (cx - r, cy - r), (cx + r, cy + r), 2)
    pygame.draw.line(surf, color, (cx - r, cy + r), (cx + r, cy - r), 2)


def arrow(surf, color, x0, x1, y):
    pygame.draw.line(surf, color, (x0, y), (x1, y), 2)
    pygame.draw.polygon(surf, color, [(x1 + 2, y), (x1 - 6, y - 5), (x1 - 6, y + 5)])
