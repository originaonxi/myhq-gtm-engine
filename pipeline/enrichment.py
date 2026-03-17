"""myHQ GTM Engine — Lead enrichment pipeline.

For every high-intent signal, build a complete lead profile with company data,
decision-maker contact, funding history, news, competitor intelligence, and WhatsApp.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime

from config.settings import DRY_RUN, PERSONAS
from pipeline.utils import (
    IST,
    apollo_enrich,
    apollo_find_person,
    batch_upsert_to_supabase,
    format_phone_india,
    generate_dedup_hash,
    is_valid_indian_mobile,
    safe_get,
    scraperapi_fetch,
    serpapi_search,
)

logger = logging.getLogger(__name__)


class LeadEnricher:
    """Enriches raw signals into complete lead profiles for SDR outreach."""

    DECISION_MAKER_TITLES: dict[int, list[str]] = {
        1: ["Founder", "Co-founder", "CEO", "CTO"],
        2: ["Operations Manager", "Admin Manager", "Facilities Manager", "Office Manager", "HR Manager"],
        3: ["VP Sales", "Director BD", "Sales Head", "VP Business Development", "Country Manager"],
    }

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    def enrich_signals(self, signals: list[dict], signal_type: str) -> list[dict]:
        """Enrich a batch of signals into full lead profiles."""
        leads: list[dict] = []
        for signal in signals:
            try:
                lead = self.enrich_single(signal, signal_type)
                if lead:
                    leads.append(lead)
            except Exception as exc:
                logger.error("Failed to enrich %s: %s", signal.get("company_name"), exc)
        logger.info("Enriched %d/%d %s signals into leads", len(leads), len(signals), signal_type)
        return leads

    def enrich_single(self, signal: dict, signal_type: str) -> dict | None:
        """Enrich a single signal into a lead profile."""
        company_name = signal.get("company_name", "") or signal.get("company_mention", "")
        if not company_name:
            # Intent signals may not have company — use username / content as identifier
            if signal_type == "intent" and (signal.get("username") or signal.get("content_snippet")):
                company_name = signal.get("username") or "Unknown (Intent Lead)"
                signal["company_name"] = company_name
            else:
                return None

        if self.dry_run:
            return self._generate_synthetic_enrichment(signal, signal_type)

        persona_id = self._determine_persona(signal, signal_type)
        website = signal.get("company_website", "")
        domain = website.replace("https://", "").replace("http://", "").split("/")[0] if website else None

        company_data = self._enrich_company(company_name, website)
        contact_data = self._find_decision_maker(company_name, persona_id, domain)
        funding_data = self._enrich_funding_history(company_name)
        news_data = self._enrich_news(company_name)
        competitor_data = self._detect_competitor_workspace(company_name)
        whatsapp_data = self._detect_whatsapp(contact_data.get("phone"), website)

        return self._build_lead_record(
            signal, signal_type, company_data, contact_data,
            funding_data, news_data, competitor_data, whatsapp_data, persona_id,
        )

    # ── Enrichment steps ────────────────────────────────────────────

    def _enrich_company(self, company_name: str, website: str | None = None) -> dict:
        domain = website.replace("https://", "").replace("http://", "").split("/")[0] if website else None
        data = apollo_enrich(company_name, domain)
        org = safe_get(data, "organizations", default=[{}])
        org = org[0] if org else {}
        return {
            "employee_count": safe_get(org, "estimated_num_employees"),
            "revenue_est": safe_get(org, "annual_revenue_printed"),
            "industry": safe_get(org, "industry"),
            "linkedin_url": safe_get(org, "linkedin_url"),
            "website": safe_get(org, "website_url") or website,
            "phone": safe_get(org, "phone"),
        }

    def _find_decision_maker(self, company_name: str, persona_id: int, domain: str | None = None) -> dict:
        titles = self.DECISION_MAKER_TITLES.get(persona_id, ["Manager"])
        if not domain:
            return {}
        data = apollo_find_person(domain, titles)
        people = safe_get(data, "people", default=[])
        if not people:
            return {}
        p = people[0]
        return {
            "name": safe_get(p, "name", default=""),
            "title": safe_get(p, "title", default=""),
            "email": safe_get(p, "email", default=""),
            "phone": format_phone_india(safe_get(p, "phone_numbers", default=[{}])[0].get("sanitized_number", "") if safe_get(p, "phone_numbers") else ""),
            "linkedin": safe_get(p, "linkedin_url", default=""),
        }

    def _enrich_funding_history(self, company_name: str) -> dict:
        data = serpapi_search(f"{company_name} funding history site:crunchbase.com OR site:tracxn.com", num=3)
        results = safe_get(data, "organic_results", default=[])
        snippet = results[0].get("snippet", "") if results else ""
        return {"funding_snippet": snippet, "source": "crunchbase/tracxn"}

    def _enrich_news(self, company_name: str) -> dict:
        data = serpapi_search(f"{company_name} India 2026", tbm="nws", num=5, tbs="qdr:m3")
        articles = safe_get(data, "news_results", default=[])
        return {
            "recent_news": [{"title": a.get("title", ""), "source": a.get("source", "")} for a in articles[:3]],
        }

    def _detect_competitor_workspace(self, company_name: str) -> dict:
        data = serpapi_search(f'"{company_name}" coworking OR "office space" India', num=5)
        results = safe_get(data, "organic_results", default=[])
        text = " ".join(r.get("snippet", "") for r in results).lower()
        competitors = ["wework", "awfis", "indiqube", "smartworks", "regus", "91springboard", "cowrks", "innov8"]
        for comp in competitors:
            if comp in text:
                return {"current_workspace": comp.title(), "is_switching_opportunity": True}
        return {"current_workspace": "unknown", "is_switching_opportunity": False}

    def _detect_whatsapp(self, phone: str | None, website: str | None) -> dict:
        normalised = format_phone_india(phone) if phone else ""
        has_wa = is_valid_indian_mobile(normalised)
        return {"has_whatsapp": has_wa, "whatsapp_number": normalised if has_wa else ""}

    def _determine_persona(self, signal: dict, signal_type: str) -> int:
        if signal_type == "funding":
            return 1
        if signal_type == "hiring":
            return 2
        if signal_type == "expansion":
            return 3
        # Intent — guess from size / content
        size = signal.get("company_size") or signal.get("company_size_est") or 0
        if size > 300:
            return 3
        if size > 50:
            return 2
        return 1

    def _generate_sdr_notes(self, lead: dict) -> str:
        parts: list[str] = []
        signal_type = lead.get("signal_type", "")
        if signal_type == "funding":
            parts.append(f"🎯 {lead.get('company_name')} just raised {lead.get('company_last_funding_amount', 'undisclosed')}")
            if lead.get("company_investors"):
                parts.append(f"Investors: {', '.join(lead['company_investors'][:3])}")
        elif signal_type == "hiring":
            parts.append(f"🎯 {lead.get('company_name')} is hiring aggressively in {lead.get('city', 'unknown city')}")
            parts.append(f"New jobs this week: {lead.get('delta', '?')}")
        elif signal_type == "expansion":
            parts.append(f"🎯 {lead.get('company_name')} is expanding to {lead.get('city', 'new city')}")
        elif signal_type == "intent":
            parts.append(f"🎯 Active workspace search detected on {lead.get('source', 'social media')}")

        if lead.get("current_workspace") and lead["current_workspace"] != "unknown":
            parts.append(f"Currently at: {lead['current_workspace']} — switching opportunity")
        if lead.get("company_size"):
            parts.append(f"Team size: ~{lead['company_size']} people")
        return " | ".join(parts)

    def _build_lead_record(self, signal: dict, signal_type: str, company: dict,
                           contact: dict, funding: dict, news: dict,
                           competitor: dict, whatsapp: dict, persona_id: int) -> dict:
        city = signal.get("city") or signal.get("city_entering", "")
        lead = {
            "signal_id": signal.get("id"),
            "signal_type": signal_type,
            "persona_id": persona_id,
            "company_name": signal.get("company_name", ""),
            "company_size": company.get("employee_count") or signal.get("employee_count_est") or signal.get("company_size_est"),
            "company_revenue": company.get("revenue_est", ""),
            "company_funding_total": signal.get("amount_raised", ""),
            "company_last_funding_date": signal.get("announcement_date"),
            "company_last_funding_amount": signal.get("amount_raised", ""),
            "company_investors": signal.get("investor_names", []),
            "contact_name": contact.get("name") or signal.get("founder_name") or signal.get("contact_name", ""),
            "contact_title": contact.get("title") or signal.get("contact_title", ""),
            "contact_email": contact.get("email", ""),
            "contact_phone": contact.get("phone", ""),
            "contact_whatsapp": whatsapp.get("whatsapp_number", ""),
            "contact_linkedin": contact.get("linkedin") or signal.get("founder_linkedin", ""),
            "company_linkedin": company.get("linkedin_url") or signal.get("linkedin_company_url", ""),
            "company_website": company.get("website") or signal.get("company_website", ""),
            "city": city,
            "current_workspace": competitor.get("current_workspace", "unknown"),
            "pain_points": [],
            "intent_score": 0,
            "enrichment_score": self._calc_enrichment_score(contact, company, whatsapp),
            "tier": None,
            "sdr_notes": "",
            "dedup_hash": generate_dedup_hash(signal.get("company_name", ""), signal_type, city),
            # Pass through for scoring
            "announcement_date": signal.get("announcement_date"),
            "delta": signal.get("delta"),
            "urgency_level": signal.get("urgency_level"),
            "source": signal.get("source", ""),
            "sector": signal.get("sector", ""),
            "employee_count_est": signal.get("employee_count_est"),
            "company_size_est": signal.get("company_size_est"),
        }
        lead["sdr_notes"] = self._generate_sdr_notes(lead)
        return lead

    def _calc_enrichment_score(self, contact: dict, company: dict, whatsapp: dict) -> int:
        score = 0
        if contact.get("name"):
            score += 20
        if contact.get("email"):
            score += 20
        if contact.get("phone"):
            score += 20
        if whatsapp.get("has_whatsapp"):
            score += 20
        if company.get("employee_count"):
            score += 10
        if company.get("linkedin_url"):
            score += 10
        return min(100, score)

    def _store_leads(self, leads: list[dict]) -> int:
        if self.dry_run:
            return len(leads)
        return batch_upsert_to_supabase("leads", leads)

    # ── Synthetic enrichment for dry run ────────────────────────────

    def _generate_synthetic_enrichment(self, signal: dict, signal_type: str) -> dict:
        """Generate realistic enrichment data without API calls."""
        persona_id = self._determine_persona(signal, signal_type)
        company_name = signal.get("company_name", "Unknown")
        city = signal.get("city") or signal.get("city_entering", "BLR")

        # Synthetic contact based on persona
        contacts_by_persona = {
            1: [
                {"name": signal.get("founder_name", "Arjun Mehta"), "title": "Founder & CEO", "email": f"founder@{company_name.lower().replace(' ', '')}.com", "phone": "+919876543210", "linkedin": signal.get("founder_linkedin", f"linkedin.com/in/{company_name.lower().replace(' ', '-')}-founder")},
            ],
            2: [
                {"name": signal.get("contact_name") or "Priya Sharma", "title": "Operations Manager", "email": f"ops@{company_name.lower().replace(' ', '')}.com", "phone": "+919812345678", "linkedin": f"linkedin.com/in/priya-ops-{company_name.lower().replace(' ', '')}"},
            ],
            3: [
                {"name": signal.get("contact_name") or "Rajesh Kumar", "title": "VP Business Development", "email": f"rajesh@{company_name.lower().replace(' ', '')}.com", "phone": "+919898765432", "linkedin": signal.get("contact_linkedin", f"linkedin.com/in/rajesh-vp-bd")},
            ],
        }
        contact = random.choice(contacts_by_persona.get(persona_id, contacts_by_persona[1]))
        phone = format_phone_india(contact["phone"])
        has_wa = is_valid_indian_mobile(phone)

        competitors = ["WeWork", "Awfis", "unknown", "unknown", "unknown", "IndiQube", "unknown"]
        workspace = random.choice(competitors)

        lead = {
            "signal_id": None,
            "signal_type": signal_type,
            "persona_id": persona_id,
            "company_name": company_name,
            "company_size": signal.get("employee_count_est") or signal.get("company_size_est") or signal.get("employee_count") or random.randint(10, 200),
            "company_revenue": "",
            "company_funding_total": signal.get("amount_raised", ""),
            "company_last_funding_date": signal.get("announcement_date"),
            "company_last_funding_amount": signal.get("amount_raised", ""),
            "company_investors": signal.get("investor_names", []),
            "contact_name": contact["name"],
            "contact_title": contact["title"],
            "contact_email": contact["email"],
            "contact_phone": phone,
            "contact_whatsapp": phone if has_wa else "",
            "contact_linkedin": contact["linkedin"],
            "company_linkedin": signal.get("linkedin_company_url") or signal.get("company_linkedin_url", ""),
            "company_website": signal.get("company_website", ""),
            "city": city,
            "current_workspace": workspace,
            "pain_points": [],
            "intent_score": 0,
            "enrichment_score": 80 if has_wa else 60,
            "tier": None,
            "sdr_notes": "",
            "dedup_hash": generate_dedup_hash(company_name, signal_type, city),
            # Pass through for scoring
            "announcement_date": signal.get("announcement_date"),
            "created_at": signal.get("created_at"),
            "delta": signal.get("delta"),
            "urgency_level": signal.get("urgency_level"),
            "source": signal.get("source", ""),
            "sector": signal.get("sector", ""),
            "employee_count_est": signal.get("employee_count_est"),
            "company_size_est": signal.get("company_size_est"),
        }
        lead["sdr_notes"] = self._generate_sdr_notes(lead)
        return lead


# ── Module entry point ──────────────────────────────────────────────


def enrich_signals(signals: list[dict], signal_type: str, dry_run: bool = False) -> list[dict]:
    """Entry point for lead enrichment."""
    enricher = LeadEnricher(dry_run=dry_run)
    return enricher.enrich_signals(signals, signal_type)
