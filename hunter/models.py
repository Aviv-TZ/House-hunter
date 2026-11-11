from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class Listing:
    source: str
    source_id: str
    url: str
    price: Optional[int] = None
    rooms: Optional[float] = None
    sqm: Optional[int] = None
    floor: Optional[int] = None
    top_floor: Optional[int] = None
    city: str = ""
    city_id: Optional[int] = None
    neighborhood: str = ""
    neighborhood_id: Optional[int] = None
    street: str = ""
    house_number: Optional[str] = None
    property_type: str = ""
    is_broker: Optional[bool] = None
    agency_name: str = ""
    elevator: Optional[bool] = None
    parking: Optional[bool] = None
    # private / street_easy / street_hard / none  (None = not mentioned)
    parking_type: Optional[str] = None
    balcony: Optional[bool] = None
    mamad: Optional[bool] = None
    furnished: Optional[bool] = None
    pets: Optional[bool] = None
    entrance_date: str = ""
    description: str = ""
    images: list = field(default_factory=list)
    created_at: str = ""
    details_fetched: bool = False
    # free-text sources (facebook)
    phone: str = ""
    source_label: str = ""      # group name / "Marketplace"
    is_rental_offer: Optional[bool] = None

    @property
    def key(self) -> str:
        return f"{self.source}:{self.source_id}"

    @property
    def address(self) -> str:
        parts = [self.street + (f" {self.house_number}" if self.house_number else ""), self.neighborhood, self.city]
        return ", ".join(p.strip() for p in parts if p and p.strip())

    @property
    def source_name(self) -> str:
        names = {"yad2": "יד 2", "fb_group": "פייסבוק", "fb_market": "Marketplace"}
        base = names.get(self.source, self.source)
        return f"{base} · {self.source_label}" if self.source_label and self.source == "fb_group" else base

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Listing":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)
