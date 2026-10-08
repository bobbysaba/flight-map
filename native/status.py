"""The status panel: are the data sources up, and how much AeroAPI allowance is left.

A port of web/src/status.js, opened by tapping the status pill. Every check it
runs is free. Requests run on background threads; `poll()` takes in results.
"""

import json
import logging
import queue
import threading
import time
import urllib.request
from datetime import date

import pygame

import ui
from card import fmt_ago

log = logging.getLogger("flightmap.native")

HEAD_H = 58
PAD = 18


class StatusPanel:
    def __init__(self, server: str, size):
        self.server = server.rstrip("/")
        self.w, self.h = size
        self.results: queue.Queue = queue.Queue()
        self.is_open = False
        self.data = None
        self.checks = None
        self.checking = False
        self.sheet = None
        self.rect = pygame.Rect(0, 0, 0, 0)
        self.btn_close = self.btn_check = self.btn_check_local = pygame.Rect(0, 0, 0, 0)
        self.shade = pygame.Surface(size, pygame.SRCALPHA)
        self.shade.fill((5, 7, 10, 153))

    def open(self):
        self.is_open = True
        self._spawn("status", lambda: self._request("/api/status"))
        self.run_checks()

    def close(self):
        self.is_open = False

    def run_checks(self):
        if self.checking:
            return
        self.checking = True
        self._spawn("checks", lambda: self._request("/api/status/check", post=True))
        self.render()

    def _request(self, path, post=False):
        req = urllib.request.Request(self.server + path, data=b"" if post else None,
                                     method="POST" if post else "GET")
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)

    def _spawn(self, kind, fn):
        def run():
            try:
                result = fn()
            except Exception as e:
                log.warning("status %s failed: %s", kind, e)
                result = None
            self.results.put((kind, result))
        threading.Thread(target=run, daemon=True).start()

    def poll(self) -> bool:
        changed = False
        while not self.results.empty():
            kind, result = self.results.get_nowait()
            changed = True
            if kind == "status":
                if result and not self.checks:    # a finished check is fresher
                    self.data = result
            else:
                self.checking = False
                if result is None:
                    self.checks = [{"name": "Flight map service", "group": "Live positions",
                                    "ok": False, "detail": "Not reachable"}]
                else:
                    self.checks = result.get("checks")
                    self.data = {"providers": result.get("providers"), "budget": result.get("budget")}
        if changed:
            self.render()
        return changed

    def tap(self, x, y):
        """Handle a tap anywhere on screen while the panel is open."""
        if not self.rect.collidepoint(x, y) or self.btn_close.inflate(16, 16).collidepoint(x, y):
            self.close()
        elif self.btn_check.collidepoint(x, y):
            self.run_checks()

    # ------------------------------------------------------------ drawing

    def draw(self, screen):
        if not self.is_open:
            return
        if self.sheet is None:
            self.render()
        screen.blit(self.shade, (0, 0))
        screen.blit(self.sheet, self.rect)

    def render(self):
        # Lay out once to find the height, then draw for real on a rounded sheet.
        w = min(920, self.w - 32)
        height = self._paint(pygame.Surface((w, self.h - 32)), w)
        self.sheet = pygame.Surface((w, height), pygame.SRCALPHA)
        pygame.draw.rect(self.sheet, ui.PANEL, self.sheet.get_rect(), border_radius=14)
        self._paint(self.sheet, w)
        pygame.draw.rect(self.sheet, ui.LINE, self.sheet.get_rect(), 1, border_radius=14)
        self.rect = self.sheet.get_rect(center=(self.w // 2, self.h // 2))
        self.btn_close = pygame.Rect(self.rect.x + w - 50, self.rect.y + 9, 40, 40)
        self.btn_check = self.btn_check_local.move(self.rect.topleft)

    def _paint(self, sheet, w):
        """Draw the sheet's contents; returns the height they need."""
        sheet.blit(ui.text("Status", 22, ui.TEXT, True), (PAD, 14))
        ui.close_icon(sheet, (w - 30, HEAD_H // 2))
        pygame.draw.line(sheet, ui.LINE, (0, HEAD_H), (w, HEAD_H))

        left_w = round((w - 1) * 1.2 / 2.2)
        pygame.draw.line(sheet, ui.LINE, (left_w, HEAD_H), (left_w, sheet.get_height() - 2))
        y = self._sources(sheet, PAD, HEAD_H + 6, left_w - 2 * PAD)
        btn = pygame.Rect(PAD, y + 14, left_w - 2 * PAD, 46)
        pygame.draw.rect(sheet, ui.ACCENT, btn, border_radius=10)
        label = ui.text("Checking…" if self.checking else "Run checks again", 14, (255, 255, 255), True)
        if self.checking:
            label.set_alpha(160)
        sheet.blit(label, label.get_rect(center=btn.center))
        self.btn_check_local = btn
        bottom = btn.bottom + PAD
        bottom = max(bottom, self._budget(sheet, left_w + 1 + PAD, HEAD_H + 6, w - left_w - 1 - 2 * PAD))
        return min(self.h - 32, max(bottom, 200))

    def _head(self, sheet, x, y, text):
        sheet.blit(ui.text(text.upper(), 11, ui.MUTED), (x, y + 14))
        return y + 34

    def _row(self, sheet, x, y, width, name, ok, detail, tag=None):
        color = ui.OK if ok is True else ui.BAD if ok is False else (74, 84, 98)
        pygame.draw.circle(sheet, color, (x + 5, y + 10), 5)
        name_s = ui.text(name, 14, ui.TEXT, True)
        sheet.blit(name_s, (x + 20, y + 1))
        if tag:
            t = ui.text(tag, 11, ui.ACCENT, True)
            box = pygame.Rect(x + 28 + name_s.get_width(), y + 2, t.get_width() + 14, 18)
            pygame.draw.rect(sheet, (31, 50, 75), box, border_radius=9)
            sheet.blit(t, t.get_rect(center=box.center))
        lines = ui.wrap(detail or "", 12, width - 20)
        for i, line in enumerate(lines):
            sheet.blit(ui.text(line, 12, ui.MUTED), (x + 20, y + 21 + 16 * i))
        return y + 26 + 16 * max(1, len(lines)) + 4

    def _sources(self, sheet, x, y, width):
        providers = (self.data or {}).get("providers") or []
        checks = self.checks or []
        by_name = {c["name"]: c for c in checks}
        now = time.time()

        y = self._head(sheet, x, y, "Live positions")
        if not providers:
            sheet.blit(ui.text("Loading…", 12, ui.MUTED), (x, y))
            y += 22
        for p in providers:
            c = by_name.get(p["name"])
            if c:
                ok = c["ok"]
            else:
                ok = (False if (p.get("last_error_at") or 0) > (p.get("last_ok_at") or 0)
                      else True if p.get("last_ok_at") else None)
            if c and c["ok"]:
                detail = f"{c.get('ms')} ms · {c['detail']}"
            elif c:
                detail = c["detail"]
            elif p.get("last_ok_at"):
                detail = f"Last answered {fmt_ago(now - p['last_ok_at'])}"
                if p.get("last_ms"):
                    detail += f" · {p['last_ms']} ms"
            else:
                detail = "Not used yet"
            if p.get("cooling_down_s"):
                detail += f" · resting {p['cooling_down_s']}s after errors"
            if not ok and p.get("last_error") and not c:
                detail = f"{p['last_error']} ({fmt_ago(now - p['last_error_at'])})"
            y = self._row(sheet, x, y, width, p["name"], ok, detail, "In use" if p.get("in_use") else None)

        groups: dict = {}
        for c in checks:
            if c.get("group") != "Live positions":
                groups.setdefault(c["group"], []).append(c)
        for group, rows in groups.items():
            y = self._head(sheet, x, y, group)
            for c in rows:
                detail = f"{c['ms']} ms · {c['detail']}" if c.get("ms") else c.get("detail")
                y = self._row(sheet, x, y, width, c["name"], c.get("ok"), detail)
        if self.checking and not checks:
            sheet.blit(ui.text("Checking card and flight-time sources…", 12, ui.MUTED), (x, y + 8))
            y += 30
        return y

    def _budget(self, sheet, x, y, width):
        b = (self.data or {}).get("budget")
        y = self._head(sheet, x, y, "FlightAware AeroAPI")
        if not b:
            sheet.blit(ui.text("Loading…", 12, ui.MUTED), (x, y))
            return y + 22
        if not b.get("enabled"):
            return self._notice(sheet, x, y, width, "Off: add an AeroAPI key to the config.")

        big = ui.text(f"{b['lookups_left']:,}", 30, ui.TEXT, True)
        sheet.blit(big, (x, y))
        sheet.blit(ui.text(" lookups left", 14, ui.MUTED), (x + big.get_width(), y + 13))
        y += big.get_height() + 6
        pct = min(100, 100 * b["spent"] / b["limit"]) if b.get("limit") else 0
        bar = pygame.Rect(x, y, width, 4)
        pygame.draw.rect(sheet, ui.PANEL_2, bar, border_radius=2)
        pygame.draw.rect(sheet, ui.BAD if b.get("exhausted") else ui.ACCENT,
                         (x, y, round(width * pct / 100), 4), border_radius=2)
        y += 16
        resets = date.fromisoformat(b["resets"]).strftime("%b %-d") if b.get("resets") else "—"
        for k, v in (("Used this month", f"{plural(b['lookups_used'], 'lookup')} · ${b['spent']:.3f}"),
                     ("Monthly cap", f"${b['limit']:.2f} (of $10 free)"),
                     ("Price per lookup", f"${b['cost_per_call']}"),
                     ("Resets", resets)):
            sheet.blit(ui.text(k, 14, ui.MUTED), (x, y))
            val = ui.text(v, 14)
            sheet.blit(val, (x + width - val.get_width(), y))
            y += 25
        if b.get("exhausted"):
            y = self._notice(sheet, x, y + 4, width,
                             "Limit reached. Cards are using the free route sources until it resets.") + 4
        fa = b.get("flightaware_reported")
        note = (f"FlightAware's own count: {plural(fa['calls'], 'lookup')}, ${fa['spent']:.3f} "
                f"(checked {fmt_ago(time.time() - fa['at'])}). It lags a few hours behind, so the "
                "higher of the two counts is used." if fa else "FlightAware's own count hasn't been checked yet.")
        if b.get("sync_error"):
            note += f" Last check failed: {b['sync_error']}."
        for line in ui.wrap(note, 12, width):
            sheet.blit(ui.text(line, 12, ui.MUTED), (x, y + 8))
            y += 16
        return y + 16

    def _notice(self, sheet, x, y, width, message):
        lines = ui.wrap(message, 13, width - 24)
        box = pygame.Rect(x, y, width, 20 + 18 * len(lines))
        pygame.draw.rect(sheet, ui.PANEL_2, box, border_radius=8)
        pygame.draw.rect(sheet, ui.LINE, box, 1, border_radius=8)
        for i, line in enumerate(lines):
            sheet.blit(ui.text(line, 13), (box.x + 12, box.y + 10 + 18 * i))
        return box.bottom


def plural(n, word):
    return f"{n:,} {word}{'' if n == 1 else 's'}"
