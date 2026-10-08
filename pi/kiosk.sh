#!/usr/bin/env bash
# Full-screen Chromium showing the map. Started by the desktop session at login
# (labwc-autostart), or on Pi OS Lite by cage (flightmap-kiosk.service).
# Relaunches Chromium if it ever exits or crashes.
URL="${FLIGHTMAP_URL:-http://127.0.0.1:8080}"
PROFILE="$HOME/.config/flightmap-kiosk"
BROWSER=$(command -v chromium || command -v chromium-browser)

# Wait for the service (it starts in parallel at boot).
until curl -sf "$URL/api/config" >/dev/null; do sleep 1; done
# And for the internet (up to a minute): the map style is fetched once when the page
# loads, so a page that opens before Wi-Fi is up keeps a black map until reloaded.
for _ in $(seq 60); do
  curl -sf -m 5 -o /dev/null https://tiles.openfreemap.org/planet && break
  sleep 1
done

while true; do
  # After a power cut Chromium thinks it crashed and shows "Restore pages?". Tell it otherwise.
  PREFS="$PROFILE/Default/Preferences"
  if [ -f "$PREFS" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS"
  fi

  "$BROWSER" \
    --kiosk "$URL/?kiosk" \
    --ozone-platform=wayland \
    --remote-debugging-port=9222 \
    --user-data-dir="$PROFILE" \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-features=Translate,TranslateUI,OverscrollHistoryNavigation,MediaRouter \
    --overscroll-history-navigation=0 \
    --disable-pinch \
    --password-store=basic \
    --check-for-update-interval=31536000
  sleep 3
done
