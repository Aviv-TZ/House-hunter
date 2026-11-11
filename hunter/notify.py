from __future__ import annotations

import html
import logging
import os

import requests

from .models import Listing

log = logging.getLogger(__name__)


def format_listing(listing: Listing, analysis: dict, price_drop_from: int | None = None) -> str:
    e = html.escape
    icon = {"match": "✅", "check": "🟡"}.get(analysis["verdict"], "•")
    star = "⭐ " if analysis.get("priority") else ""
    price = f"{listing.price:,} ₪" if listing.price else "מחיר לא צוין"
    if price_drop_from:
        price += f" (ירד מ-{price_drop_from:,})"
    floor = f"קומה {listing.floor}" if listing.floor is not None else "קומה ?"
    if listing.top_floor:
        floor += f"/{listing.top_floor}"
    elevator = {True: "מעלית ✔", False: "בלי מעלית", None: "מעלית ?"}[listing.elevator]
    lines = [
        f"{icon} {star}<b>{e(listing.address or listing.city)}</b>",
        f"💰 {e(price)} · 🛏 {listing.rooms} חד' · {listing.sqm or '?'} מ\"ר",
        f"🏢 {floor} · {elevator}" + (" · 🚗 חניה" if listing.parking else ""),
    ]
    if listing.is_broker:
        lines.append(f"🧾 תיווך{': ' + e(listing.agency_name) if listing.agency_name else ''}")
    if listing.entrance_date:
        lines.append(f"📅 כניסה: {e(listing.entrance_date)}")
    if analysis.get("summary"):
        lines.append(f"📝 {e(analysis['summary'])}")
    if analysis.get("pluses"):
        lines.append("👍 " + e(" · ".join(analysis["pluses"])))
    if analysis.get("flags"):
        lines.append("❓ לבדוק: " + e(" · ".join(analysis["flags"])))
    lines.append(f'<a href="{e(listing.url)}">פתח מודעה</a>')
    return "\n".join(lines)


class Notifier:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, photo: str | None = None) -> bool:
        if not self.enabled:
            print("\n" + text.replace("<b>", "").replace("</b>", "") + "\n" + "-" * 40)
            return True
        base = f"https://api.telegram.org/bot{self.token}"
        try:
            if photo and len(text) <= 1024:
                r = requests.post(f"{base}/sendPhoto", timeout=30,
                                  data={"chat_id": self.chat_id, "photo": photo, "caption": text, "parse_mode": "HTML"})
                if r.ok:
                    return True
            r = requests.post(f"{base}/sendMessage", timeout=30,
                              data={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                                    "disable_web_page_preview": "true"})
            if not r.ok:
                log.warning("Telegram error %s: %s", r.status_code, r.text[:200])
            return r.ok
        except requests.RequestException as e:
            log.warning("Telegram send failed: %s", e)
            return False
