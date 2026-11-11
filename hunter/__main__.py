import argparse
import logging
import random
import time

from .config import load_config
from .scan import run_scan


def main():
    p = argparse.ArgumentParser(prog="hunter", description="סוכן חיפוש דירות")
    p.add_argument("command", choices=["scan", "loop"], help="scan = ריצה אחת, loop = כל X דקות")
    p.add_argument("--config", default=None)
    p.add_argument("--every", type=int, default=None, help="דקות בין ריצות (ל-loop)")
    p.add_argument("--no-notify", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)

    if args.command == "scan":
        run_scan(cfg, notify=not args.no_notify)
        return
    every = args.every or cfg["run"].get("every_minutes", 60)
    while True:
        try:
            run_scan(cfg, notify=not args.no_notify)
        except Exception:
            logging.exception("scan failed")
        # +-10% jitter so requests don't land at the exact same minute every hour
        time.sleep(every * 60 * random.uniform(0.9, 1.1))


if __name__ == "__main__":
    main()
