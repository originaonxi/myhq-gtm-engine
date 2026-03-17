"""myHQ GTM Engine — Funding signal collection from Indian startup ecosystem.

Every funded startup (Seed → Series A) is a Persona 1 lead.
Timing: contact within 48 hours of funding announcement.
Sources: Entrackr, Inc42, Tracxn, Crunchbase, LinkedIn, Twitter/X, Google News.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from config.settings import CITIES, DRY_RUN
from pipeline.utils import (
    IST,
    batch_upsert_to_supabase,
    generate_dedup_hash,
    parse_indian_amount,
    resolve_city_code,
    safe_get,
    scraperapi_fetch,
    serpapi_search,
)

logger = logging.getLogger(__name__)


class FundingSignalCollector:
    """Collects funding signals from multiple Indian startup news sources."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.signals: list[dict] = []

    def collect_all(self) -> list[dict]:
        """Run all collectors, deduplicate, store, and return signals."""
        if self.dry_run:
            self.signals = self._generate_synthetic_data()
            logger.info("[DRY RUN] Generated %d synthetic funding signals", len(self.signals))
            return self.signals

        collectors = [
            ("Entrackr", self._collect_entrackr),
            ("Inc42", self._collect_inc42),
            ("Tracxn", self._collect_tracxn),
            ("Crunchbase", self._collect_crunchbase),
            ("LinkedIn News", self._collect_linkedin_news),
            ("Twitter/X", self._collect_twitter),
            ("Google News", self._collect_google_news),
        ]
        for name, fn in collectors:
            try:
                results = fn()
                logger.info("[%s] Collected %d funding signals", name, len(results))
                self.signals.extend(results)
            except Exception as exc:
                logger.error("[%s] Collection failed: %s", name, exc)

        self.signals = self._deduplicate(self.signals)
        stored = self._store(self.signals)
        logger.info("Total funding signals: %d (stored %d)", len(self.signals), stored)
        return self.signals

    # ── Source collectors ────────────────────────────────────────────

    def _collect_entrackr(self) -> list[dict]:
        """Scrape Entrackr funding section via ScraperAPI."""
        html = scraperapi_fetch("https://entrackr.com/category/funding/")
        if not html:
            return []
        return self._parse_funding_page(html, "entrackr")

    def _collect_inc42(self) -> list[dict]:
        """Scrape Inc42 funding section via ScraperAPI."""
        html = scraperapi_fetch("https://inc42.com/buzz/funding/")
        if not html:
            return []
        return self._parse_funding_page(html, "inc42")

    def _collect_tracxn(self) -> list[dict]:
        """Search Tracxn via SerpAPI Google search."""
        data = serpapi_search(
            "raised funding India 2026 seed series-a site:tracxn.com",
            num=20,
        )
        return [
            r
            for item in safe_get(data, "organic_results", default=[])
            if (r := self._parse_search_result(item, "tracxn"))
        ]

    def _collect_crunchbase(self) -> list[dict]:
        """Search Crunchbase via SerpAPI."""
        data = serpapi_search(
            "startup funding India 2026 seed series-a site:crunchbase.com",
            num=20,
        )
        return [
            r
            for item in safe_get(data, "organic_results", default=[])
            if (r := self._parse_search_result(item, "crunchbase"))
        ]

    def _collect_linkedin_news(self) -> list[dict]:
        """Search LinkedIn for funding announcements via SerpAPI."""
        signals: list[dict] = []
        queries = [
            "thrilled to announce our seed round site:linkedin.com India",
            "excited to share we raised site:linkedin.com India",
            "we closed our Series A site:linkedin.com India",
        ]
        for city_code, city_info in CITIES.items():
            queries.append(f"raised funding {city_info['name']} 2026 startup site:linkedin.com")

        for q in queries:
            data = serpapi_search(q, num=10)
            for item in safe_get(data, "organic_results", default=[]):
                r = self._parse_search_result(item, "linkedin_news")
                if r:
                    signals.append(r)
        return signals

    def _collect_twitter(self) -> list[dict]:
        """Search Twitter/X for funding announcements via SerpAPI."""
        data = serpapi_search(
            '"raised seed" OR "raised series a" OR "we raised" India 2026',
            search_type="google",
            num=20,
            tbs="qdr:w",  # last week
        )
        return [
            r
            for item in safe_get(data, "organic_results", default=[])
            if (r := self._parse_search_result(item, "twitter"))
        ]

    def _collect_google_news(self) -> list[dict]:
        """Search Google News for funding per city via SerpAPI."""
        signals: list[dict] = []
        for city_code, city_info in CITIES.items():
            data = serpapi_search(
                f"startup funding India {city_info['name']}",
                tbm="nws",
                tbs="qdr:w",
                num=10,
            )
            for item in safe_get(data, "news_results", default=[]):
                r = self._parse_search_result(item, "google_news")
                if r:
                    r["city"] = city_code
                    signals.append(r)
        return signals

    # ── Parsing helpers ─────────────────────────────────────────────

    def _parse_funding_page(self, html: str, source: str) -> list[dict]:
        """Parse an HTML funding news page for articles."""
        soup = BeautifulSoup(html, "html.parser")
        signals: list[dict] = []
        for article in soup.find_all("article")[:20]:
            try:
                title_el = article.find(["h2", "h3", "a"])
                title = title_el.get_text(strip=True) if title_el else ""
                snippet_el = article.find("p")
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                text = f"{title} {snippet}".lower()

                # Quick relevance filter
                if not any(kw in text for kw in ["raise", "fund", "seed", "series", "crore", "million"]):
                    continue

                signal = self._extract_funding_from_text(title, snippet, source)
                if signal:
                    signals.append(signal)
            except Exception as exc:
                logger.debug("Article parse error in %s: %s", source, exc)
        return signals

    def _parse_search_result(self, item: dict, source: str) -> dict | None:
        """Parse a SerpAPI search result into a funding signal."""
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        return self._extract_funding_from_text(title, snippet, source)

    def _extract_funding_from_text(self, title: str, snippet: str, source: str) -> dict | None:
        """Extract structured funding data from title + snippet text."""
        text = f"{title} {snippet}"
        if not text.strip():
            return None

        # Try to find amount
        amount = parse_indian_amount(text)

        # Determine round type
        tl = text.lower()
        round_type = None
        for rt, keywords in [
            ("pre_seed", ["pre-seed", "pre seed"]),
            ("seed", ["seed round", "seed funding", "raised seed"]),
            ("series_a", ["series a", "series-a"]),
            ("series_b", ["series b", "series-b"]),
        ]:
            if any(kw in tl for kw in keywords):
                round_type = rt
                break

        # Determine city
        city = None
        for code, info in CITIES.items():
            for alias in info["aliases"]:
                if alias.lower() in tl:
                    city = code
                    break
            if city:
                break

        return self._normalize_signal(
            {
                "source": source,
                "company_name": title.split(" raise")[0].split(" Raise")[0].split(" secure")[0].strip()[:100],
                "amount_raised": amount,
                "round_type": round_type or "seed",
                "city": city,
                "sector": "",
                "investor_names": [],
                "announcement_date": datetime.now(IST).isoformat(),
                "raw_data": {"title": title, "snippet": snippet},
            }
        )

    def _normalize_signal(self, raw: dict) -> dict:
        """Normalize a raw signal into the signals_funding schema."""
        return {
            "source": raw.get("source", ""),
            "company_name": raw.get("company_name", "Unknown"),
            "founder_name": raw.get("founder_name", ""),
            "amount_raised": raw.get("amount_raised", ""),
            "round_type": raw.get("round_type", "seed"),
            "city": raw.get("city") or "",
            "sector": raw.get("sector", ""),
            "investor_names": raw.get("investor_names", []),
            "announcement_date": raw.get("announcement_date"),
            "linkedin_company_url": raw.get("linkedin_company_url", ""),
            "founder_linkedin": raw.get("founder_linkedin", ""),
            "company_website": raw.get("company_website", ""),
            "employee_count_est": raw.get("employee_count_est"),
            "raw_data": raw.get("raw_data", {}),
            "intent_score": 0,
            "processed": False,
        }

    # ── Dedup & storage ─────────────────────────────────────────────

    def _deduplicate(self, signals: list[dict]) -> list[dict]:
        """Deduplicate via MD5(company_name + round_type + city)."""
        seen: set[str] = set()
        unique: list[dict] = []
        for s in signals:
            h = generate_dedup_hash(
                s.get("company_name", ""),
                s.get("round_type", ""),
                s.get("city", ""),
            )
            if h not in seen:
                seen.add(h)
                s["dedup_hash"] = h
                unique.append(s)
        return unique

    def _store(self, signals: list[dict]) -> int:
        if self.dry_run:
            return len(signals)
        return batch_upsert_to_supabase("signals_funding", signals)

    # ── Synthetic data for --dry-run ────────────────────────────────

    def _generate_synthetic_data(self) -> list[dict]:
        """Generate realistic Indian startup funding data."""
        now = datetime.now(IST)
        companies = [
            {
                "company_name": "PayRight",
                "founder_name": "Arjun Mehta",
                "amount_raised": "₹18Cr",
                "round_type": "seed",
                "city": "BLR",
                "sector": "FinTech",
                "investor_names": ["Peak XV Partners", "Blume Ventures"],
                "employee_count_est": 12,
                "company_website": "payright.in",
                "founder_linkedin": "linkedin.com/in/arjunmehta",
            },
            {
                "company_name": "MedAssist AI",
                "founder_name": "Dr. Priya Sharma",
                "amount_raised": "₹25Cr",
                "round_type": "series_a",
                "city": "BLR",
                "sector": "HealthTech",
                "investor_names": ["Accel", "Nexus Venture Partners"],
                "employee_count_est": 35,
                "company_website": "medassistai.com",
                "founder_linkedin": "linkedin.com/in/drpriyasharma",
            },
            {
                "company_name": "StackBuild",
                "founder_name": "Karthik Rajan",
                "amount_raised": "₹8Cr",
                "round_type": "seed",
                "city": "BLR",
                "sector": "DevTools",
                "investor_names": ["Y Combinator", "Lightspeed India"],
                "employee_count_est": 8,
                "company_website": "stackbuild.dev",
                "founder_linkedin": "linkedin.com/in/karthikrajan",
            },
            {
                "company_name": "FarmFresh Direct",
                "founder_name": "Sneha Patil",
                "amount_raised": "₹15Cr",
                "round_type": "seed",
                "city": "PUN",
                "sector": "AgriTech",
                "investor_names": ["Omnivore Partners", "Ankur Capital"],
                "employee_count_est": 20,
                "company_website": "farmfreshdirect.in",
                "founder_linkedin": "linkedin.com/in/snehapatil",
            },
            {
                "company_name": "CleanGrid Energy",
                "founder_name": "Rohan Deshmukh",
                "amount_raised": "₹30Cr",
                "round_type": "series_a",
                "city": "MUM",
                "sector": "CleanTech",
                "investor_names": ["Sequoia Capital India", "ADB Ventures"],
                "employee_count_est": 28,
                "company_website": "cleangridenergy.in",
                "founder_linkedin": "linkedin.com/in/rohandeshmukh",
            },
            {
                "company_name": "Lingua Learn",
                "founder_name": "Aisha Khan",
                "amount_raised": "₹10Cr",
                "round_type": "seed",
                "city": "DEL",
                "sector": "EdTech",
                "investor_names": ["Matrix Partners India", "India Quotient"],
                "employee_count_est": 14,
                "company_website": "lingualearns.com",
                "founder_linkedin": "linkedin.com/in/aishakhan",
            },
            {
                "company_name": "ShipKaro",
                "founder_name": "Vikram Singh",
                "amount_raised": "₹22Cr",
                "round_type": "series_a",
                "city": "DEL",
                "sector": "Logistics SaaS",
                "investor_names": ["Tiger Global", "Chiratae Ventures"],
                "employee_count_est": 42,
                "company_website": "shipkaro.io",
                "founder_linkedin": "linkedin.com/in/vikramsingh",
            },
            {
                "company_name": "PropSync",
                "founder_name": "Neeraj Gupta",
                "amount_raised": "₹7Cr",
                "round_type": "pre_seed",
                "city": "MUM",
                "sector": "PropTech",
                "investor_names": ["Venture Highway", "iSeed"],
                "employee_count_est": 6,
                "company_website": "propsync.co",
                "founder_linkedin": "linkedin.com/in/neerajgupta",
            },
            {
                "company_name": "KreditBee Enterprise",
                "founder_name": "Deepak Ramanathan",
                "amount_raised": "₹50Cr",
                "round_type": "series_a",
                "city": "HYD",
                "sector": "FinTech / Lending",
                "investor_names": ["Elevation Capital", "Ribbit Capital"],
                "employee_count_est": 45,
                "company_website": "kreditbee-enterprise.in",
                "founder_linkedin": "linkedin.com/in/deepakramanathan",
            },
            {
                "company_name": "CyberVault",
                "founder_name": "Ananya Iyer",
                "amount_raised": "₹12Cr",
                "round_type": "seed",
                "city": "HYD",
                "sector": "Cybersecurity",
                "investor_names": ["3one4 Capital", "Endiya Partners"],
                "employee_count_est": 10,
                "company_website": "cybervault.io",
                "founder_linkedin": "linkedin.com/in/ananyaiyer",
            },
            {
                "company_name": "NutriBox",
                "founder_name": "Rahul Joshi",
                "amount_raised": "₹5Cr",
                "round_type": "seed",
                "city": "PUN",
                "sector": "D2C / Health",
                "investor_names": ["Titan Capital", "Sauce.vc"],
                "employee_count_est": 9,
                "company_website": "nutribox.in",
                "founder_linkedin": "linkedin.com/in/rahuljoshi",
            },
            {
                "company_name": "FleetPulse",
                "founder_name": "Manish Agarwal",
                "amount_raised": "₹16Cr",
                "round_type": "seed",
                "city": "BLR",
                "sector": "Fleet Management SaaS",
                "investor_names": ["Stellaris Venture Partners", "Together Fund"],
                "employee_count_est": 18,
                "company_website": "fleetpulse.in",
                "founder_linkedin": "linkedin.com/in/manishagarwal",
            },
            {
                "company_name": "TaxEase",
                "founder_name": "Pooja Reddy",
                "amount_raised": "₹9Cr",
                "round_type": "seed",
                "city": "HYD",
                "sector": "RegTech",
                "investor_names": ["WaterBridge Ventures", "Arkam Ventures"],
                "employee_count_est": 11,
                "company_website": "taxease.co.in",
                "founder_linkedin": "linkedin.com/in/poojareddy",
            },
            {
                "company_name": "DesignStack",
                "founder_name": "Ishaan Bhatia",
                "amount_raised": "₹20Cr",
                "round_type": "series_a",
                "city": "MUM",
                "sector": "B2B SaaS / Design",
                "investor_names": ["Accel", "Z47 (formerly Matrix)"],
                "employee_count_est": 30,
                "company_website": "designstack.io",
                "founder_linkedin": "linkedin.com/in/ishaanbhatia",
            },
            {
                "company_name": "RuralConnect",
                "founder_name": "Kavitha Sundaram",
                "amount_raised": "₹14Cr",
                "round_type": "seed",
                "city": "DEL",
                "sector": "AgriTech / Rural Fintech",
                "investor_names": ["Omidyar Network India", "Aavishkaar Capital"],
                "employee_count_est": 22,
                "company_website": "ruralconnect.in",
                "founder_linkedin": "linkedin.com/in/kavithasundaram",
            },
            {
                "company_name": "Voxel Robotics",
                "founder_name": "Amit Tiwari",
                "amount_raised": "₹35Cr",
                "round_type": "series_a",
                "city": "PUN",
                "sector": "Deep Tech / Robotics",
                "investor_names": ["pi Ventures", "SpecialeInvest"],
                "employee_count_est": 25,
                "company_website": "voxelrobotics.in",
                "founder_linkedin": "linkedin.com/in/amittiwari",
            },
        ]

        signals: list[dict] = []
        for i, c in enumerate(companies):
            hours_ago = random.randint(2, 168)  # 2 hours to 7 days
            announcement = now - timedelta(hours=hours_ago)
            signal = self._normalize_signal(
                {
                    **c,
                    "source": random.choice(["entrackr", "inc42", "tracxn", "crunchbase", "google_news"]),
                    "announcement_date": announcement.isoformat(),
                    "raw_data": {"synthetic": True, "index": i},
                }
            )
            signal["dedup_hash"] = generate_dedup_hash(
                c["company_name"], c["round_type"], c["city"]
            )
            signals.append(signal)

        return signals


# ── Module entry point ──────────────────────────────────────────────


def collect_funding_signals(dry_run: bool = False) -> list[dict]:
    """Entry point for funding signal collection."""
    collector = FundingSignalCollector(dry_run=dry_run)
    return collector.collect_all()
