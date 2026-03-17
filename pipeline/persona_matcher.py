"""myHQ GTM Engine — 3-persona matching system.

Persona 1: The Funded Founder (Seed → Series A, 5-50 employees)
Persona 2: The Ops Expander (50-300 employees, hiring in new city)
Persona 3: The Enterprise Expander (300+ employees, entering new city)
"""

from __future__ import annotations

import logging
import re

from config.settings import PERSONAS

logger = logging.getLogger(__name__)


class PersonaMatcher:
    """Matches leads to buyer personas based on signal data and company attributes."""

    PERSONA_CONFIG = {
        1: {
            "name": "The Funded Founder",
            "title_keywords": ["founder", "co-founder", "cofounder", "ceo", "cto", "chief executive", "chief technology"],
            "signal_keywords": ["raised", "funding", "seed", "series-a", "series a", "pre-seed", "startup", "team growing", "new hire", "venture", "investor"],
            "company_size_range": (1, 50),
            "signal_types": ["funding", "intent"],
            "product_fit": ["Fixed Desks", "Private Cabins", "Managed Office (10-30 seats)"],
            "urgency": "HIGH",
            "contact_window_days": 2,
            "sdr_angle": "Congratulations on the raise — most founders we work with need a real office within 60 days. We can have you set up in a week.",
        },
        2: {
            "name": "The Ops Expander",
            "title_keywords": ["operations", "admin", "facilities", "office manager", "procurement", "vendor management", "hr manager", "people ops", "workplace"],
            "signal_keywords": ["operations", "admin", "facilities", "office", "workspace", "team", "gst", "invoice", "vendor", "procurement", "hiring", "jobs"],
            "company_size_range": (50, 300),
            "signal_types": ["hiring", "intent"],
            "product_fit": ["Managed Office (30-100 seats)", "Fixed Desks"],
            "urgency": "MEDIUM",
            "contact_window_days": 7,
            "sdr_angle": "We handle everything — shortlisting, site visits, documentation, GST invoicing. You present one vetted recommendation to your leadership.",
        },
        3: {
            "name": "The Enterprise Expander",
            "title_keywords": ["vp", "vice president", "director", "country manager", "regional head", "head of", "general manager", "managing director"],
            "signal_keywords": ["expansion", "new city", "satellite", "hub", "compliance", "legal", "enterprise", "pan-india", "new office", "mca", "gst registration", "subsidiary"],
            "company_size_range": (300, 100000),
            "signal_types": ["expansion", "intent"],
            "product_fit": ["Managed Office (100+ seats)", "Commercial Leasing"],
            "urgency": "LOW-MEDIUM",
            "contact_window_days": 14,
            "sdr_angle": "We work with JLL and CBRE-level clients. Full compliance documentation, dedicated account manager, references from similar companies.",
        },
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    # ── Public API ──────────────────────────────────────────────────

    def match(self, lead: dict) -> dict:
        """Match a lead to a persona. Returns lead with persona_id and details."""
        scores = {pid: self._calculate_match_score(lead, cfg) for pid, cfg in self.PERSONA_CONFIG.items()}
        best = max(scores, key=scores.get)
        lead["persona_id"] = best
        lead["persona_name"] = self.PERSONA_CONFIG[best]["name"]
        lead["persona_details"] = self.PERSONA_CONFIG[best]
        lead["persona_match_scores"] = scores
        return lead

    def match_batch(self, leads: list[dict]) -> list[dict]:
        return [self.match(l) for l in leads]

    # ── Scoring ─────────────────────────────────────────────────────

    def _calculate_match_score(self, lead: dict, cfg: dict) -> float:
        score = 0.0

        # 1. Signal type match (0-30)
        signal_type = lead.get("signal_type", "")
        if signal_type in cfg["signal_types"]:
            score += 30.0

        # 2. Company size match (0-25)
        size = lead.get("company_size") or lead.get("company_size_est") or lead.get("employee_count_est") or 0
        lo, hi = cfg["company_size_range"]
        if lo <= size <= hi:
            score += 25.0
        elif size > 0:
            # Partial credit for close matches
            if size < lo:
                dist = lo - size
                score += max(0, 15.0 - dist * 0.5)
            else:
                dist = size - hi
                score += max(0, 15.0 - dist * 0.02)

        # 3. Title keyword match (0-25)
        title = (lead.get("contact_title") or "").lower()
        if title:
            matches = sum(1 for kw in cfg["title_keywords"] if kw in title)
            score += min(25.0, matches * 12.5)

        # 4. Content keyword match (0-20)
        searchable = " ".join([
            lead.get("sdr_notes", ""),
            str(lead.get("raw_data", "")),
            lead.get("content_snippet", ""),
            lead.get("sector", ""),
        ]).lower()
        if searchable:
            matches = sum(1 for kw in cfg["signal_keywords"] if kw in searchable)
            score += min(20.0, matches * 4.0)

        return score

    # ── Helpers ─────────────────────────────────────────────────────

    def get_persona_name(self, persona_id: int) -> str:
        return self.PERSONA_CONFIG.get(persona_id, {}).get("name", "Unknown")

    def get_persona_config(self, persona_id: int) -> dict:
        return self.PERSONA_CONFIG.get(persona_id, {})

    def get_product_recommendation(self, persona_id: int, team_size: int | None = None) -> list[str]:
        cfg = self.PERSONA_CONFIG.get(persona_id, {})
        products = list(cfg.get("product_fit", []))
        if team_size and team_size > 100 and persona_id != 3:
            products.append("Managed Office (100+ seats)")
        return products

    def get_sdr_angle(self, persona_id: int) -> str:
        return self.PERSONA_CONFIG.get(persona_id, {}).get("sdr_angle", "")

    def get_contact_window(self, persona_id: int) -> int:
        return self.PERSONA_CONFIG.get(persona_id, {}).get("contact_window_days", 7)


# ── Module entry point ──────────────────────────────────────────────


def match_personas(leads: list[dict]) -> list[dict]:
    """Entry point for persona matching."""
    matcher = PersonaMatcher()
    return matcher.match_batch(leads)
