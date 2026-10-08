"""The connection to the flight map service: the same WebSocket the browser uses.

Runs on its own thread. Incoming messages land in `inbox`; `set_area` tells the
service which circle to watch (resent after every reconnect).
"""

import json
import logging
import queue
import threading
import time
import urllib.request

from websockets.sync.client import connect

log = logging.getLogger("flightmap.native")


class Link:
    def __init__(self, server: str):
        self.server = server.rstrip("/")
        self.ws_url = "ws" + self.server[len("http"):] + "/ws"
        self.inbox: queue.Queue = queue.Queue()
        self._outbox: queue.Queue = queue.Queue()
        self._area = None
        self.connected = False

    def config(self) -> dict:
        """/api/config, waiting for the service if it's still starting."""
        while True:
            try:
                with urllib.request.urlopen(self.server + "/api/config", timeout=5) as r:
                    return json.load(r)
            except OSError as e:
                log.info("waiting for the service (%s)", e)
                time.sleep(1)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def set_area(self, lat, lon, radius_nm):
        self._area = {"type": "area", "lat": lat, "lon": lon, "radius_nm": radius_nm}
        self._outbox.put(self._area)

    def _run(self):
        while True:
            try:
                with connect(self.ws_url, open_timeout=5, max_size=16 * 2**20) as ws:
                    self.connected = True
                    if self._area:
                        ws.send(json.dumps(self._area))
                    while True:
                        while not self._outbox.empty():
                            ws.send(json.dumps(self._outbox.get_nowait()))
                        try:
                            msg = ws.recv(timeout=0.2)
                        except TimeoutError:
                            continue
                        self.inbox.put(json.loads(msg))
            except Exception as e:
                if self.connected:
                    log.warning("lost connection to the service: %s", e)
                self.connected = False
                self.inbox.put({"type": "status", "ok": False,
                                "error": "Lost connection to the flight map service"})
                time.sleep(2)
