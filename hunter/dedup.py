"""Find the same apartment posted in several places (yad2, many FB groups, Marketplace).

Signals, strongest first:
  1. identical normalized text (same post copied to 10 groups) - checked BEFORE calling Claude
  2. same phone number + similar price
  3. near-identical text (word 3-gram Jaccard)
  4. same city + street + rooms + similar price, and house number / floor don't contradict
"""
from __future__ import annotations

import hashlib
import re
from typing import Iterable, Optional

from .models import Listing

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACES = re.compile(r"\s+")
_STREET_PREFIX = re.compile(r"^(?:רחוב|רח'|רח|שדרות|שד'|שד)\s+")


def normalize_text(text: str) -> str:
    t = _PUNCT.sub(" ", (text or "").lower())
    t = re.sub(r"\d{9,10}", " ", t)  # phone numbers differ between reposts sometimes
    return _SPACES.sub(" ", t).strip()


def text_hash(text: str) -> Optional[str]:
    norm = normalize_text(text)
    if len(norm.split()) < 8:
        return None
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:16]


def shingles(text: str, n: int = 3) -> set:
    words = normalize_text(text).split()
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize_phone(p: str) -> str:
    d = re.sub(r"\D", "", p or "")
    if d.startswith("972"):
        d = "0" + d[3:]
    return d if len(d) in (9, 10) else ""


def normalize_street(s: str) -> str:
    s = _PUNCT.sub(" ", (s or "").strip())
    s = _SPACES.sub(" ", s).strip()
    return _STREET_PREFIX.sub("", s)


def _close_price(a: Optional[int], b: Optional[int], tol: float) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= tol * max(a, b)


def _compatible(a, b) -> bool:
    """Equal, or at least one side unknown."""
    return a in (None, "") or b in (None, "") or str(a) == str(b)


def is_duplicate(a: Listing, b: Listing) -> Optional[str]:
    """Return the reason if a and b look like the same apartment, else None."""
    if a.key == b.key:
        return None
    if a.source == b.source == "yad2":
        return None  # yad2 already has unique tokens; same flat by 2 agencies is rare and both are useful
    pa, pb = normalize_phone(a.phone), normalize_phone(b.phone)
    if pa and pa == pb and (_close_price(a.price, b.price, 0.1) or not a.price or not b.price):
        if _compatible(a.rooms, b.rooms):
            return "אותו טלפון"
    if len(a.description) > 60 and len(b.description) > 60:
        if (jaccard(shingles(a.description), shingles(b.description)) >= 0.6
                and _compatible(a.rooms, b.rooms)
                and (_close_price(a.price, b.price, 0.1) or not a.price or not b.price)):
            return "טקסט כמעט זהה"
    sa, sb = normalize_street(a.street), normalize_street(b.street)
    if (sa and sa == sb and a.city_id and a.city_id == b.city_id
            and a.rooms and b.rooms and abs(float(a.rooms) - float(b.rooms)) <= 0.5
            and _close_price(a.price, b.price, 0.07)
            and _compatible(a.house_number, b.house_number) and _compatible(a.floor, b.floor)):
        # need at least one positive confirmation beyond the street
        if (a.house_number and a.house_number == b.house_number) or (a.floor is not None and a.floor == b.floor):
            return "אותה כתובת, חדרים ומחיר"
    return None


def find_duplicate(listing: Listing, candidates: Iterable[tuple[str, Listing]]) -> Optional[tuple[str, str]]:
    """candidates: (canonical_key, listing). Returns (canonical_key, reason) of the first match."""
    for canonical_key, other in candidates:
        reason = is_duplicate(listing, other)
        if reason:
            return canonical_key, reason
    return None
