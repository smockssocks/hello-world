"""Entry point: ``python -m ds5forge`` (window) or ``python -m ds5forge --no-gui``."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from . import APP_NAME, __version__


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ds5forge", description="%s - DualSense haptics and adaptive triggers" % APP_NAME)
    parser.add_argument("--no-gui", action="store_true", help="run in the console without a window")
    parser.add_argument("--list", action="store_true", help="list connected DualSense controllers and exit")
    parser.add_argument("--profile", help="profile to use when no known game is running")
    parser.add_argument("--verbose", action="store_true", help="debug logging")
    parser.add_argument("--version", action="version", version="%s %s" % (APP_NAME, __version__))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")

    if args.list:
        from .controller import find_controllers, guess_transport
        found = find_controllers()
        if not found:
            print("No DualSense found.")
        for info in found:
            print("%s  [%s]  %s" % (info.get("product_string") or "DualSense", guess_transport(info),
                                    info["path"].decode(errors="replace") if isinstance(info["path"], bytes) else info["path"]))
        return 0

    from .engine import Engine
    engine = Engine()
    if args.profile:
        engine.settings.profile = args.profile
    engine.start()

    if args.no_gui:
        print("%s running. Press Ctrl+C to quit." % APP_NAME)
        stop = []
        signal.signal(signal.SIGINT, lambda *a: stop.append(1))
        try:
            while not stop:
                time.sleep(0.2)
        finally:
            engine.stop()
        return 0

    from .gui import run_gui
    run_gui(engine)
    return 0


if __name__ == "__main__":
    sys.exit(main())
