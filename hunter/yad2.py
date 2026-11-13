"""Yad2 source: uses the public JSON gateway that the yad2 website itself calls.

Be gentle: few requests, random delays. If yad2 answers with its bot-protection
page we stop the run (Yad2Blocked) instead of trying to get around it.
"""
from __future__ import annotations

import logging
import random
import time
from typing import Iterator, Optional

import requests

from .models import Listing

log = logging.getLogger(__name__)

FEED_URL = "https://gw.yad2.co.il/realestate-feed/rent/map"
ITEM_URL = "https://gw.yad2.co.il/realestate-item/{token}"
PUBLIC_URL = "https://www.yad2.co.il/realestate/item/{token}"
FEED_CAP = 200  # the map feed returns at most this many markers per query
MIN_BAND = 200  # don't split price bands narrower than this


class Yad2Blocked(RuntimeError):
    """Yad2 answered with a captcha / bot-protection page."""


class Yad2Client:
    def __init__(self, delay_seconds=(4, 9), backoff=(45, 120), session: Optional[requests.Session] = None):
        self.delay = tuple(delay_seconds)
        self.backoff = list(backoff)
        self.session = session or requests.Session()
        self.session.headers.update({"Accept": "application/json", "Accept-Language": "he-IL,he;q=0.9"})
        self._last = 0.0
        self.requests_made = 0
        self.blocked: Optional[str] = None  # set once yad2 blocks us; no more requests this run

    # ---------- HTTP ----------
    def _wait(self):
        wait = random.uniform(*self.delay) - (time.time() - self._last)
        if self._last and wait > 0:
            time.sleep(wait)

    def _get_json(self, url: str, params: Optional[dict] = None) -> dict:
        """GET JSON. On a bot-protection answer, back off and retry a couple of times, then give up."""
        if self.blocked:
            raise Yad2Blocked(self.blocked)
        for attempt, backoff in enumerate(self.backoff + [None]):
            self._wait()
            self._last = time.time()
            self.requests_made += 1
            resp = self.session.get(url, params=params, timeout=30, allow_redirects=False)
            ctype = resp.headers.get("Content-Type", "")
            if resp.status_code not in (301, 302, 403, 429) and "json" in ctype:
                resp.raise_for_status()
                return resp.json()
            if backoff is None:
                self.blocked = f"yad2 returned HTTP {resp.status_code} ({ctype}) for {resp.url}"
                raise Yad2Blocked(self.blocked)
            log.warning("yad2 bot-check (HTTP %s), backing off %ss", resp.status_code, backoff)
            time.sleep(backoff)
        raise AssertionError("unreachable")

    # ---------- feed ----------
    def search(self, region: int, city_id: int, min_rooms: float, max_price: int,
               min_price: int = 0) -> list[dict]:
        """Return all feed markers for a city, splitting price bands when the 200 cap is hit."""
        params = {"region": region, "city": city_id, "minRooms": min_rooms, "maxPrice": max_price}
        if min_price:
            params["minPrice"] = min_price
        data = self._get_json(FEED_URL, params).get("data", {}) or {}
        markers = data.get("markers", []) or []
        log.info("yad2 city=%s price %s-%s -> %d markers", city_id, min_price, max_price, len(markers))
        if len(markers) >= FEED_CAP and max_price - min_price > MIN_BAND:
            mid = (min_price + max_price) // 2
            try:
                low = self.search(region, city_id, min_rooms, mid, min_price)
                high = self.search(region, city_id, min_rooms, max_price, mid + 1)
            except Yad2Blocked:
                log.warning("blocked while splitting price bands - keeping the %d markers we have", len(markers))
                return markers
            seen, merged = set(), []
            for m in markers + low + high:
                if m.get("token") not in seen:
                    seen.add(m.get("token"))
                    merged.append(m)
            return merged
        return markers

    def iter_listings(self, region: int, cities: list[dict], min_rooms: float, max_price: int,
                      min_price: int = 0) -> Iterator[Listing]:
        for city in cities:
            if self.blocked:
                return
            try:
                markers = self.search(region, city["id"], min_rooms, max_price, min_price)
            except Yad2Blocked:
                return
            for marker in markers:
                if marker.get("token"):
                    yield listing_from_marker(marker)

    # ---------- item details ----------
    def fetch_details(self, listing: Listing) -> Listing:
        data = self._get_json(ITEM_URL.format(token=listing.source_id)).get("data") or {}
        return merge_item_details(listing, data)


# ---------- parsing (pure functions, unit-tested) ----------
def _g(d: dict, *path, default=None):
    for p in path:
        if not isinstance(d, dict):
            return default
        d = d.get(p)
        if d is None:
            return default
    return d


def _int(v) -> Optional[int]:
    try:
        return int(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def listing_from_marker(m: dict) -> Listing:
    addr = m.get("address") or {}
    details = m.get("additionalDetails") or {}
    tags = {t.get("name") for t in (m.get("tags") or [])}
    price = _int(m.get("price"))
    return Listing(
        source="yad2",
        source_id=m["token"],
        url=PUBLIC_URL.format(token=m["token"]),
        price=price if price else None,
        rooms=details.get("roomsCount"),
        sqm=_int(details.get("squareMeter")),
        floor=_int(_g(addr, "house", "floor")),
        city=_g(addr, "city", "text", default=""),
        city_id=_int(_g(addr, "city", "id")),
        neighborhood=_g(addr, "neighborhood", "text", default=""),
        neighborhood_id=_int(_g(addr, "neighborhood", "id")),
        street=_g(addr, "street", "text", default=""),
        house_number=str(_g(addr, "house", "number")) if _g(addr, "house", "number") else None,
        property_type=_g(details, "property", "text", default=""),
        is_broker=(m.get("adType") == "commercial") if m.get("adType") else None,
        agency_name=_g(m, "customer", "agencyName", default="") or "",
        parking=True if "חניה" in tags else None,
        parking_type="private" if "חניה" in tags else None,
        elevator=True if "מעלית" in tags else None,
        images=list(_g(m, "metaData", "images", default=[]) or [])[:10],
        created_at=_g(m, "dates", "createdAt", default="") or "",
    )


def merge_item_details(listing: Listing, d: dict) -> Listing:
    if not d:
        return listing
    details = d.get("additionalDetails") or {}
    inprop = d.get("inProperty") or {}
    addr = d.get("address") or {}
    meta = d.get("metaData") or {}

    def flag(key):
        return inprop[key] if isinstance(inprop.get(key), bool) else None

    pets = None
    for k, v in inprop.items():
        if "pet" in k.lower() and isinstance(v, bool):
            pets = v
    desc_parts = [meta.get("description") or "", d.get("furnitureInfo") or ""]
    listing.description = "\n".join(p.strip() for p in desc_parts if p and p.strip())
    if d.get("price"):
        listing.price = _int(d.get("price"))
    listing.rooms = details.get("roomsCount", listing.rooms)
    listing.sqm = _int(details.get("squareMeter")) or listing.sqm
    listing.top_floor = _int(details.get("buildingTopFloor"))
    floor = _int(_g(addr, "house", "floor"))
    listing.floor = floor if floor is not None else listing.floor
    listing.elevator = flag("includeElevator") if flag("includeElevator") is not None else listing.elevator
    parking = flag("includeParking")
    if parking is None and details.get("parkingSpacesCount") is not None:
        parking = details.get("parkingSpacesCount", 0) > 0
    listing.parking = parking if parking is not None else listing.parking
    if listing.parking:
        listing.parking_type = "private"
    listing.balcony = flag("includeBalcony")
    listing.mamad = flag("includeSecurityRoom")
    listing.furnished = flag("includeFurniture")
    listing.pets = pets
    listing.entrance_date = (details.get("entranceDate") or "")[:10]
    if d.get("adType"):
        listing.is_broker = d["adType"] == "commercial"
    listing.created_at = _g(d, "dates", "createdAt", default=listing.created_at) or listing.created_at
    if meta.get("images"):
        listing.images = list(meta["images"])[:10]
    listing.details_fetched = True
    return listing
