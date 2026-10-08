"""Run the flight map service: python -m flightmap [--config path]."""

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from . import config
from .app import create_app


def main():
    parser = argparse.ArgumentParser(prog="flightmap")
    parser.add_argument("--config", type=Path, default=config.DEFAULT_PATH)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per poll is too chatty
    try:
        cfg = config.load(args.config)
    except config.ConfigError as e:
        sys.exit(f"config error: {e}")
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")


if __name__ == "__main__":
    main()
