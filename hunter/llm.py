"""Optional: let Claude read the free-text description (pets, kitchen, broker fee, roommates)."""
from __future__ import annotations

import json
import logging
import os
from typing import Optional

from .models import Listing

log = logging.getLogger(__name__)

SYSTEM = (
    "You read Israeli rental apartment ads (usually Hebrew) for a tenant and extract facts. "
    "Only report what the ad text actually says; use 'unknown' when it is not mentioned. "
    "kitchen: 'small' if described as small/kitchenette, 'large' if described as big/spacious, "
    "'normal' if mentioned without size, else 'unknown'. "
    "broker_fee: 'yes' if the ad says a brokerage fee applies or it is posted by an agency, "
    "'no' if it says no brokerage, else 'unknown'. "
    "roommates_ad: true only if this is a room in a shared apartment / looking for a roommate. "
    "red_flags: short Hebrew notes on things a tenant should know (e.g. short contract, renovation, "
    "no fridge, ground-floor noise, required guarantees). At most 3. "
    "summary: one short Hebrew sentence describing the apartment."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "pets": {"type": "string", "enum": ["allowed", "not_allowed", "unknown"]},
        "kitchen": {"type": "string", "enum": ["small", "normal", "large", "unknown"]},
        "broker_fee": {"type": "string", "enum": ["yes", "no", "unknown"]},
        "roommates_ad": {"type": "boolean"},
        "red_flags": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
    },
    "required": ["pets", "kitchen", "broker_fee", "roommates_ad", "red_flags", "summary"],
    "additionalProperties": False,
}


class LLMAnalyzer:
    def __init__(self, model: str, effort: str = "low"):
        import anthropic

        self.client = anthropic.Anthropic()
        self.anthropic = anthropic
        self.model = model
        self.effort = effort

    @classmethod
    def from_config(cls, cfg: dict) -> Optional["LLMAnalyzer"]:
        llm_cfg = cfg.get("llm") or {}
        if not llm_cfg.get("enabled") or not os.environ.get("ANTHROPIC_API_KEY"):
            return None
        return cls(llm_cfg.get("model", "claude-opus-5-5"), llm_cfg.get("effort", "low"))

    def analyze(self, listing: Listing) -> Optional[dict]:
        if not listing.description.strip():
            return None
        ad = (
            f"סוג: {listing.property_type}, {listing.rooms} חדרים, {listing.sqm or '?'} מ\"ר, "
            f"קומה {listing.floor}, מחיר {listing.price}\n"
            f"כתובת: {listing.address}\n\nטקסט המודעה:\n{listing.description}"
        )
        kwargs = dict(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM,
            messages=[{"role": "user", "content": ad}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        try:
            if self.model.startswith("claude-haiku"):
                resp = self.client.messages.create(**kwargs)
            else:
                # Server-side fallback if the model declines (stop_reason "refusal").
                resp = self.client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
                )
        except self.anthropic.RateLimitError as e:
            log.warning("Claude rate limited, skipping LLM analysis: %s", e)
            return None
        except self.anthropic.APIStatusError as e:
            log.warning("Claude API error %s, skipping LLM analysis", e.status_code)
            return None
        except self.anthropic.APIConnectionError as e:
            log.warning("Claude connection error, skipping LLM analysis: %s", e)
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
