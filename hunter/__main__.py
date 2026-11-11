import argparse
import logging
import random
import time

from .config import load_config
from .scan import run_scan


def main():
    p = argparse.ArgumentParser(prog="hunter", description="סוכן חיפוש דירות")
    p.add_argument("command", choices=["scan", "loop", "fb-login", "fb-groups"],
                   help="scan = ריצה אחת · loop = כל X דקות · fb-login = התחברות לפייסבוק · "
                        "fb-groups = רשימת הקבוצות שלך לקובץ")
    p.add_argument("--config", default=None)
    p.add_argument("--every", type=int, default=None, help="דקות בין ריצות (ל-loop)")
    p.add_argument("--only", choices=["yad2", "facebook"], default=None, help="להריץ מקור אחד בלבד")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--show-browser", action="store_true", help="להציג את חלון הדפדפן של פייסבוק")
    p.add_argument("--debug", action="store_true", help="לשמור צילומי מסך/HTML של פייסבוק ב-data/fb_debug")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)
    if args.show_browser:
        cfg.setdefault("facebook", {})["headless"] = False

    if args.command in ("fb-login", "fb-groups"):
        from .facebook import FacebookSource, write_groups_file

        with FacebookSource(cfg, headless=False) as fb:
            if args.command == "fb-login":
                fb.login_interactively()
            else:
                groups = fb.list_joined_groups()
                path = write_groups_file(groups, cfg)
                enabled = sum(1 for _ in open(path, encoding="utf-8") if "enabled: true" in _)
                print(f"נמצאו {len(groups)} קבוצות, {enabled} סומנו כקשורות לדירות. עבור על הקובץ: {path}")
        return

    if args.command == "scan":
        run_scan(cfg, notify=not args.no_notify, only=args.only, debug=args.debug)
        return
    every = args.every or cfg["run"].get("every_minutes", 60)
    while True:
        try:
            run_scan(cfg, notify=not args.no_notify, only=args.only, debug=args.debug)
        except Exception:
            logging.exception("scan failed")
        # +-10% jitter so requests don't land at the exact same minute every hour
        time.sleep(every * 60 * random.uniform(0.9, 1.1))


if __name__ == "__main__":
    main()
