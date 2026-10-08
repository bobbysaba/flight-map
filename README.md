# flight-map

A personal Flightradar24-style map on a Raspberry Pi 5 with a 7" touch screen
(Waveshare 1024×600, HDMI + USB touch). Fully pannable, live aircraft with
type-specific icons, and a status card with route, photo and (on demand)
flight times. Runs entirely on free data.

## Architecture

```
Chromium (kiosk) ──WebSocket──> flightmap service (Python, FastAPI)
  MapLibre map      snapshots      ├─ live poller ──> airplanes.live → adsb.fi → adsb.lol (failover)
  status card   ──HTTP──────────>  ├─ card details ──> adsbdb, adsb.lol route data, planespotters
                                   ├─ flight times ──> FlightAware AeroAPI (button only, capped)
                                   └─ SQLite cache + AeroAPI spending log
```

- **Live area.** The screen tells the service which circle to watch (map centre,
  radius out to the far corner, capped at the APIs' 250 nm). The service refreshes it
  every `poll_interval_s` while a screen is connected, and the browser glides each
  plane along its track between refreshes. Pan away and **Search here** appears; the
  old area stays live until you tap it.
- **Status card.** Tapping a plane shows only free data: aircraft, airline, route,
  photo, trail and live values. **Get flight times** is the only thing that calls
  AeroAPI. Results are cached until the flight lands.
- **Spending cap.** Every AeroAPI call is logged; hourly, the total is replaced by
  FlightAware's own usage figure. Calls stop at `stop_at` (default $9.50 of the
  $10/month feeder allowance) and the card says so until the 1st.
- **Map.** A custom minimal dark style on [OpenFreeMap](https://openfreemap.org)
  vector tiles (free, no key).

## First-time setup

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager: hostname
   `flightmap`, your user, home Wi-Fi, SSH on. Lite is recommended: it runs the
   map in [cage](https://github.com/cage-kiosk/cage) with no desktop session, which
   saves about 200 MB of RAM. The desktop image also works (see below).
2. On your Mac, add to `~/.ssh/config`:
   ```
   Host flightmap
       HostName flightmap.local
       User <your-pi-user>
   ```
   Optional but saves typing the password on every deploy: `ssh-copy-id flightmap`.
3. Install:
   ```sh
   ./deploy.sh install
   ```
4. Edit the config on the Pi, then restart:
   ```sh
   ssh flightmap
   sudo nano /etc/flight-map/config.toml   # start location, photo contact, AeroAPI key
   sudo systemctl restart flightmap
   sudo reboot                             # boots straight into the map
   ```

## Day to day

```sh
./deploy.sh restart      # after code changes: sync, restart the service, reload the kiosk
./deploy.sh install      # after changing requirements.txt or pi/ files
ssh flightmap journalctl -u flightmap -f   # service logs
```

On Pi OS Lite the screen is run by `flightmap-kiosk.service`
(`ssh flightmap journalctl -u flightmap-kiosk -f` for its logs). To get a text console
back on the screen: `sudo systemctl disable --now flightmap-kiosk`.

On the desktop image the map starts from the desktop session instead. To get the normal
desktop back: delete `~/.config/labwc/autostart` and reboot.

## Config (`/etc/flight-map/config.toml`)

| Setting | What it does |
|---|---|
| `map.start_lat/lon/zoom` | Where the map opens, and where ⌂ returns to |
| `live.poll_interval_s` | Refresh interval for the live area (min 2 s) |
| `live.providers` | Order of the free position APIs to try |
| `photos.contact` | planespotters.net requires a contact URL or email in each request. Blank turns photos off |
| `aeroapi.api_key` | AeroAPI Personal key. Blank hides "Get flight times" |
| `aeroapi.stop_at` | Monthly spending cap in USD |
| `aeroapi.cost_per_call` | Estimate used between syncs with FlightAware's usage figure |

### Getting an AeroAPI key

Sign in to FlightAware with your feeder account → **AeroAPI** → sign up for the
**Personal** tier → create an API key. Feeders get $10/month of free usage.
A flight lookup costs $0.005 on the Personal tier (Oct 2026), so the $9.50 cap is
about 1,900 lookups a month. If the price changes, update `cost_per_call`.

## Office Wi-Fi (WPA2-Enterprise / university login)

Imager can't set this up, so add it over SSH once (at home is fine). The Pi will join
whichever saved network is in range. Most universities use PEAP + MSCHAPv2; check
your IT page (or the eduroam CAT tool) for the exact settings and the server domain.

```sh
sudo nmcli connection add type wifi con-name office ifname wlan0 ssid "<SSID>" \
  wifi-sec.key-mgmt wpa-eap \
  802-1x.eap peap 802-1x.phase2-auth mschapv2 \
  802-1x.identity "<username>" 802-1x.password "<password>" \
  802-1x.domain-suffix-match "<university domain>" \
  802-1x.ca-cert /etc/ssl/certs/ca-certificates.crt
```

## Running on your Mac (development)

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.toml config.toml   # set port, db_path to somewhere writable, start location
cd server && ../.venv/bin/python -m flightmap --config ../config.toml
```

Then open `http://127.0.0.1:8080`. Add `?kiosk` to the URL to hide the cursor like on the Pi.
`window.flightmap` in the browser console exposes the map, aircraft list and card.

## Data sources and credits

- Positions: [airplanes.live](https://airplanes.live), [adsb.fi](https://adsb.fi), [adsb.lol](https://adsb.lol) (ODbL)
- Routes: adsb.lol / VRS standing data, [adsbdb](https://www.adsbdb.com)
- Aircraft and airlines: [adsbdb](https://www.adsbdb.com)
- Photos: [planespotters.net](https://www.planespotters.net) (photographer credit shown on the card)
- Flight times: [FlightAware AeroAPI](https://flightaware.com/commercial/aeroapi/)
- Map: [OpenFreeMap](https://openfreemap.org), © OpenMapTiles, © OpenStreetMap contributors
- Map library: [MapLibre GL JS](https://maplibre.org) (BSD-3-Clause, vendored in `web/vendor`)
