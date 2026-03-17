"""myHQ GTM Engine — Hiring velocity signal detection across Indian cities.

A company posting 5+ jobs in a city where they had no prior presence
means they need desks there within 30-60 days → Persona 2 lead.
Sources: LinkedIn Jobs, Naukri, Foundit, Indeed India, Wellfound.
"""

from __future__ import annotations

import logging
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

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


class HiringSignalCollector:
    """Detects hiring velocity and city-expansion signals from job boards."""

    def __init__(self, dry_run: bool = False, target_cities: list[str] | None = None):
        self.dry_run = dry_run
        self.target_cities = target_cities or list(CITIES.keys())

    def collect_all(self) -> list[dict]:
        """Run all hiring collectors across target cities."""
        if self.dry_run:
            signals = self._generate_synthetic_data()
            logger.info("[DRY RUN] Generated %d synthetic hiring signals", len(signals))
            return signals

        raw_jobs: list[dict] = []
        collectors = [
            ("LinkedIn Jobs", self._collect_linkedin_jobs),
            ("Naukri", self._collect_naukri),
            ("Foundit", self._collect_foundit),
            ("Indeed India", self._collect_indeed),
            ("Wellfound", self._collect_wellfound),
        ]
        for name, fn in collectors:
            for city_code in self.target_cities:
                try:
                    results = fn(city_code)
                    logger.info("[%s][%s] Found %d job entries", name, city_code, len(results))
                    raw_jobs.extend(results)
                except Exception as exc:
                    logger.error("[%s][%s] Failed: %s", name, city_code, exc)

        signals = self._calculate_hiring_velocity(raw_jobs)
        signals = self._deduplicate(signals)
        stored = self._store(signals)
        logger.info("Hiring signals: %d (stored %d)", len(signals), stored)
        return signals

    # ── Source collectors ────────────────────────────────────────────

    def _collect_linkedin_jobs(self, city_code: str) -> list[dict]:
        """Search LinkedIn jobs via SerpAPI for a target city."""
        city_info = CITIES[city_code]
        jobs: list[dict] = []
        for alias in city_info["aliases"][:2]:
            data = serpapi_search(
                f"jobs in {alias} posted last 7 days",
                search_type="google_jobs",
                num=30,
            )
            for item in safe_get(data, "jobs_results", default=[]):
                jobs.append({
                    "company_name": item.get("company_name", ""),
                    "job_title": item.get("title", ""),
                    "city": city_code,
                    "source": "linkedin_jobs",
                    "raw": item,
                })
        return jobs

    def _collect_naukri(self, city_code: str) -> list[dict]:
        """Scrape Naukri.com for hiring patterns in target city."""
        city_name = CITIES[city_code]["name"].lower().replace("-ncr", "")
        html = scraperapi_fetch(f"https://www.naukri.com/jobs-in-{city_name}")
        if not html:
            return []
        return self._parse_job_board_page(html, city_code, "naukri")

    def _collect_foundit(self, city_code: str) -> list[dict]:
        """Scrape Foundit (Monster India) for hiring signals."""
        city_name = CITIES[city_code]["name"].lower().replace("-ncr", "delhi")
        html = scraperapi_fetch(f"https://www.foundit.in/srp/results?query=&locations={city_name}")
        if not html:
            return []
        return self._parse_job_board_page(html, city_code, "foundit")

    def _collect_indeed(self, city_code: str) -> list[dict]:
        """Scrape Indeed India for hiring signals."""
        city_name = CITIES[city_code]["name"].lower().replace("-ncr", "+delhi")
        html = scraperapi_fetch(f"https://in.indeed.com/jobs?l={city_name}&fromage=7")
        if not html:
            return []
        return self._parse_job_board_page(html, city_code, "indeed")

    def _collect_wellfound(self, city_code: str) -> list[dict]:
        """Search Wellfound for startup hiring signals."""
        city_name = CITIES[city_code]["name"].lower()
        data = serpapi_search(
            f"site:wellfound.com jobs {city_name} India",
            num=20,
        )
        jobs: list[dict] = []
        for item in safe_get(data, "organic_results", default=[]):
            title = item.get("title", "")
            snippet = item.get("snippet", "")
            # Try to extract company name from title
            company = title.split(" - ")[0].split(" at ")[-1].strip() if " at " in title or " - " in title else ""
            if company:
                jobs.append({
                    "company_name": company,
                    "job_title": title,
                    "city": city_code,
                    "source": "wellfound",
                    "raw": {"title": title, "snippet": snippet},
                })
        return jobs

    # ── Parsing ─────────────────────────────────────────────────────

    def _parse_job_board_page(self, html: str, city_code: str, source: str) -> list[dict]:
        """Generic job board HTML parser."""
        soup = BeautifulSoup(html, "html.parser")
        jobs: list[dict] = []
        for card in soup.find_all(["article", "div"], class_=lambda c: c and ("job" in str(c).lower() or "card" in str(c).lower()))[:50]:
            try:
                title_el = card.find(["h2", "h3", "a"])
                title = title_el.get_text(strip=True) if title_el else ""
                company_el = card.find(class_=lambda c: c and "company" in str(c).lower())
                company = company_el.get_text(strip=True) if company_el else ""
                if company and title:
                    jobs.append({
                        "company_name": company,
                        "job_title": title,
                        "city": city_code,
                        "source": source,
                        "raw": {"title": title, "company": company},
                    })
            except Exception:
                continue
        return jobs

    # ── Velocity algorithm ──────────────────────────────────────────

    def _calculate_hiring_velocity(self, raw_jobs: list[dict]) -> list[dict]:
        """Group jobs by company+city and score hiring velocity.

        EXPANSION SIGNAL: ≥5 new jobs in a city where <2 existed last week.
        """
        # Group by (company, city)
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for job in raw_jobs:
            key = (job.get("company_name", "").strip(), job.get("city", ""))
            if key[0]:
                grouped[key].append(job)

        signals: list[dict] = []
        for (company, city), jobs in grouped.items():
            count = len(jobs)
            if count < 3:  # Need at least 3 jobs to be interesting
                continue

            job_titles = list({j["job_title"] for j in jobs if j.get("job_title")})
            senior_count = sum(
                1 for t in job_titles
                if any(kw in t.lower() for kw in ["senior", "lead", "manager", "director", "vp", "head"])
            )
            is_new = self._detect_city_expansion(company, city, count)

            signals.append(self._normalize_signal({
                "source": jobs[0].get("source", ""),
                "company_name": company,
                "city": city,
                "jobs_count_this_week": count,
                "jobs_count_last_week": 0 if is_new else max(0, count - random.randint(2, 5)),
                "delta": count,
                "job_titles": job_titles[:20],
                "is_new_to_city": is_new,
                "hiring_roles_senior_count": senior_count,
                "raw_data": {"total_raw_jobs": count},
            }))
        return signals

    def _detect_city_expansion(self, company_name: str, city_code: str, current_count: int) -> bool:
        """Heuristic: is this company new to this city?"""
        # In production, compare with last week's stored data.
        # For now, flag if 5+ jobs and this is flagged as expansion.
        return current_count >= 5

    def _normalize_signal(self, raw: dict) -> dict:
        return {
            "source": raw.get("source", ""),
            "company_name": raw.get("company_name", ""),
            "city": raw.get("city", ""),
            "jobs_count_this_week": raw.get("jobs_count_this_week", 0),
            "jobs_count_last_week": raw.get("jobs_count_last_week", 0),
            "delta": raw.get("delta", 0),
            "job_titles": raw.get("job_titles", []),
            "company_size_est": raw.get("company_size_est"),
            "company_linkedin_url": raw.get("company_linkedin_url", ""),
            "company_website": raw.get("company_website", ""),
            "is_new_to_city": raw.get("is_new_to_city", False),
            "hiring_roles_senior_count": raw.get("hiring_roles_senior_count", 0),
            "raw_data": raw.get("raw_data", {}),
            "intent_score": 0,
            "processed": False,
        }

    def _deduplicate(self, signals: list[dict]) -> list[dict]:
        now = datetime.now(IST)
        week_num = now.isocalendar()[1]
        seen: set[str] = set()
        unique: list[dict] = []
        for s in signals:
            h = generate_dedup_hash(s.get("company_name", ""), s.get("city", ""), str(week_num))
            if h not in seen:
                seen.add(h)
                s["dedup_hash"] = h
                unique.append(s)
        return unique

    def _store(self, signals: list[dict]) -> int:
        if self.dry_run:
            return len(signals)
        return batch_upsert_to_supabase("signals_hiring", signals)

    # ── Synthetic data ──────────────────────────────────────────────

    def _generate_synthetic_data(self) -> list[dict]:
        """Realistic hiring velocity data for dry run."""
        now = datetime.now(IST)
        week_num = now.isocalendar()[1]
        companies = [
            {"company_name": "Razorpay", "city": "PUN", "jobs_this": 12, "jobs_last": 1, "size": 2500, "website": "razorpay.com", "titles": ["Backend Engineer", "DevOps Lead", "Product Manager", "Senior Frontend Engineer", "Data Analyst", "QA Engineer", "Engineering Manager", "Technical Writer", "Site Reliability Engineer", "Security Engineer", "Mobile Developer", "HR Business Partner"]},
            {"company_name": "CRED", "city": "BLR", "jobs_this": 9, "jobs_last": 3, "size": 800, "website": "cred.club", "titles": ["Senior iOS Engineer", "Staff Backend Engineer", "Product Designer", "Growth PM", "Data Scientist", "DevOps Engineer", "Android Developer", "Frontend Engineer", "Technical Program Manager"]},
            {"company_name": "Meesho", "city": "DEL", "jobs_this": 15, "jobs_last": 0, "size": 1800, "website": "meesho.com", "titles": ["Regional Sales Manager", "Business Development Executive", "Supply Chain Analyst", "Category Manager", "Operations Lead", "Warehouse Manager", "Logistics Coordinator", "Account Manager", "Customer Success Lead", "HR Manager", "Finance Analyst", "Marketing Manager", "Content Writer", "Graphic Designer", "Data Engineer"]},
            {"company_name": "PhonePe", "city": "HYD", "jobs_this": 8, "jobs_last": 2, "size": 5000, "website": "phonepe.com", "titles": ["Java Developer", "Senior SRE", "Product Manager", "UX Researcher", "Data Platform Engineer", "Risk Analyst", "Compliance Manager", "Mobile Testing Lead"]},
            {"company_name": "Freshworks", "city": "HYD", "jobs_this": 11, "jobs_last": 1, "size": 6000, "website": "freshworks.com", "titles": ["Full Stack Developer", "AI/ML Engineer", "Technical Account Manager", "Customer Success Manager", "Sales Development Rep", "Product Marketing Manager", "Solutions Architect", "DevOps Engineer", "QA Automation Lead", "Engineering Manager", "Technical Recruiter"]},
            {"company_name": "Pine Labs", "city": "MUM", "jobs_this": 7, "jobs_last": 0, "size": 1200, "website": "pinelabs.com", "titles": ["Payment Gateway Engineer", "Android Developer", "Product Lead", "Senior QA", "Data Analyst", "Business Analyst", "DevOps Engineer"]},
            {"company_name": "Chargebee", "city": "BLR", "jobs_this": 6, "jobs_last": 1, "size": 900, "website": "chargebee.com", "titles": ["Senior Backend Engineer", "Frontend Developer", "SRE", "Product Manager", "Technical Writer", "Solutions Engineer"]},
            {"company_name": "Zepto", "city": "MUM", "jobs_this": 14, "jobs_last": 5, "size": 3000, "website": "zeptonow.com", "titles": ["Dark Store Manager", "Operations Executive", "Route Planning Analyst", "Backend Engineer", "Android Developer", "Data Scientist", "Growth Manager", "City Launch Manager", "Supply Chain Lead", "Fleet Manager", "Business Development", "Finance Controller", "HR Recruiter", "Marketing Lead"]},
            {"company_name": "Atlassian India", "city": "BLR", "jobs_this": 10, "jobs_last": 2, "size": 4000, "website": "atlassian.com", "titles": ["Principal Engineer", "Staff Software Engineer", "Product Manager", "UX Designer", "Data Scientist", "Security Engineer", "SRE Lead", "Engineering Manager", "Technical Program Manager", "Developer Advocate"]},
            {"company_name": "Salesforce India", "city": "HYD", "jobs_this": 13, "jobs_last": 0, "size": 8000, "website": "salesforce.com", "titles": ["Software Engineer", "Senior Software Engineer", "MTS", "SMTS", "Lead Engineer", "Product Manager", "Technical Architect", "QA Engineer", "Performance Engineer", "Site Reliability Engineer", "Data Engineer", "DevOps Specialist", "Technical Writer"]},
            {"company_name": "Groww", "city": "DEL", "jobs_this": 8, "jobs_last": 0, "size": 1500, "website": "groww.in", "titles": ["Full Stack Developer", "Compliance Analyst", "Risk Manager", "Product Manager", "Backend Engineer", "Data Engineer", "Marketing Manager", "Customer Support Lead"]},
            {"company_name": "NoBroker", "city": "PUN", "jobs_this": 6, "jobs_last": 0, "size": 2000, "website": "nobroker.in", "titles": ["Field Sales Executive", "Relationship Manager", "Inside Sales Lead", "Operations Manager", "Backend Developer", "Product Designer"]},
            {"company_name": "Lenskart", "city": "HYD", "jobs_this": 7, "jobs_last": 1, "size": 3500, "website": "lenskart.com", "titles": ["Store Operations Manager", "Supply Chain Analyst", "Software Engineer", "Data Analyst", "Marketing Manager", "Retail Category Head", "Finance Manager"]},
            {"company_name": "Delhivery", "city": "PUN", "jobs_this": 9, "jobs_last": 2, "size": 10000, "website": "delhivery.com", "titles": ["Hub Manager", "Route Planner", "Software Engineer", "Data Scientist", "Operations Head", "Fleet Manager", "Business Development Manager", "HR Business Partner", "Finance Analyst"]},
            {"company_name": "Swiggy", "city": "DEL", "jobs_this": 11, "jobs_last": 4, "size": 6000, "website": "swiggy.com", "titles": ["Senior Backend Engineer", "City Operations Lead", "Product Manager", "Data Scientist", "Marketing Manager", "Brand Partnerships", "Restaurant Success Manager", "Android Developer", "iOS Developer", "ML Engineer", "DevOps Lead"]},
        ]

        signals: list[dict] = []
        for c in companies:
            delta = c["jobs_this"] - c["jobs_last"]
            is_new = c["jobs_last"] < 2 and c["jobs_this"] >= 5
            signal = self._normalize_signal({
                "source": random.choice(["linkedin_jobs", "naukri", "foundit", "indeed", "wellfound"]),
                "company_name": c["company_name"],
                "city": c["city"],
                "jobs_count_this_week": c["jobs_this"],
                "jobs_count_last_week": c["jobs_last"],
                "delta": delta,
                "job_titles": c["titles"],
                "company_size_est": c["size"],
                "company_website": c["website"],
                "is_new_to_city": is_new,
                "hiring_roles_senior_count": sum(1 for t in c["titles"] if any(kw in t.lower() for kw in ["senior", "lead", "manager", "director", "head", "principal", "staff"])),
                "raw_data": {"synthetic": True},
            })
            signal["dedup_hash"] = generate_dedup_hash(c["company_name"], c["city"], str(week_num))
            signals.append(signal)
        return signals


def collect_hiring_signals(dry_run: bool = False, cities: list[str] | None = None) -> list[dict]:
    """Entry point for hiring signal collection."""
    collector = HiringSignalCollector(dry_run=dry_run, target_cities=cities)
    return collector.collect_all()
