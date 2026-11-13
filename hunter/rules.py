"""Decide whether a listing fits the criteria.

verdict:
  match  - everything known passes
  check  - nothing fails, but something must be confirmed with the landlord
  reject - a hard criterion fails
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from .models import Listing

ANIMAL = r'(?:חיות(?:\s*מחמד)?|בעלי\s*(?:חיים|חיות)(?:\s*מחמד)?|בע"ח|כלבים|חתולים)'
PETS_NO = [rf"(?:ללא|בלי|אין|לא\s*מתאים\s*ל|לא\s*מאפשרים|אסור(?:ות)?)\s*{ANIMAL}", rf"{ANIMAL}\s*[-:]?\s*(?:לא|אסור)",
           r"no\s*pets"]
PETS_YES = [rf"{ANIMAL}\s*[-:]?\s*(?:מותר|בברכה|מתקבל|אפשרי|אין\s*בעיה|כן|יתקבלו)", rf"מתאים\s*ל{ANIMAL}",
            rf"(?:מותר|אפשר)\s*(?:עם\s*)?{ANIMAL}", r"אפשר\s*(?:עם\s*)?(?:כלב|חתול)", r"pet\s*friendly",
            r"pets?\s*(?:allowed|welcome|ok)"]
KITCHEN_SMALL = [r"מטבח\s*(?:קטן|צר|קטנטן)", r"מטבחון", r"פינת\s*בישול"]
KITCHEN_BIG = [r"מטבח\s*(?:גדול|מרווח|ענק|רחב|גדול\s*ומאובזר)", r"מטבח\s*\S*\s*(?:גדול|מרווח)"]
NO_BROKER = [r"ללא\s*(?:דמי\s*)?תיווך", r"בלי\s*(?:דמי\s*)?תיווך", r"לא\s*תיווך", r"ללא\s*מתווכים", r"מתווכים\s*לא"]
BROKER = [r"דמי\s*תיווך", r"עמלת\s*תיווך", r"\bתיווך\b"]
PARKING_PRIVATE = [r"חני{1,2}ה\s*(?:פרטית|צמודה|בטאבו|מקורה|בבניין|תת\s*קרקעית|משותפת)", r"כולל\s*חני{1,2}ה",
                   r"מקום\s*חני{1,2}ה", r"\d\s*חניות", r"חני{1,2}ה\s*ל-?\s*\d\s*רכבים", r"private\s*parking"]
PARKING_EASY = [r"חני{1,2}ה\s*(?:ברחוב\s*)?בשפע", r"שפע\s*(?:של\s*)?חני{1,2}ה", r"(?:קל|קלה|נוח)\s*(?:מאוד\s*)?(?:למצוא\s*)?חני{1,2}ה",
                r"אין\s*בעי(?:ה|ית)\s*חני{1,2}ה", r"חני{1,2}ה\s*(?:חופשית|קלה|נוחה)\s*(?:ברחוב|בסביבה)?", r"easy\s*parking"]
PARKING_HARD = [r"(?:קשה|בעייתי)\s*(?:למצוא\s*)?חני{1,2}ה", r"בעיית\s*חני{1,2}ה", r"אין\s*חני{1,2}ה", r"ללא\s*חני{1,2}ה"]
ROOMMATE_AD = [r"(?:מחפש|מחפשת|מחפשים|דרוש|דרושה)\s*(?:\S+\s*)?שות[פף]", r"חדר\s*(?:להשכרה\s*)?בדירת\s*שותפים"]


def _any(patterns, text) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def keyword_signals(text: str) -> dict:
    """Signals from free text. None = not mentioned."""
    text = text or ""
    pets = False if _any(PETS_NO, text) else (True if _any(PETS_YES, text) else None)
    kitchen = "small" if _any(KITCHEN_SMALL, text) else ("large" if _any(KITCHEN_BIG, text) else None)
    if _any(NO_BROKER, text):
        broker = False
    elif _any(BROKER, text):
        broker = True
    else:
        broker = None
    if _any(PARKING_PRIVATE, text):
        parking = "private"
    elif _any(PARKING_EASY, text):
        parking = "street_easy"
    elif _any(PARKING_HARD, text):
        parking = "street_hard"
    else:
        parking = None
    return {"pets": pets, "kitchen": kitchen, "broker": broker, "parking": parking,
            "roommate_ad": _any(ROOMMATE_AD, text)}


@dataclass
class Analysis:
    verdict: str = "match"
    score: float = 0.0
    reasons: list = field(default_factory=list)   # why rejected
    flags: list = field(default_factory=list)     # needs confirmation
    pluses: list = field(default_factory=list)    # nice things
    warnings: list = field(default_factory=list)  # informational notes from Claude (don't change verdict)
    priority: bool = False
    pets: Optional[bool] = None
    kitchen: Optional[str] = None
    parking: Optional[str] = None
    summary: str = ""
    llm_used: bool = False

    def to_dict(self):
        return asdict(self)


def _norm(t: str) -> str:
    return re.sub(r"[\"'״׳\-,.()]", " ", t or "").replace("הותיקים", "ותיקים").replace("  ", " ").strip()


def is_priority(listing: Listing, cfg: dict) -> bool:
    if listing.neighborhood_id in cfg["priority_neighborhoods"]:
        return True
    if listing.city_id not in (None, 8600):  # all priority neighborhoods are in Ramat Gan
        return False
    hay = _norm(" ".join([listing.neighborhood or "", listing.street or "",
                          listing.description if listing.source != "yad2" else ""]))
    return any(_norm(name) in hay for name in cfg.get("priority_neighborhood_names", []))


def prefilter(listing: Listing, cfg: dict) -> list[str]:
    """Cheap checks on feed data only (before fetching details). Returns reject reasons."""
    s, r = cfg["search"], cfg["rules"]
    reasons = []
    allowed_cities = {c["id"] for c in s["cities"]}
    if listing.is_rental_offer is False:
        reasons.append("לא מודעת השכרה (מחפשים דירה / מכירה / סאבלט וכו')")
    if listing.city_id and listing.city_id not in allowed_cities:
        reasons.append(f"עיר לא ברשימה ({listing.city or 'אחר'})")
    if listing.price and listing.price < s.get("min_price", 0):
        reasons.append(f"מחיר לא סביר ({listing.price:,})")
    if listing.price and listing.price > s["max_price"]:
        reasons.append(f"מחיר {listing.price:,} מעל {s['max_price']:,}")
    if listing.rooms is not None and listing.rooms < s["min_rooms"]:
        reasons.append(f"{listing.rooms} חדרים")
    if listing.property_type in r.get("excluded_property_types", []):
        reasons.append(f"סוג נכס: {listing.property_type}")
    if listing.is_broker and listing.price and listing.price > r["broker_max_price"]:
        reasons.append(f"תיווך במחיר {listing.price:,} (מעל {r['broker_max_price']:,})")
    return reasons


def analyze(listing: Listing, cfg: dict, llm: Optional[dict] = None) -> Analysis:
    s, r = cfg["search"], cfg["rules"]
    a = Analysis()
    a.reasons = prefilter(listing, cfg)
    kw = keyword_signals(listing.description)
    llm = llm or {}
    a.llm_used = bool(llm)
    a.summary = llm.get("summary", "")

    # --- broker (structured adType first, then the ad text) ---
    if listing.is_broker:
        is_broker = True
    elif kw["broker"] is True or (kw["broker"] is None and llm.get("broker_fee") == "yes"):
        is_broker = True
    elif listing.is_broker is False or kw["broker"] is False:
        is_broker = False
    else:
        is_broker = None
    if is_broker and not listing.is_broker and listing.price and listing.price > r["broker_max_price"]:
        a.reasons.append(f"מוזכר תיווך במחיר {listing.price:,}")
    elif is_broker and listing.price and listing.price <= r["broker_max_price"]:
        a.flags.append("תיווך (המחיר מאפשר)")
    elif is_broker is False:
        a.pluses.append("ללא תיווך")

    # --- roommates ad ---
    if kw["roommate_ad"] or llm.get("roommates_ad"):
        a.reasons.append("מודעת שותפים / חדר בדירה")

    # --- missing basics (mostly facebook posts) ---
    if not listing.city_id:
        a.flags.append("עיר לא צוינה")
    if listing.rooms is None:
        a.flags.append("מספר חדרים לא צוין")

    # --- price ---
    if not listing.price:
        a.flags.append("מחיר לא צוין")
    elif listing.price < r.get("suspicious_price_below", 0):
        a.flags.append(f"מחיר נמוך באופן חשוד ({listing.price:,})")

    # --- elevator ---
    limit = r["elevator_required_above_floor"]
    if listing.floor is None:
        if listing.elevator is not True:
            a.flags.append("קומה לא צוינה - לבדוק מעלית")
    elif listing.floor > limit:
        if listing.elevator is False:
            a.reasons.append(f"קומה {listing.floor} בלי מעלית")
        elif listing.elevator is None:
            a.flags.append(f"קומה {listing.floor} - לא ידוע אם יש מעלית")

    # --- pets ---
    pets = listing.pets if listing.pets is not None else kw["pets"]
    if pets is None and llm.get("pets") in ("allowed", "not_allowed"):
        pets = llm["pets"] == "allowed"
    a.pets = pets
    if r.get("pets_required"):
        if pets is False:
            a.reasons.append("לא מאפשרים חיות מחמד")
        elif pets is None:
            a.flags.append("חיות מחמד לא צוין - לבקש אישור")
        else:
            a.pluses.append("מאפשרים חיות")

    # --- kitchen ---
    kitchen = kw["kitchen"] or (llm.get("kitchen") if llm.get("kitchen") in ("small", "normal", "large") else None)
    a.kitchen = kitchen
    if r.get("kitchen_not_small"):
        if kitchen == "small":
            a.reasons.append("מטבח קטן")
        elif kitchen is None:
            a.flags.append("גודל מטבח לא ידוע - לבקש תמונה")
        elif kitchen == "large":
            a.pluses.append("מטבח גדול")

    a.warnings = list(llm.get("red_flags") or [])

    # --- parking (big bonus; private > plentiful street parking) ---
    parking = listing.parking_type or ("private" if listing.parking else None) or kw["parking"]
    if parking is None and llm.get("parking") not in (None, "unknown"):
        parking = llm["parking"]
    a.parking = parking

    # --- priority & score ---
    a.priority = is_priority(listing, cfg)
    score = 50.0
    if a.priority:
        score += 30
        a.pluses.insert(0, "שכונה מועדפת")
    score += {8600: 10, 6300: 5}.get(listing.city_id or 0, 0)
    if listing.price and listing.price >= r.get("suspicious_price_below", 0):
        score += max(0, (s["max_price"] - listing.price) / 250)
    if parking == "private":
        score += r.get("parking_private_bonus", 12)
        a.pluses.append("🚗 חניה פרטית")
    elif parking == "street_easy":
        score += r.get("parking_street_bonus", 6)
        a.pluses.append("🚗 חניה בשפע ברחוב")
    elif parking in ("street_hard", "none"):
        score -= 3
    if listing.mamad:
        score += 3
        a.pluses.append('ממ"ד')
    if listing.balcony:
        score += 2
    if is_broker is False:
        score += 5
    if kitchen == "large":
        score += 5
    if pets is True:
        score += 5
    if listing.rooms and listing.rooms >= 4:
        score += 3
    score -= 4 * len(a.flags)
    a.score = round(score, 1)

    a.verdict = "reject" if a.reasons else ("check" if a.flags else "match")
    return a
