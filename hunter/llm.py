"""Claude reads an ad's free text and returns structured facts.

Used for two things:
  * Facebook posts / Marketplace items - free text only, Claude extracts everything.
  * Yad2 ads - structure is known, Claude fills the gaps (pets, kitchen, street parking...).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .models import Listing

log = logging.getLogger(__name__)

SYSTEM = """You read Israeli apartment-rental posts (usually Hebrew, sometimes English) for a tenant \
and extract facts as JSON. Report only what the text says; use null / "unknown" when it is not mentioned. \
Never guess an address or a price.

Fields:
- is_rental_offer: true only if someone OFFERS a whole apartment for long-term rent. false for: people \
looking for an apartment, sales, sublets / short term, rooms in shared apartments, roommates wanted, \
commercial property, apartment swaps, ads for services.
- roommates_ad: true if it is a room in a shared apartment / roommate search.
- price: monthly rent in NIS (integer). If several apartments are listed, the main one.
- rooms: number of rooms in Israeli convention (living room counts; "2 חדרי שינה + סלון" = 3).
- city: one of "תל אביב יפו", "רמת גן", "גבעתיים", or "אחר" if it is clearly another city, null if unknown. \
Infer the city from a well-known neighborhood or street only when unambiguous (e.g. "נגבה" / "מרום נווה" → רמת גן, \
"בורוכוב גבעתיים" → גבעתיים, "פלורנטין" → תל אביב יפו).
- neighborhood, street, house_number: as written (Hebrew), null if missing.
- floor: integer (ground floor / קרקע = 0).
- elevator: "yes" / "no" / "unknown".
- parking: "private" (own spot / חניה בטאבו / חניה צמודה / חניה בבניין), "street_easy" (the post says street \
parking is easy / plentiful, e.g. "חניה בשפע", "קל למצוא חניה"), "street_hard", "none", or "unknown".
- pets: "allowed" / "not_allowed" / "unknown".
- kitchen: "small" (small / kitchenette), "large" (big / spacious), "normal" (mentioned, no size), "unknown".
- broker_fee: "yes" if a brokerage fee applies or the poster is an agency, "no" if it says no brokerage, else "unknown".
- entrance_date: as written, null if missing.
- phone: Israeli phone number if present (digits only), else null.
- red_flags: up to 3 short Hebrew notes a tenant should know (short contract, renovation, guarantees, \
noise, partial furniture, etc.).
- summary: one short Hebrew sentence describing the apartment."""


def _nullable(t: str) -> dict:
    return {"anyOf": [{"type": t}, {"type": "null"}]}


def _enum(*values) -> dict:
    return {"type": "string", "enum": list(values)}


SCHEMA = {
    "type": "object",
    "properties": {
        "is_rental_offer": {"type": "boolean"},
        "roommates_ad": {"type": "boolean"},
        "price": _nullable("integer"),
        "rooms": _nullable("number"),
        "sqm": _nullable("integer"),
        "city": _nullable("string"),
        "neighborhood": _nullable("string"),
        "street": _nullable("string"),
        "house_number": _nullable("string"),
        "floor": _nullable("integer"),
        "elevator": _enum("yes", "no", "unknown"),
        "parking": _enum("private", "street_easy", "street_hard", "none", "unknown"),
        "pets": _enum("allowed", "not_allowed", "unknown"),
        "kitchen": _enum("small", "normal", "large", "unknown"),
        "broker_fee": _enum("yes", "no", "unknown"),
        "entrance_date": _nullable("string"),
        "phone": _nullable("string"),
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["is_rental_offer", "roommates_ad", "price", "rooms", "sqm", "city", "neighborhood", "street",
                 "house_number", "floor", "elevator", "parking", "pets", "kitchen", "broker_fee",
                 "entrance_date", "phone", "red_flags", "summary"],
    "additionalProperties": False,
}


def known_facts(listing: Listing) -> str:
    """Structured facts we already have (Yad2) so Claude focuses on the free text."""
    if listing.source != "yad2":
        return ""
    return (f"Known from the listing site: {listing.property_type}, {listing.rooms} rooms, {listing.sqm or '?'} sqm, "
            f"floor {listing.floor}, price {listing.price}, address {listing.address}, "
            f"{'agency' if listing.is_broker else 'private owner'}.\n\n")


class LLMAnalyzer:
    def __init__(self, model: str, effort: str = "low"):
        import anthropic

        self.client = anthropic.Anthropic()
        self.anthropic = anthropic
        self.model = model
        self.effort = effort
        self.calls = 0

    @classmethod
    def from_config(cls, cfg: dict) -> Optional["LLMAnalyzer"]:
        llm_cfg = cfg.get("llm") or {}
        if not llm_cfg.get("enabled") or not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        return cls(llm_cfg.get("model", "claude-opus-5-5"), llm_cfg.get("effort", "low"))

    def analyze(self, listing: Listing) -> Optional[dict]:
        text = listing.description.strip()
        if not text:
            return None
        content = f"{known_facts(listing)}Source: {listing.source_name}\n\nPost text:\n{text[:6000]}"
        kwargs = dict(
            model=self.model,
            max_tokens=3000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        self.calls += 1
        try:
            if self.model.startswith("claude-haiku"):
                resp = self.client.messages.create(**kwargs)
            else:
                # Server-side fallback if the model declines (stop_reason "refusal").
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
        except self.anthropic.RateLimitError as e:
            log.warning("Claude rate limited, skipping: %s", e)
            return None
        except self.anthropic.APIStatusError as e:
            log.warning("Claude API error %s, skipping", e.status_code)
            return None
        except self.anthropic.APIConnectionError as e:
            log.warning("Claude connection error, skipping: %s", e)
            return None
        if resp.stop_reason != "end_turn":
            log.warning("Claude stop_reason=%s for %s", resp.stop_reason, listing.key)
            return None
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            log.warning("Could not parse Claude output for %s", listing.key)
            return None


def apply_extraction(listing: Listing, x: dict, cfg: dict) -> Listing:
    """Fill listing fields that are still unknown from Claude's extraction (structured data wins)."""
    if not x:
        return listing
    city_ids = {c["name"]: c["id"] for c in cfg["search"]["cities"]}

    def fill(attr, value):
        if value not in (None, "", "unknown") and getattr(listing, attr) in (None, ""):
            setattr(listing, attr, value)

    fill("price", x.get("price"))
    fill("rooms", x.get("rooms"))
    fill("sqm", x.get("sqm"))
    fill("neighborhood", x.get("neighborhood"))
    fill("street", x.get("street"))
    fill("house_number", str(x["house_number"]) if x.get("house_number") else None)
    fill("floor", x.get("floor"))
    fill("entrance_date", x.get("entrance_date"))
    fill("phone", x.get("phone"))
    if x.get("city") and not listing.city:
        listing.city = x["city"]
        listing.city_id = city_ids.get(x["city"], -1)
    if listing.elevator is None and x.get("elevator") in ("yes", "no"):
        listing.elevator = x["elevator"] == "yes"
    parking = x.get("parking")
    if parking == "private":
        listing.parking, listing.parking_type = True, "private"
    elif parking in ("street_easy", "street_hard", "none") and listing.parking_type is None:
        listing.parking_type = parking
    if listing.is_broker is None and x.get("broker_fee") in ("yes", "no"):
        listing.is_broker = x["broker_fee"] == "yes"
    listing.is_rental_offer = x.get("is_rental_offer")
    return listing
