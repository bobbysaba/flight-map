#!/usr/bin/env bash
# Copy the app to the Pi and (optionally) install or restart it there.
#   ./deploy.sh              # sync code only
#   ./deploy.sh install      # sync + full install (first time, or after changing requirements)
#   ./deploy.sh install --server-only   # same, but only the service (no screen/kiosk)
#   ./deploy.sh install --native        # same, with the pygame display instead of Chromium
#   ./deploy.sh restart      # sync + restart the service and reload the kiosk page
set -euo pipefail
HOST="${PI_HOST:-flightmap}"
cd "$(dirname "$0")"

rsync -a --delete --exclude __pycache__ --exclude .DS_Store \
  server web native pi requirements.txt config.example.toml "$HOST":/tmp/flight-map-src/
ssh -t "$HOST" 'sudo mkdir -p /opt/flight-map/app \
  && sudo rsync -a --delete --chown=root:root /tmp/flight-map-src/ /opt/flight-map/app/'

case "${1:-}" in
  install)
    ssh -t "$HOST" "sudo bash /opt/flight-map/app/pi/install.sh \"\$USER\" ${2:-}" ;;
  restart)
    # The kiosk loop relaunches Chromium, which picks up new web files.
    ssh -t "$HOST" 'sudo systemctl restart flightmap && sudo systemctl try-restart flightmap-native \
      && (pkill -f flightmap-kiosk || true)' ;;
  "") ;;
  *) echo "unknown command: $1" >&2; exit 1 ;;
esac
