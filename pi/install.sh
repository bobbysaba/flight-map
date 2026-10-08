#!/usr/bin/env bash
# Install or update flight-map on the Pi. Run as root from /opt/flight-map/app:
#   sudo bash pi/install.sh <desktop-user>
#   sudo bash pi/install.sh <desktop-user> --server-only   # the service, no screen/kiosk
#   sudo bash pi/install.sh <desktop-user> --native        # pygame display instead of Chromium
set -euo pipefail

APP=/opt/flight-map/app
VENV=/opt/flight-map/venv
ETC=/etc/flight-map
STATE=/var/lib/flight-map
DESKTOP_USER="${1:-${SUDO_USER:-}}"

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }
[ -n "$DESKTOP_USER" ] || { echo "usage: sudo bash pi/install.sh <desktop-user>"; exit 1; }
DESKTOP_HOME=$(getent passwd "$DESKTOP_USER" | cut -d: -f6)

# Pi OS with desktop runs the kiosk inside the desktop session (labwc); Pi OS Lite
# has no desktop, so the kiosk gets its own compositor (cage) on tty1 instead.
# --server-only skips the screen entirely (e.g. to measure the service on its own).
# --native draws the map with pygame instead of Chromium (for small boards).
if [ "${2:-}" = --server-only ]; then MODE=server
elif [ "${2:-}" = --native ]; then MODE=native
elif command -v labwc >/dev/null; then MODE=desktop; else MODE=lite; fi

echo "==> packages ($MODE)"
apt-get update -qq
apt-get install -y -qq python3-venv curl
if [ "$MODE" = native ]; then
  apt-get install -y -qq python3-pygame python3-websockets
elif [ "$MODE" != server ]; then
  command -v chromium >/dev/null || command -v chromium-browser >/dev/null \
    || apt-get install -y -qq chromium || apt-get install -y -qq chromium-browser
fi
if [ "$MODE" = lite ]; then
  # Lite ships without fonts; the status card and buttons need one.
  apt-get install -y -qq --no-install-recommends cage fonts-noto-core
fi

echo "==> service user and directories"
id flightmapd >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin flightmapd
install -d -o flightmapd -g flightmapd -m 750 "$STATE"
install -d -m 755 "$ETC"
if [ ! -f "$ETC/config.toml" ]; then
  cp "$APP/config.example.toml" "$ETC/config.toml"
  echo "    created $ETC/config.toml (edit it: start location, photo contact, AeroAPI key)"
fi
chown root:flightmapd "$ETC/config.toml"
chmod 640 "$ETC/config.toml"

echo "==> python environment"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$APP/requirements.txt"

echo "==> service"
install -m 644 "$APP/pi/flightmap.service" /etc/systemd/system/flightmap.service
systemctl daemon-reload
systemctl enable flightmap.service
systemctl restart flightmap.service

if [ "$MODE" = server ]; then
  echo "==> no kiosk (--server-only)"
elif [ "$MODE" = native ]; then
  echo "==> native display for $DESKTOP_USER"
  sed "s/@USER@/$DESKTOP_USER/" "$APP/pi/flightmap-native.service" > /etc/systemd/system/flightmap-native.service
  systemctl daemon-reload
  systemctl enable flightmap-native.service
  systemctl disable flightmap-kiosk.service 2>/dev/null || true
  if command -v raspi-config >/dev/null; then
    raspi-config nonint do_boot_behaviour B1   # console, no autologin
    raspi-config nonint do_blanking 1          # never blank the screen
  fi
else
  echo "==> kiosk for $DESKTOP_USER ($MODE)"
  chmod 755 "$APP/pi/kiosk.sh"
  if [ "$MODE" = lite ]; then
    # A cursor theme whose every pointer is one transparent pixel (Xcursor format).
    CURSORS=/usr/share/flightmap/cursors/default/cursors
    install -d "$CURSORS"
    python3 - "$CURSORS/default" <<'PY'
import struct, sys
size = 24
image = struct.pack("<9I", 36, 0xFFFD0002, size, 1, 1, 1, 0, 0, 0) + struct.pack("<I", 0)
header = struct.pack("<4sIII", b"Xcur", 16, 0x10000, 1) + struct.pack("<III", 0xFFFD0002, size, 28)
open(sys.argv[1], "wb").write(header + image)
PY
    for name in left_ptr arrow pointer hand2 text xterm; do ln -sf default "$CURSORS/$name"; done

    sed "s/@USER@/$DESKTOP_USER/" "$APP/pi/flightmap-kiosk.service" > /etc/systemd/system/flightmap-kiosk.service
    systemctl daemon-reload
    systemctl enable flightmap-kiosk.service
    command -v raspi-config >/dev/null && raspi-config nonint do_boot_behaviour B1   # console, no autologin
  else
    # This replaces the desktop session's autostart, so the Pi boots to the map only
    # (no taskbar). Delete ~/.config/labwc/autostart to get the normal desktop back.
    install -d -o "$DESKTOP_USER" -g "$DESKTOP_USER" "$DESKTOP_HOME/.config/labwc"
    install -o "$DESKTOP_USER" -g "$DESKTOP_USER" -m 644 "$APP/pi/labwc-autostart" \
      "$DESKTOP_HOME/.config/labwc/autostart"
    # Pi OS maps touch screens with mouse emulation on, which turns every touch into a
    # single mouse pointer: no pinch-zoom and no swipe-to-scroll. Pass real touch through.
    RC="$DESKTOP_HOME/.config/labwc/rc.xml"
    if [ -f "$RC" ] && grep -q 'mouseEmulation="yes"' "$RC"; then
      sed -i 's/mouseEmulation="yes"/mouseEmulation="no"/' "$RC"
      echo "    turned off touch mouse emulation in $RC"
    fi
    command -v raspi-config >/dev/null && raspi-config nonint do_boot_behaviour B4   # desktop, autologin
  fi
  command -v raspi-config >/dev/null && raspi-config nonint do_blanking 1            # never blank the screen
fi

echo "==> done"
systemctl --no-pager --lines=5 status flightmap.service || true
echo
echo "Reboot to start the kiosk: sudo reboot"
