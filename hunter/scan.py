from __future__ import annotations

import json
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Optional

from .db import DB, now
from .dedup import find_duplicate, text_hash
from .llm import LLMAnalyzer, apply_extraction
from .models import Listing
from .notify import Notifier, format_listing
from .rules import analyze, is_priority, prefilter
from .yad2 import Yad2Blocked, Yad2Client

log = logging.getLogger(__name__)

SOURCE_GROUPS = {"yad2": ("yad2",), "facebook": ("fb_group", "fb_market")}


class Pipeline:
    """Shared per-listing processing: dedup -> Claude -> dedup -> rules -> save."""

    def __init__(self, cfg: dict, db: DB, llm: Optional[LLMAnalyzer], stats: dict):
        self.cfg, self.db, self.llm, self.stats = cfg, db, llm, stats
        self.llm_budget = int(cfg["run"].get("max_llm_per_run", 150))

    def process(self, listing: Listing) -> None:
        free_text = listing.source != "yad2"
        # 1) identical text already seen (same post in another group) -> merge, no Claude call
        h = text_hash(listing.description) if free_text else None
        self.db.set_text_hash(listing.key, h)
        if h:
            canonical = self.db.find_by_hash(h, listing.key)
            if canonical:
                self._merge(listing, canonical, "אותו פוסט בדיוק")
                return

        # 2) Claude: extraction for free text, gap-filling for yad2 (skip if already rejected by structure)
        x = None
        if self.llm and self.llm_budget > 0 and (free_text or not prefilter(listing, self.cfg)):
            self.llm_budget -= 1
            x = self.llm.analyze(listing)
            self.stats["llm_calls"] += 1
            listing = apply_extraction(listing, x, self.cfg)
        listing.details_fetched = True

        # 3) same apartment from another source / group?
        if listing.is_rental_offer is not False:
            dup = find_duplicate(listing, self.db.dedup_candidates(listing.key))
            if dup:
                self._merge(listing, *dup, analysis=analyze(listing, self.cfg, x).to_dict())
                return

        self.db.save_analysis(listing, analyze(listing, self.cfg, x).to_dict())
        self.db.commit()

    def _merge(self, listing: Listing, canonical: str, reason: str, analysis: Optional[dict] = None):
        listing.details_fetched = True
        a = analysis or {"verdict": "dup", "score": 0, "reasons": [], "flags": [], "pluses": [], "warnings": []}
        self.db.save_analysis(listing, a)
        self.db.mark_duplicate(listing.key, canonical, reason)
        self.db.commit()
        self.stats["duplicates"] += 1
        log.info("duplicate: %s == %s (%s)", listing.key, canonical, reason)


# ---------------------------------------------------------------- yad2
def refresh_pending(cfg, db) -> None:
    """Re-apply the rules to yad2 listings not opened yet (criteria may have changed)."""
    for row in db.conn.execute("SELECT data FROM listings WHERE details_fetched=0 AND source='yad2' "
                               "AND dup_of IS NULL").fetchall():
        listing = Listing.from_dict(json.loads(row["data"]))
        db.save_analysis(listing, analyze(listing, cfg).to_dict())
    db.commit()


def run_yad2(cfg, db, pipe: Pipeline, stats, price_drops) -> Optional[str]:
    s, run_cfg = cfg["search"], cfg["run"]
    refresh_pending(cfg, db)
    client = Yad2Client(delay_seconds=run_cfg.get("delay_seconds", (4, 9)))
    try:
        for listing in client.iter_listings(s["region"], s["cities"], s["min_rooms"], s["max_price"],
                                            s.get("min_price", 0)):
            stats["seen"] += 1
            is_new, old_price = db.upsert_seen(listing)
            if is_new:
                stats["new"] += 1
                # preliminary verdict from feed data; rejected ones are never opened
                db.save_analysis(listing, analyze(listing, cfg).to_dict())
            elif old_price and listing.price and listing.price < old_price:
                price_drops[listing.key] = old_price
        db.commit()
        if client.blocked:
            raise Yad2Blocked(client.blocked)

        pending = db.pending_details(10_000, sources=SOURCE_GROUPS["yad2"])
        pending.sort(key=lambda r: not is_priority(Listing.from_dict(json.loads(r["data"])), cfg))
        for row in pending[: run_cfg.get("max_details_per_run", 60)]:
            listing = client.fetch_details(Listing.from_dict(json.loads(row["data"])))
            stats["details"] += 1
            pipe.process(listing)
    except Yad2Blocked as e:
        log.error("yad2 stopped: %s", e)
        return "יד 2 חסמה את הבקשות (בדיקת בוט). ננסה שוב בריצה הבאה."
    finally:
        db.commit()
    return None


# ---------------------------------------------------------------- facebook
def facebook_due(cfg, db) -> bool:
    every = (cfg.get("facebook") or {}).get("every_minutes", 120)
    last = db.get_meta("fb_last_run")
    return not last or datetime.fromisoformat(last) <= datetime.now() - timedelta(minutes=every * 0.9)


def pick_groups(groups: list[dict], visits: dict, n: int) -> list[dict]:
    """Rotate: least recently visited groups first."""
    return sorted(groups, key=lambda g: visits.get(g["id"], ""))[:n]


def run_facebook(cfg, db, pipe: Pipeline, stats, debug=False) -> Optional[str]:
    from .facebook import FacebookBlocked, FacebookSource, load_groups

    fb_cfg = cfg.get("facebook") or {}
    groups = load_groups(cfg)
    markets = fb_cfg.get("marketplace_urls") or []
    if not groups and not markets:
        log.info("facebook: no groups / marketplace urls configured (run: python -m hunter fb-groups)")
        return None
    visits = json.loads(db.get_meta("fb_group_visits") or "{}")
    try:
        with FacebookSource(cfg, debug=debug) as fb:
            for g in pick_groups(groups, visits, int(fb_cfg.get("groups_per_run", 10))):
                for listing in fb.scan_group(g):
                    stats["seen"] += 1
                    if db.upsert_seen(listing)[0]:
                        stats["new"] += 1
                visits[g["id"]] = now()
                db.set_meta("fb_group_visits", json.dumps(visits))
            for url in markets:
                for listing in fb.scan_marketplace(url):
                    stats["seen"] += 1
                    if db.upsert_seen(listing)[0]:
                        stats["new"] += 1
            db.commit()

            pending = db.pending_details(int(fb_cfg.get("max_posts_per_run", 200)),
                                         sources=SOURCE_GROUPS["facebook"])
            market_budget = int(fb_cfg.get("max_marketplace_items_per_run", 15))
            for row in pending:
                listing = Listing.from_dict(json.loads(row["data"]))
                if listing.source == "fb_market":
                    if market_budget <= 0:
                        continue
                    market_budget -= 1
                    listing = fb.enrich_marketplace(listing)
                    stats["details"] += 1
                pipe.process(listing)
        db.set_meta("fb_last_run", now())
    except FacebookBlocked as e:
        log.error("facebook stopped: %s", e)
        return "פייסבוק ביקש התחברות/אימות מחדש. הרץ: python -m hunter fb-login"
    except Exception as e:  # browser crash, selector timeout... don't kill the whole run
        log.exception("facebook run failed")
        return f"שגיאה בסריקת פייסבוק: {type(e).__name__}"
    finally:
        db.commit()
    return None


# ---------------------------------------------------------------- notifications
def _bootstrap(cfg, db, notifier, notify, source_group: str, ok: bool) -> None:
    """Until a source's first full pass succeeds, everything it finds counts as 'already existed'."""
    meta_key = f"bootstrapped:{source_group}"
    if db.get_meta(meta_key) or cfg["run"].get("notify_on_first_run", False):
        return
    sources = SOURCE_GROUPS[source_group]
    for r in db.to_notify():
        if r["source"] in sources:
            db.mark_notified(r["key"], 2)  # 2 = existed before the agent started
    if ok and not db.pending_details(1, sources=sources):
        db.set_meta(meta_key, now())
        rows = db.conn.execute(
            f"SELECT verdict FROM listings WHERE dup_of IS NULL AND source IN ({','.join('?' * len(sources))})",
            sources).fetchall()
        m = sum(1 for r in rows if r["verdict"] == "match")
        c = sum(1 for r in rows if r["verdict"] == "check")
        name = {"yad2": "יד 2", "facebook": "פייסבוק"}[source_group]
        if notify:
            notifier.send(f"🏠 הסריקה הראשונית של {name} הסתיימה: {m} התאמות מלאות, {c} דורשות בירור. "
                          "מעכשיו אשלח רק מודעות חדשות. הכל בדאשבורד.")
    db.commit()


def run_scan(cfg: dict, notify: bool = True, only: Optional[str] = None, debug: bool = False) -> dict:
    db = DB(cfg["db_path"])
    notifier = Notifier()
    llm = LLMAnalyzer.from_config(cfg)
    started = now()
    stats = {"seen": 0, "new": 0, "details": 0, "llm_calls": 0, "duplicates": 0, "notified": 0,
             "price_drops": 0, "error": None}
    price_drops: dict[str, int] = {}
    pipe = Pipeline(cfg, db, llm, stats)
    errors = []

    if (cfg.get("yad2") or {}).get("enabled", True) and only in (None, "yad2"):
        err = run_yad2(cfg, db, pipe, stats, price_drops)
        if err:
            errors.append(err)
        _bootstrap(cfg, db, notifier, notify, "yad2", err is None)

    fb_enabled = (cfg.get("facebook") or {}).get("enabled", False)
    if fb_enabled and (only == "facebook" or (only is None and facebook_due(cfg, db))):
        err = run_facebook(cfg, db, pipe, stats, debug=debug)
        if err:
            errors.append(err)
        _bootstrap(cfg, db, notifier, notify, "facebook", err is None)

    # notify new listings (bootstrap already silenced the backlog)
    rows = db.to_notify()
    if not cfg["run"].get("notify_check_listings", True):
        rows = [r for r in rows if r["verdict"] == "match"]
    if notify:
        for r in rows:
            listing = Listing.from_dict(json.loads(r["data"]))
            if notifier.send(format_listing(listing, json.loads(r["analysis"])),
                             photo=listing.images[0] if listing.images else None):
                db.mark_notified(r["key"])
                stats["notified"] += 1
            time.sleep(random.uniform(0.5, 1.5))
        for key, old in price_drops.items():
            r = db.get(key)
            if r and r["verdict"] in ("match", "check") and r["status"] != "not_relevant" and not r["dup_of"]:
                listing = Listing.from_dict(json.loads(r["data"]))
                listing.price = r["price"]
                notifier.send("📉 ירידת מחיר\n" + format_listing(listing, json.loads(r["analysis"]), old))
                stats["price_drops"] += 1
        for err in errors:
            notifier.send("⚠ " + err)
    stats["error"] = " | ".join(errors) or None
    db.commit()
    db.log_run(started, stats["seen"], stats["new"], stats["notified"], stats["error"])
    log.info("scan done: %s", stats)
    return stats
