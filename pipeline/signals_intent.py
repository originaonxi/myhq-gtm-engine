"""myHQ GTM Engine — Real-time workspace search intent signals.

People actively searching for office space RIGHT NOW on Reddit, Twitter,
LinkedIn, Google Trends, and IndiaMart are the warmest leads.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta

from config.settings import CITIES, DRY_RUN
from pipeline.utils import (
    IST,
    batch_upsert_to_supabase,
    generate_dedup_hash,
    resolve_city_code,
    safe_get,
    scraperapi_fetch,
    serpapi_search,
)

logger = logging.getLogger(__name__)


class IntentSignalCollector:
    """Detects real-time workspace search intent from social and B2B platforms."""

    URGENCY_HIGH = ["immediately", "urgent", "asap", "today", "this week", "right now", "desperately"]
    URGENCY_MEDIUM = ["looking for", "need", "searching", "any recommendations", "suggest", "help me find"]
    URGENCY_LOW = ["thinking about", "planning", "eventually", "next quarter", "considering", "exploring"]

    def __init__(self, dry_run: bool = False, target_cities: list[str] | None = None):
        self.dry_run = dry_run
        self.target_cities = target_cities or list(CITIES.keys())

    def collect_all(self) -> list[dict]:
        if self.dry_run:
            signals = self._generate_synthetic_data()
            logger.info("[DRY RUN] Generated %d synthetic intent signals", len(signals))
            return signals

        raw: list[dict] = []
        collectors = [
            ("Reddit India", self._collect_reddit),
            ("Twitter/X", self._collect_twitter),
            ("LinkedIn Posts", self._collect_linkedin_posts),
            ("Google Trends", self._collect_google_trends),
            ("IndiaMart", self._collect_indiamart),
        ]
        for name, fn in collectors:
            try:
                results = fn()
                logger.info("[%s] Collected %d intent signals", name, len(results))
                raw.extend(results)
            except Exception as exc:
                logger.error("[%s] Failed: %s", name, exc)

        signals = self._deduplicate(raw)
        stored = self._store(signals)
        logger.info("Intent signals: %d (stored %d)", len(signals), stored)
        return signals

    # ── Collectors ──────────────────────────────────────────────────

    def _collect_reddit(self) -> list[dict]:
        signals: list[dict] = []
        subreddits = ["india", "bangalore", "mumbai", "delhi", "hyderabad", "pune", "indianstartups", "startups"]
        search_terms = ["office space", "coworking", "managed office", "workspace", "looking for office"]
        for term in search_terms:
            for sub in subreddits:
                data = serpapi_search(
                    f"{term} site:reddit.com/r/{sub}",
                    tbs="qdr:w",
                    num=5,
                )
                for item in safe_get(data, "organic_results", default=[]):
                    s = self._parse_intent_result(item, "reddit", sub)
                    if s:
                        signals.append(s)
        return signals

    def _collect_twitter(self) -> list[dict]:
        signals: list[dict] = []
        queries = [
            '"looking for office space" India',
            '"need office space" OR "need coworking" Bangalore OR Mumbai OR Delhi OR Hyderabad OR Pune',
            '"myhq" OR "awfis" OR "wework india" OR "coworking space"',
        ]
        for q in queries:
            data = serpapi_search(q, tbs="qdr:d2", num=10)
            for item in safe_get(data, "organic_results", default=[]):
                s = self._parse_intent_result(item, "twitter", "")
                if s:
                    signals.append(s)
        return signals

    def _collect_linkedin_posts(self) -> list[dict]:
        signals: list[dict] = []
        queries = [
            '"looking for office space" site:linkedin.com India',
            '"anyone recommend coworking" site:linkedin.com India',
            '"office space recommendations" site:linkedin.com India',
        ]
        for q in queries:
            data = serpapi_search(q, num=10)
            for item in safe_get(data, "organic_results", default=[]):
                s = self._parse_intent_result(item, "linkedin", "")
                if s:
                    signals.append(s)
        return signals

    def _collect_google_trends(self) -> list[dict]:
        signals: list[dict] = []
        keywords_by_city = {
            "BLR": ["coworking space bangalore", "managed office bangalore"],
            "MUM": ["coworking space mumbai", "managed office mumbai"],
            "DEL": ["office space delhi", "coworking space gurgaon"],
            "HYD": ["flexible workspace hyderabad", "coworking hyderabad"],
            "PUN": ["coworking pune", "office space pune"],
        }
        for city_code in self.target_cities:
            for kw in keywords_by_city.get(city_code, []):
                data = serpapi_search(kw, search_type="google_trends")
                interest = safe_get(data, "interest_over_time", "timeline_data", default=[])
                if interest:
                    signals.append(self._normalize_signal({
                        "source": "google_trends",
                        "platform": "google_trends",
                        "content_snippet": f"Trending: '{kw}' — interest data available",
                        "city": city_code,
                        "urgency_level": "medium",
                        "raw_data": {"keyword": kw, "data_points": len(interest)},
                    }))
        return signals

    def _collect_indiamart(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            data = serpapi_search(
                f"office space requirement {city_name} site:indiamart.com",
                num=10,
            )
            for item in safe_get(data, "organic_results", default=[]):
                s = self._parse_intent_result(item, "indiamart", "")
                if s:
                    s["city"] = city_code
                    signals.append(s)
        return signals

    # ── Parsing ─────────────────────────────────────────────────────

    def _parse_intent_result(self, item: dict, platform: str, subreddit: str) -> dict | None:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        content = f"{title} {snippet}"
        if not content.strip():
            return None

        city = self._extract_city_from_content(content)
        urgency = self._assess_urgency(content)

        # Try to extract username
        username = ""
        link = item.get("link", "")
        if "reddit.com" in link and "/u/" in link:
            username = link.split("/u/")[-1].split("/")[0]
        elif "twitter.com" in link or "x.com" in link:
            username = link.split("/")[-1].split("?")[0] if "/" in link else ""

        return self._normalize_signal({
            "source": platform,
            "platform": platform,
            "content_snippet": content[:500],
            "username": username,
            "company_mention": "",
            "city": city or "",
            "urgency_level": urgency,
            "contact_hint": link,
            "raw_data": {"title": title, "snippet": snippet, "link": link, "subreddit": subreddit},
        })

    def _assess_urgency(self, content: str) -> str:
        cl = content.lower()
        if any(kw in cl for kw in self.URGENCY_HIGH):
            return "high"
        if any(kw in cl for kw in self.URGENCY_MEDIUM):
            return "medium"
        return "low"

    def _extract_city_from_content(self, content: str) -> str | None:
        return resolve_city_code(content)

    def _normalize_signal(self, raw: dict) -> dict:
        return {
            "source": raw.get("source", ""),
            "platform": raw.get("platform", ""),
            "content_snippet": raw.get("content_snippet", ""),
            "username": raw.get("username", ""),
            "company_mention": raw.get("company_mention", ""),
            "city": raw.get("city", ""),
            "urgency_level": raw.get("urgency_level", "medium"),
            "contact_hint": raw.get("contact_hint", ""),
            "raw_data": raw.get("raw_data", {}),
            "intent_score": 0,
            "processed": False,
        }

    def _deduplicate(self, signals: list[dict]) -> list[dict]:
        seen: set[str] = set()
        unique: list[dict] = []
        for s in signals:
            h = generate_dedup_hash(s.get("platform", ""), s.get("username", ""), s.get("city", ""), s.get("content_snippet", "")[:50])
            if h not in seen:
                seen.add(h)
                s["dedup_hash"] = h
                unique.append(s)
        return unique

    def _store(self, signals: list[dict]) -> int:
        if self.dry_run:
            return len(signals)
        return batch_upsert_to_supabase("signals_intent", signals)

    # ── Synthetic data ──────────────────────────────────────────────

    def _generate_synthetic_data(self) -> list[dict]:
        now = datetime.now(IST)
        entries = [
            {"platform": "reddit", "username": "startup_founder_blr", "content": "Looking for a 20-seater office in Koramangala, Bangalore. Budget around 50k/seat. Any leads? We're a Series A startup moving out of our co-founder's apartment.", "city": "BLR", "urgency": "high", "company_mention": ""},
            {"platform": "reddit", "username": "ops_manager_mum", "content": "Need a managed office space in BKC Mumbai for 40 people. Should have meeting rooms, good internet, and GST invoicing. Currently in WeWork but looking for better pricing.", "city": "MUM", "urgency": "high", "company_mention": "WeWork"},
            {"platform": "twitter", "username": "rajesh_startup", "content": "Our team is growing to 15, need to move out of the flat. Anyone know good coworking spaces in Indiranagar or HSR Layout? Preferably with 24/7 access.", "city": "BLR", "urgency": "medium", "company_mention": ""},
            {"platform": "linkedin", "username": "priya-facilities-mgr", "content": "Can anyone recommend a managed office space provider in Hyderabad? We're setting up a new engineering hub — need 100+ seats with server room and compliance documentation.", "city": "HYD", "urgency": "high", "company_mention": ""},
            {"platform": "reddit", "username": "delhi_entrepreneur", "content": "Planning to set up our first office in Gurgaon. Team of 8, all developers. Need good internet and quiet space. Any recommendations for Cyber City area?", "city": "DEL", "urgency": "medium", "company_mention": ""},
            {"platform": "twitter", "username": "aisha_ceo", "content": "Just raised our seed round! Now the hard part — finding office space in Bangalore that doesn't cost an arm and a leg. Any founders have recommendations? #startuplife", "city": "BLR", "urgency": "high", "company_mention": ""},
            {"platform": "reddit", "username": "pune_techie", "content": "WeWork is too expensive for our 12-person startup. Any alternatives in Pune, Kharadi or Hinjewadi area? Need dedicated desks, not hot desking.", "city": "PUN", "urgency": "medium", "company_mention": "WeWork"},
            {"platform": "linkedin", "username": "vikram-ops-lead", "content": "We're expanding our operations to Mumbai from Bangalore. Looking for a workspace partner who can handle everything — from shortlisting to setup. Team of 30 initially, scaling to 60 by Q3.", "city": "MUM", "urgency": "high", "company_mention": ""},
            {"platform": "reddit", "username": "fintech_hyd", "content": "Any coworking spaces in Hyderabad HITEC City that offer virtual office + GST registration? We need a billing address urgently for our new entity.", "city": "HYD", "urgency": "high", "company_mention": ""},
            {"platform": "google_trends", "username": "", "content": "Trending: 'coworking space bangalore' — 23% spike this week vs last month", "city": "BLR", "urgency": "medium", "company_mention": ""},
            {"platform": "google_trends", "username": "", "content": "Trending: 'managed office mumbai' — 15% spike, approaching 12-month high", "city": "MUM", "urgency": "medium", "company_mention": ""},
            {"platform": "twitter", "username": "saurabh_cto", "content": "Moved our team from Awfis to our own space but the fit-out is taking forever. Any managed office providers in Delhi NCR who can set us up in under a week?", "city": "DEL", "urgency": "high", "company_mention": "Awfis"},
            {"platform": "indiamart", "username": "buyer_pune_372", "content": "Requirement: Office space for 25 people in Pune, Kharadi. Furnished, AC, parking. Budget: ₹40,000-60,000 per month. Immediate requirement.", "city": "PUN", "urgency": "high", "company_mention": ""},
            {"platform": "reddit", "username": "remote_team_lead", "content": "Our fully remote company is looking for a satellite office in Bangalore for the team members who want to come in. Maybe 10 dedicated desks. Any suggestions for Whitefield?", "city": "BLR", "urgency": "low", "company_mention": ""},
            {"platform": "linkedin", "username": "meera-hr-head", "content": "Setting up a new office in Pune for our 50-person engineering team. Need workspace recommendations — preferably near Hinjewadi IT Park. Must support GST invoicing and have good cafeteria.", "city": "PUN", "urgency": "medium", "company_mention": ""},
            {"platform": "twitter", "username": "deepak_founder", "content": "IndiQube or Smartworks in Hyderabad — which one is better for a 20-person team? Or should we look at other options? DM me!", "city": "HYD", "urgency": "medium", "company_mention": "IndiQube, Smartworks"},
        ]

        signals: list[dict] = []
        for e in entries:
            hours_ago = random.randint(1, 72)
            signal = self._normalize_signal({
                "source": e["platform"],
                "platform": e["platform"],
                "content_snippet": e["content"],
                "username": e["username"],
                "company_mention": e.get("company_mention", ""),
                "city": e["city"],
                "urgency_level": e["urgency"],
                "contact_hint": f"https://{e['platform']}.com/u/{e['username']}" if e["username"] else "",
                "raw_data": {"synthetic": True},
            })
            signal["dedup_hash"] = generate_dedup_hash(e["platform"], e["username"], e["city"], e["content"][:50])
            signals.append(signal)
        return signals


def collect_intent_signals(dry_run: bool = False, cities: list[str] | None = None) -> list[dict]:
    """Entry point for intent signal collection."""
    collector = IntentSignalCollector(dry_run=dry_run, target_cities=cities)
    return collector.collect_all()
