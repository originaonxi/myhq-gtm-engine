"""myHQ GTM Engine — Enterprise expansion signal detection.

Enterprise companies (300+ employees) entering new cities need managed workspace.
GST registration in a new city = they are serious about being there → Persona 3 lead.
Sources: MCA filings, GST portal, press releases, LinkedIn, business news, real estate.
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
    safe_get,
    scraperapi_fetch,
    serpapi_search,
)

logger = logging.getLogger(__name__)


class ExpansionSignalCollector:
    """Detects enterprise city-expansion signals from regulatory & news sources."""

    def __init__(self, dry_run: bool = False, target_cities: list[str] | None = None):
        self.dry_run = dry_run
        self.target_cities = target_cities or list(CITIES.keys())

    def collect_all(self) -> list[dict]:
        if self.dry_run:
            signals = self._generate_synthetic_data()
            logger.info("[DRY RUN] Generated %d synthetic expansion signals", len(signals))
            return signals

        raw: list[dict] = []
        collectors = [
            ("MCA Filings", self._collect_mca_filings),
            ("GST Signals", self._collect_gst_signals),
            ("Press Releases", self._collect_press_releases),
            ("LinkedIn Updates", self._collect_linkedin_updates),
            ("Business News", self._collect_business_news),
            ("Real Estate Signals", self._collect_real_estate_signals),
        ]
        for name, fn in collectors:
            try:
                results = fn()
                logger.info("[%s] Collected %d expansion signals", name, len(results))
                raw.extend(results)
            except Exception as exc:
                logger.error("[%s] Failed: %s", name, exc)

        signals = self._deduplicate(raw)
        stored = self._store(signals)
        logger.info("Expansion signals: %d (stored %d)", len(signals), stored)
        return signals

    # ── Collectors ──────────────────────────────────────────────────

    def _collect_mca_filings(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            for query in [
                f"company filings new subsidiary registration {city_name} India 2026",
                f"expanding to {city_name} new office 2026",
            ]:
                data = serpapi_search(query, num=10)
                for item in safe_get(data, "organic_results", default=[]):
                    s = self._parse_result(item, "mca_filings", city_code)
                    if s:
                        signals.append(s)
        return signals

    def _collect_gst_signals(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            data = serpapi_search(f"GST registration new office {city_name} India 2026", num=10)
            for item in safe_get(data, "organic_results", default=[]):
                s = self._parse_result(item, "gst_portal", city_code)
                if s:
                    signals.append(s)
        return signals

    def _collect_press_releases(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            data = serpapi_search(
                f'"expands to" OR "opens office in" OR "new office" India {city_name} 2026',
                tbm="nws",
                tbs="qdr:m",
                num=15,
            )
            for item in safe_get(data, "news_results", default=[]) or safe_get(data, "organic_results", default=[]):
                s = self._parse_result(item, "press_release", city_code)
                if s:
                    signals.append(s)
        return signals

    def _collect_linkedin_updates(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            data = serpapi_search(
                f"site:linkedin.com/company expanding {city_name} office India",
                num=10,
            )
            for item in safe_get(data, "organic_results", default=[]):
                s = self._parse_result(item, "linkedin_company", city_code)
                if s:
                    signals.append(s)
        return signals

    def _collect_business_news(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            for site in ["economictimes.com", "business-standard.com", "livemint.com"]:
                data = serpapi_search(
                    f"office expansion {city_name} India site:{site}",
                    tbs="qdr:m",
                    num=5,
                )
                for item in safe_get(data, "organic_results", default=[]):
                    s = self._parse_result(item, "business_news", city_code)
                    if s:
                        signals.append(s)
        return signals

    def _collect_real_estate_signals(self) -> list[dict]:
        signals: list[dict] = []
        for city_code in self.target_cities:
            city_name = CITIES[city_code]["name"]
            data = serpapi_search(
                f"JLL OR CBRE OR Colliers office leasing deal {city_name} India 2026",
                tbm="nws",
                num=10,
            )
            for item in safe_get(data, "news_results", default=[]) or safe_get(data, "organic_results", default=[]):
                s = self._parse_result(item, "real_estate_signal", city_code)
                if s:
                    signals.append(s)
        return signals

    # ── Parsing ─────────────────────────────────────────────────────

    def _parse_result(self, item: dict, source: str, city_code: str) -> dict | None:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        if not title:
            return None
        company = title.split(" expands")[0].split(" opens")[0].split(" to ")[0].strip()[:80]
        return self._normalize_signal({
            "source": source,
            "company_name": company,
            "city_entering": city_code,
            "source_url": item.get("link", ""),
            "announcement_date": datetime.now(IST).isoformat(),
            "raw_data": {"title": title, "snippet": snippet},
        })

    def _normalize_signal(self, raw: dict) -> dict:
        return {
            "source": raw.get("source", ""),
            "company_name": raw.get("company_name", "Unknown"),
            "company_size_est": raw.get("company_size_est"),
            "city_entering": raw.get("city_entering", ""),
            "current_cities": raw.get("current_cities", []),
            "announcement_date": raw.get("announcement_date"),
            "source_url": raw.get("source_url", ""),
            "contact_name": raw.get("contact_name", ""),
            "contact_title": raw.get("contact_title", ""),
            "company_website": raw.get("company_website", ""),
            "employee_count": raw.get("employee_count"),
            "raw_data": raw.get("raw_data", {}),
            "intent_score": 0,
            "processed": False,
        }

    def _deduplicate(self, signals: list[dict]) -> list[dict]:
        seen: set[str] = set()
        unique: list[dict] = []
        for s in signals:
            h = generate_dedup_hash(s.get("company_name", ""), s.get("city_entering", ""))
            if h not in seen:
                seen.add(h)
                s["dedup_hash"] = h
                unique.append(s)
        return unique

    def _store(self, signals: list[dict]) -> int:
        if self.dry_run:
            return len(signals)
        return batch_upsert_to_supabase("signals_expansion", signals)

    # ── Synthetic data ──────────────────────────────────────────────

    def _generate_synthetic_data(self) -> list[dict]:
        now = datetime.now(IST)
        entries = [
            {"company_name": "Google India", "city_entering": "HYD", "size": 12000, "employee_count": 12000, "current_cities": ["BLR", "MUM", "DEL"], "source": "press_release", "contact_name": "Pradeep Nair", "contact_title": "VP Engineering India", "website": "google.co.in"},
            {"company_name": "Amazon India", "city_entering": "PUN", "size": 25000, "employee_count": 25000, "current_cities": ["BLR", "HYD", "DEL", "MUM"], "source": "real_estate_signal", "contact_name": "Rajiv Malhotra", "contact_title": "Director Real Estate India", "website": "amazon.in"},
            {"company_name": "Flipkart", "city_entering": "HYD", "size": 18000, "employee_count": 18000, "current_cities": ["BLR", "DEL", "MUM"], "source": "business_news", "contact_name": "Sanjay Mashruwala", "contact_title": "VP Operations", "website": "flipkart.com"},
            {"company_name": "Wipro", "city_entering": "PUN", "size": 250000, "employee_count": 250000, "current_cities": ["BLR", "HYD", "DEL", "MUM"], "source": "mca_filings", "contact_name": "Anita Verma", "contact_title": "VP Facilities", "website": "wipro.com"},
            {"company_name": "HCL Technologies", "city_entering": "MUM", "size": 220000, "employee_count": 220000, "current_cities": ["DEL", "BLR", "HYD"], "source": "gst_portal", "contact_name": "Suresh Iyer", "contact_title": "Director Administration", "website": "hcltech.com"},
            {"company_name": "Razorpay", "city_entering": "MUM", "size": 2500, "employee_count": 2500, "current_cities": ["BLR"], "source": "press_release", "contact_name": "Neha Sharma", "contact_title": "Head of Operations", "website": "razorpay.com"},
            {"company_name": "Stripe India", "city_entering": "BLR", "size": 8000, "employee_count": 500, "current_cities": [], "source": "press_release", "contact_name": "Ravi Krishnan", "contact_title": "Country Manager India", "website": "stripe.com"},
            {"company_name": "Deloitte India", "city_entering": "HYD", "size": 60000, "employee_count": 60000, "current_cities": ["MUM", "BLR", "DEL", "PUN"], "source": "business_news", "contact_name": "Arun Chopra", "contact_title": "Managing Director South India", "website": "deloitte.com/in"},
            {"company_name": "JPMorgan India", "city_entering": "BLR", "size": 50000, "employee_count": 50000, "current_cities": ["MUM", "HYD"], "source": "real_estate_signal", "contact_name": "Meera Patel", "contact_title": "VP Corporate Real Estate", "website": "jpmorgan.com"},
            {"company_name": "Zomato", "city_entering": "PUN", "size": 5500, "employee_count": 5500, "current_cities": ["DEL", "BLR", "MUM", "HYD"], "source": "linkedin_company", "contact_name": "Akash Gupta", "contact_title": "VP City Launches", "website": "zomato.com"},
            {"company_name": "ServiceNow India", "city_entering": "PUN", "size": 3000, "employee_count": 3000, "current_cities": ["HYD", "BLR"], "source": "mca_filings", "contact_name": "Deepa Menon", "contact_title": "Director India Operations", "website": "servicenow.com"},
            {"company_name": "Genpact", "city_entering": "BLR", "size": 90000, "employee_count": 90000, "current_cities": ["DEL", "HYD", "MUM", "PUN"], "source": "business_news", "contact_name": "Rohit Saxena", "contact_title": "SVP Delivery India South", "website": "genpact.com"},
        ]

        signals: list[dict] = []
        for e in entries:
            hours_ago = random.randint(6, 720)
            signal = self._normalize_signal({
                "source": e["source"],
                "company_name": e["company_name"],
                "company_size_est": e["size"],
                "city_entering": e["city_entering"],
                "current_cities": e["current_cities"],
                "announcement_date": (now - timedelta(hours=hours_ago)).isoformat(),
                "contact_name": e["contact_name"],
                "contact_title": e["contact_title"],
                "company_website": e["website"],
                "employee_count": e["employee_count"],
                "raw_data": {"synthetic": True},
            })
            signal["dedup_hash"] = generate_dedup_hash(e["company_name"], e["city_entering"])
            signals.append(signal)
        return signals


def collect_expansion_signals(dry_run: bool = False, cities: list[str] | None = None) -> list[dict]:
    """Entry point for expansion signal collection."""
    collector = ExpansionSignalCollector(dry_run=dry_run, target_cities=cities)
    return collector.collect_all()
