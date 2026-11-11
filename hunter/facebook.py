"""Facebook groups + Marketplace, read through a real browser logged in as you.

How it works
  * A dedicated Chromium profile lives in data/fb_profile. You log in once by hand
    (`python -m hunter fb-login`); the session cookie is reused afterwards.
  * Each run opens a few groups (sorted by newest), scrolls a little, reads the posts,
    and opens new Marketplace rental items. Slow, random pauses, a small number of pages.
  * No stealth tricks, no captcha solving: if Facebook asks to log in again or shows a
    security checkpoint, the run stops and you get a message.

Facebook's markup changes often; selectors here are deliberately generic
(role="article", permalinks in hrefs). `--debug` saves HTML snapshots to data/fb_debug/
so selectors can be tuned.
"""
from __future__ import annotations

import hashlib
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional

import yaml

from .config import ROOT
from .dedup import normalize_text
from .models import Listing

log = logging.getLogger(__name__)

PROFILE_DIR = ROOT / "data" / "fb_profile"
DEBUG_DIR = ROOT / "data" / "fb_debug"
GROUP_KEYWORDS = ["דירות", "דירה", "השכרה", "להשכרה", "שכירות", "שכירויות", "רמת גן", "גבעתיים", "תל אביב",
                  "ר\"ג", "רמת-גן", "ramat gan", "givatayim", "tel aviv", "apartment", "rent"]
SEE_MORE = ["עוד", "ראה עוד", "הצג עוד", "See more", "See More"]
UI_LINES = {"לייק", "אהבתי", "תגובה", "הגב", "שיתוף", "שתף", "שלח", "like", "comment", "share", "send",
            "follow", "עקוב", "הצטרף", "join", "·", "מעורבות", "תגובות", "comments", "most relevant",
            "הרלוונטיות ביותר", "write a comment…", "כתוב תגובה…", "facebook"}

GROUP_POSTS_JS = r"""
() => {
  const out = [];
  for (const art of document.querySelectorAll('[role="article"]')) {
    if (art.parentElement && art.parentElement.closest('[role="article"]')) continue;  // comments
    let link = null;
    for (const a of art.querySelectorAll('a[href]')) {
      const h = a.href;
      if (/\/groups\/[^/]+\/(posts|permalink)\/\d+/.test(h) || /[?&]story_fbid=/.test(h) ||
          /\/groups\/[^/]+\/posts\/pfbid/.test(h)) { link = h; break; }
    }
    const img = [...art.querySelectorAll('img')].map(i => i.src).find(s => s && s.includes('scontent'));
    out.push({text: art.innerText || '', link, img: img || null});
  }
  return out;
}
"""

MARKET_CARDS_JS = r"""
() => {
  const seen = new Set(), out = [];
  for (const a of document.querySelectorAll('a[href*="/marketplace/item/"]')) {
    const m = a.href.match(/\/marketplace\/item\/(\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const img = a.querySelector('img');
    out.push({id: m[1], text: a.innerText || '', img: img ? img.src : null});
  }
  return out;
}
"""

JOINED_GROUPS_JS = r"""
() => {
  const out = {};
  for (const a of document.querySelectorAll('a[href*="/groups/"]')) {
    const m = a.href.match(/facebook\.com\/groups\/([^/?#]+)\/?(?:[?#].*)?$/);
    const name = (a.innerText || '').trim().split('\n')[0];
    if (!m || !name || ['feed', 'discover', 'joins', 'create', 'notifications'].includes(m[1])) continue;
    if (!out[m[1]] || out[m[1]].length < name.length) out[m[1]] = name;
  }
  return Object.entries(out).map(([id, name]) => ({id, name}));
}
"""


class FacebookBlocked(RuntimeError):
    """Not logged in, or Facebook showed a checkpoint."""


def clean_post_text(text: str) -> str:
    """Drop UI chrome lines (like / comment / counts) so the same post hashes equally in every group."""
    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if len(s) < 2 or s.lower() in UI_LINES or re.fullmatch(r"[\d\s.,KkMm·]+", s):
            continue
        if re.fullmatch(r"(?:\d+\s*)?(?:תגובות|תגובה|שיתופים|comments?|shares?)", s, re.IGNORECASE):
            continue
        lines.append(s)
    return "\n".join(lines)


def post_id(link: Optional[str], group_id: str, text: str) -> str:
    if link:
        m = (re.search(r"/(?:posts|permalink)/(\d+|pfbid\w+)", link)
             or re.search(r"story_fbid=(\d+|pfbid\w+)", link))
        if m:
            return m.group(1)
    return "h" + hashlib.sha1(f"{group_id}|{normalize_text(text)}".encode()).hexdigest()[:15]


def load_groups(cfg: dict) -> list[dict]:
    fb = cfg.get("facebook") or {}
    groups = list(fb.get("groups") or [])
    gfile = ROOT / fb.get("groups_file", "data/fb_groups.yaml")
    if gfile.exists():
        data = yaml.safe_load(gfile.read_text(encoding="utf-8")) or {}
        groups += [g for g in (data.get("groups") or []) if g.get("enabled", True)]
    out, seen = [], set()
    for g in groups:
        gid = str(g.get("id") or re.sub(r".*/groups/([^/?#]+).*", r"\1", g.get("url", "")))
        if gid and gid not in seen:
            seen.add(gid)
            out.append({"id": gid, "name": g.get("name", gid)})
    return out


class FacebookSource:
    def __init__(self, cfg: dict, headless: Optional[bool] = None, debug: bool = False):
        self.cfg = cfg.get("facebook") or {}
        self.headless = self.cfg.get("headless", True) if headless is None else headless
        self.debug = debug
        self.delay = tuple(self.cfg.get("delay_between_pages", (15, 45)))
        self._pw = self._ctx = self.page = None
        self.pages_opened = 0

    # ---------- browser lifecycle ----------
    def __enter__(self):
        from playwright.sync_api import sync_playwright

        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        kwargs = dict(user_data_dir=str(PROFILE_DIR), headless=self.headless, locale="he-IL",
                      viewport={"width": 1280, "height": 900})
        if self.cfg.get("browser_channel"):
            kwargs["channel"] = self.cfg["browser_channel"]  # e.g. "chrome" to use installed Google Chrome
        self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self

    def __exit__(self, *exc):
        try:
            if self._ctx:
                self._ctx.close()
        finally:
            if self._pw:
                self._pw.stop()

    # ---------- helpers ----------
    def _pause(self, lo=None, hi=None):
        time.sleep(random.uniform(lo if lo is not None else self.delay[0], hi if hi is not None else self.delay[1]))

    def _goto(self, url: str):
        if self.pages_opened:
            self._pause()
        self.pages_opened += 1
        self.page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        self._pause(3, 6)
        self._check_session()

    def _check_session(self):
        url = self.page.url
        if "/login" in url or "/checkpoint" in url or "two_step_verification" in url:
            raise FacebookBlocked(f"Facebook needs you to log in / verify ({url})")
        if self.page.locator('input[name="email"], input[name="pass"]').count() > 0:
            raise FacebookBlocked("Facebook is showing the login form")

    def _scroll(self, times: int):
        for _ in range(times):
            self.page.mouse.wheel(0, random.randint(1400, 2200))
            self._pause(2, 4)

    def _expand_see_more(self):
        for label in SEE_MORE:
            buttons = self.page.locator(f'[role="article"] div[role="button"]:text-is("{label}")')
            for i in range(min(buttons.count(), 25)):
                try:
                    buttons.nth(i).click(timeout=1500)
                    time.sleep(random.uniform(0.2, 0.6))
                except Exception:
                    pass

    def _snapshot(self, name: str):
        if self.debug:
            DEBUG_DIR.mkdir(parents=True, exist_ok=True)
            (DEBUG_DIR / f"{name}.html").write_text(self.page.content(), encoding="utf-8")
            self.page.screenshot(path=str(DEBUG_DIR / f"{name}.png"))

    # ---------- groups ----------
    def scan_group(self, group: dict) -> list[Listing]:
        gid = group["id"]
        self._goto(f"https://www.facebook.com/groups/{gid}/?sorting_setting=CHRONOLOGICAL")
        self._scroll(int(self.cfg.get("scrolls_per_group", 3)))
        self._expand_see_more()
        posts = self.page.evaluate(GROUP_POSTS_JS)
        self._snapshot(f"group_{gid}")
        out = []
        for p in posts:
            text = clean_post_text(p["text"])
            if len(text) < 40:
                continue
            pid = post_id(p.get("link"), gid, text)
            out.append(Listing(
                source="fb_group", source_id=f"{gid}_{pid}",
                url=(p.get("link") or f"https://www.facebook.com/groups/{gid}/").split("?")[0],
                description=text, images=[p["img"]] if p.get("img") else [],
                source_label=group.get("name", gid),
            ))
        log.info("facebook group %s (%s): %d posts", group.get("name"), gid, len(out))
        return out

    # ---------- marketplace ----------
    def scan_marketplace(self, url: str) -> list[Listing]:
        self._goto(url)
        self._scroll(int(self.cfg.get("scrolls_marketplace", 4)))
        cards = self.page.evaluate(MARKET_CARDS_JS)
        self._snapshot("marketplace")
        out = [Listing(source="fb_market", source_id=c["id"],
                       url=f"https://www.facebook.com/marketplace/item/{c['id']}/",
                       description=clean_post_text(c["text"]), images=[c["img"]] if c.get("img") else [],
                       source_label="Marketplace")
               for c in cards]
        log.info("marketplace: %d items", len(out))
        return out

    def enrich_marketplace(self, listing: Listing) -> Listing:
        self._goto(listing.url)
        main = self.page.locator('div[role="main"]').first
        text = main.inner_text(timeout=15_000) if main.count() else self.page.inner_text("body")
        # the item page ends with "similar items" etc. - keep the top part
        listing.description = clean_post_text(text)[:4000]
        og = self.page.locator('meta[property="og:image"]')
        if og.count():
            listing.images = [og.first.get_attribute("content")] + listing.images
        self._snapshot(f"item_{listing.source_id}")
        return listing

    # ---------- setup helpers ----------
    def login_interactively(self):
        self.page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        input("\nהתחבר לפייסבוק בחלון שנפתח (כולל אימות דו-שלבי אם יש), ואז לחץ Enter כאן... ")
        self._check_session()
        print("✅ מחובר. הפרופיל נשמר ב-data/fb_profile")

    def list_joined_groups(self) -> list[dict]:
        self._goto("https://www.facebook.com/groups/joins/?nav_source=tab")
        self._scroll(int(self.cfg.get("scrolls_group_list", 12)))
        return self.page.evaluate(JOINED_GROUPS_JS)


def write_groups_file(groups: list[dict], cfg: dict) -> Path:
    """Write discovered groups to data/fb_groups.yaml; rental-looking ones enabled."""
    gfile = ROOT / (cfg.get("facebook") or {}).get("groups_file", "data/fb_groups.yaml")
    rows = []
    for g in sorted(groups, key=lambda g: g["name"]):
        name = g["name"].lower()
        rows.append({"id": g["id"], "name": g["name"], "enabled": any(k.lower() in name for k in GROUP_KEYWORDS)})
    rows.sort(key=lambda r: not r["enabled"])
    header = ("# קבוצות הפייסבוק שלך. enabled: true = נסרקת.\n"
              "# סימנתי אוטומטית קבוצות ששמן נראה קשור לדירות - עבור על הרשימה ותקן.\n")
    gfile.parent.mkdir(parents=True, exist_ok=True)
    gfile.write_text(header + yaml.safe_dump({"groups": rows}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return gfile
