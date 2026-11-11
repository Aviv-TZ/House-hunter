from __future__ import annotations

import json
import logging
import random
import time

from .db import DB, now
from .llm import LLMAnalyzer
from .models import Listing
from .notify import Notifier, format_listing
from .rules import analyze, prefilter
from .yad2 import Yad2Blocked, Yad2Client

log = logging.getLogger(__name__)


def run_scan(cfg: dict, notify: bool = True) -> dict:
    db = DB(cfg["db_path"])
    first_run = db.get_meta("bootstrapped") is None
    notifier = Notifier()
    llm = LLMAnalyzer.from_config(cfg)
    run_cfg = cfg["run"]
    client = Yad2Client(delay_seconds=run_cfg.get("delay_seconds", (4, 9)))
    s = cfg["search"]
    started = now()
    stats = {"seen": 0, "new": 0, "details": 0, "notified": 0, "price_drops": 0, "error": None}
    price_drops: dict[str, int] = {}

    try:
        # 1) feed: discover listings
        for listing in client.iter_listings(s["region"], s["cities"], s["min_rooms"], s["max_price"]):
            stats["seen"] += 1
            is_new, old_price = db.upsert_seen(listing)
            if is_new:
                stats["new"] += 1
                reasons = prefilter(listing, cfg)
                if reasons:  # rejected from feed data alone - no need to open it
                    a = analyze(listing, cfg).to_dict()
                    db.save_analysis(listing, a)
            elif old_price and listing.price and listing.price < old_price:
                price_drops[listing.key] = old_price
        db.commit()
        if client.blocked:
            raise Yad2Blocked(client.blocked)

        # 2) details for promising listings (priority neighborhoods first)
        pending = db.pending_details(limit=10_000)
        pending.sort(key=lambda r: (json.loads(r["data"]).get("neighborhood_id") not in cfg["priority_neighborhoods"],))
        for row in pending[: run_cfg.get("max_details_per_run", 60)]:
            listing = Listing.from_dict(json.loads(row["data"]))
            listing = client.fetch_details(listing)
            stats["details"] += 1
            llm_result = llm.analyze(listing) if llm and not prefilter(listing, cfg) else None
            db.save_analysis(listing, analyze(listing, cfg, llm_result).to_dict())
            db.commit()
    except Yad2Blocked as e:
        log.error("Stopped: %s", e)
        stats["error"] = "yad2 חסם את הבקשות (בדיקת בוט). ננסה שוב בריצה הבאה."
    db.commit()

    # 3) notifications
    rows = db.to_notify()
    if not run_cfg.get("notify_check_listings", True):
        rows = [r for r in rows if r["verdict"] == "match"]
    if first_run and not run_cfg.get("notify_on_first_run", False):
        # Until one full scan succeeds, everything found counts as "already existed": no flood.
        for r in db.to_notify():
            db.mark_notified(r["key"], 2)  # 2 = existed before the agent started
        done = stats["error"] is None and not db.pending_details(1)
        if done:
            db.set_meta("bootstrapped", now())
        else:
            log.info("bootstrap in progress: %d listings still waiting for details", len(db.pending_details(10_000)))
        if notify and done:
            m = sum(1 for r in rows if r["verdict"] == "match")
            notifier.send(f"🏠 הסריקה הראשונית הסתיימה: {m} התאמות מלאות, "
                          f"{len(rows) - m} דורשות בירור. מעכשיו אשלח רק מודעות חדשות. הכל בדאשבורד.")
    elif notify:
        for r in rows:
            listing = Listing.from_dict(json.loads(r["data"]))
            a = json.loads(r["analysis"])
            if notifier.send(format_listing(listing, a), photo=listing.images[0] if listing.images else None):
                db.mark_notified(r["key"])
                stats["notified"] += 1
            time.sleep(random.uniform(0.5, 1.5))
        for key, old in price_drops.items():
            r = db.get(key)
            if r and r["verdict"] in ("match", "check") and r["status"] != "not_relevant":
                listing = Listing.from_dict(json.loads(r["data"]))
                listing.price = r["price"]
                notifier.send("📉 ירידת מחיר\n" + format_listing(listing, json.loads(r["analysis"]), old))
                stats["price_drops"] += 1
    if stats["error"] and notify:
        notifier.send("⚠ " + stats["error"])
    db.commit()
    db.log_run(started, stats["seen"], stats["new"], stats["notified"], stats["error"])
    log.info("scan done: %s (yad2 requests: %d)", stats, client.requests_made)
    return stats
