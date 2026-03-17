"""myHQ GTM Engine — Shared utilities for API calls, hashing, and DB ops."""

from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from config.settings import (
    APOLLO_API_KEY,
    DRY_RUN,
    SCRAPER_API_KEY,
    SERP_API_KEY,
    SUPABASE_KEY,
    SUPABASE_URL,
)

logger = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))

# ── Supabase client ─────────────────────────────────────────────────


def get_supabase_client():
    """Return a Supabase client instance, or None in dry-run mode."""
    if DRY_RUN or not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        from supabase import create_client

        return create_client(SUPABASE_URL, SUPABASE_KEY)
    except Exception as exc:
        logger.error("Failed to create Supabase client: %s", exc)
        return None


# ── HTTP helpers with retry ─────────────────────────────────────────

_MAX_RETRIES = 3
_BACKOFF_SECONDS = (2, 4, 8)


def _retry(fn, *args, **kwargs) -> Any:
    """Call *fn* with exponential back-off (3 attempts)."""
    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            last_exc = exc
            wait = _BACKOFF_SECONDS[attempt]
            logger.warning(
                "Attempt %d/%d failed (%s). Retrying in %ds…",
                attempt + 1,
                _MAX_RETRIES,
                exc,
                wait,
            )
            time.sleep(wait)
    logger.error("All %d attempts failed. Last error: %s", _MAX_RETRIES, last_exc)
    return None


def serpapi_search(
    query: str,
    search_type: str = "google",
    **kwargs: Any,
) -> dict:
    """Search via SerpAPI with retry logic.

    Parameters
    ----------
    query:
        The search query string.
    search_type:
        Engine name — ``google``, ``google_news``, ``google_trends``, etc.
    **kwargs:
        Extra params forwarded to SerpAPI (``num``, ``tbm``, ``tbs``, …).

    Returns
    -------
    dict  — parsed JSON response, or empty dict on failure.
    """
    if not SERP_API_KEY:
        logger.debug("SERP_API_KEY not set — skipping search.")
        return {}

    params: dict[str, Any] = {
        "q": query,
        "api_key": SERP_API_KEY,
        "engine": search_type,
        **kwargs,
    }

    def _do() -> dict:
        resp = requests.get(
            "https://serpapi.com/search",
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    result = _retry(_do)
    return result if isinstance(result, dict) else {}


def scraperapi_fetch(url: str) -> str:
    """Fetch a URL through ScraperAPI (handles JS rendering, proxies).

    Returns
    -------
    str  — raw HTML, or empty string on failure.
    """
    if not SCRAPER_API_KEY:
        logger.debug("SCRAPER_API_KEY not set — skipping fetch.")
        return ""

    api_url = "https://api.scraperapi.com"
    params = {"api_key": SCRAPER_API_KEY, "url": url}

    def _do() -> str:
        resp = requests.get(api_url, params=params, timeout=60)
        resp.raise_for_status()
        return resp.text

    result = _retry(_do)
    return result if isinstance(result, str) else ""


def apollo_enrich(
    company_name: str,
    domain: str | None = None,
) -> dict:
    """Look up a company / person via Apollo.io API.

    Returns
    -------
    dict  — enrichment payload, or empty dict on failure.
    """
    if not APOLLO_API_KEY:
        logger.debug("APOLLO_API_KEY not set — skipping enrichment.")
        return {}

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
    }
    payload: dict[str, Any] = {
        "api_key": APOLLO_API_KEY,
        "q_organization_name": company_name,
    }
    if domain:
        payload["q_organization_domains"] = domain

    def _do() -> dict:
        resp = requests.post(
            "https://api.apollo.io/v1/mixed_companies/search",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    result = _retry(_do)
    return result if isinstance(result, dict) else {}


def apollo_find_person(
    company_domain: str,
    titles: list[str],
) -> dict:
    """Find a decision-maker at a company via Apollo people search."""
    if not APOLLO_API_KEY:
        return {}

    headers = {"Content-Type": "application/json", "Cache-Control": "no-cache"}
    payload = {
        "api_key": APOLLO_API_KEY,
        "q_organization_domains": company_domain,
        "person_titles": titles,
        "page": 1,
        "per_page": 3,
    }

    def _do() -> dict:
        resp = requests.post(
            "https://api.apollo.io/v1/mixed_people/search",
            headers=headers,
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    result = _retry(_do)
    return result if isinstance(result, dict) else {}


# ── Deduplication ───────────────────────────────────────────────────


def generate_dedup_hash(*fields: str) -> str:
    """MD5 hash of concatenated, lowercased, stripped fields."""
    normalised = "||".join(f.strip().lower() for f in fields if f)
    return hashlib.md5(normalised.encode()).hexdigest()


# ── Supabase writes ────────────────────────────────────────────────


def upsert_to_supabase(
    table: str,
    data: dict,
    dedup_field: str = "dedup_hash",
) -> bool:
    """Upsert a single record (idempotent on *dedup_field*).

    Returns True on success.
    """
    client = get_supabase_client()
    if client is None:
        return False
    try:
        client.table(table).upsert(data, on_conflict=dedup_field).execute()
        return True
    except Exception as exc:
        logger.error("Supabase upsert to %s failed: %s", table, exc)
        return False


def batch_upsert_to_supabase(
    table: str,
    records: list[dict],
    dedup_field: str = "dedup_hash",
) -> int:
    """Upsert a batch of records. Returns count of successful writes."""
    client = get_supabase_client()
    if client is None:
        return 0
    ok = 0
    # Supabase supports bulk upsert
    try:
        client.table(table).upsert(records, on_conflict=dedup_field).execute()
        ok = len(records)
    except Exception:
        # Fallback to one-by-one
        for rec in records:
            try:
                client.table(table).upsert(rec, on_conflict=dedup_field).execute()
                ok += 1
            except Exception as exc:
                logger.error("Row upsert failed in %s: %s", table, exc)
    return ok


# ── Data helpers ────────────────────────────────────────────────────


def safe_get(d: dict | None, *keys: str, default: Any = None) -> Any:
    """Nested dict access without KeyError."""
    current = d
    for k in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(k, default)
    return current


_AMOUNT_RE = re.compile(
    r"[\$₹]?\s*([\d,.]+)\s*(cr|crore|crores|m|mn|million|k|lakh|lakhs|billion|bn|b)?\b",
    re.IGNORECASE,
)


def parse_indian_amount(text: str) -> str:
    """Parse Indian-style funding amounts into standardised form.

    Examples
    --------
    >>> parse_indian_amount("₹12Cr")
    '₹12 Cr'
    >>> parse_indian_amount("$2M")
    '$2M'
    >>> parse_indian_amount("INR 12 crore")
    '₹12 Cr'
    """
    if not text:
        return ""
    text = text.replace(",", "").strip()
    m = _AMOUNT_RE.search(text)
    if not m:
        return text
    number = m.group(1)
    unit = (m.group(2) or "").lower()
    currency = "₹" if ("inr" in text.lower() or "₹" in text or "rs" in text.lower()) else "$"
    unit_map = {
        "cr": "Cr",
        "crore": "Cr",
        "crores": "Cr",
        "m": "M",
        "mn": "M",
        "million": "M",
        "k": "K",
        "lakh": "L",
        "lakhs": "L",
        "billion": "B",
        "bn": "B",
        "b": "B",
    }
    normalised_unit = unit_map.get(unit, "")
    return f"{currency}{number}{normalised_unit}" if normalised_unit else f"{currency}{number}"


def is_within_days(date_str: str | None, days: int) -> bool:
    """Return True if *date_str* (ISO-ish) is within *days* of now (IST)."""
    if not date_str:
        return False
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return (datetime.now(IST) - dt).days <= days
    except (ValueError, TypeError):
        return False


def days_since(date_str: str | None) -> int:
    """Return number of days since *date_str*, or 999 if unparseable."""
    if not date_str:
        return 999
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return max(0, (datetime.now(IST) - dt).days)
    except (ValueError, TypeError):
        return 999


def hours_since(date_str: str | None) -> float:
    """Return hours since *date_str*, or 9999 if unparseable."""
    if not date_str:
        return 9999.0
    try:
        dt = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        delta = datetime.now(IST) - dt
        return delta.total_seconds() / 3600
    except (ValueError, TypeError):
        return 9999.0


_INDIAN_MOBILE_RE = re.compile(r"^\+?91?[6-9]\d{9}$")


def format_phone_india(phone: str | None) -> str:
    """Normalise an Indian phone number to +91XXXXXXXXXX format."""
    if not phone:
        return ""
    digits = re.sub(r"[^\d]", "", phone)
    if len(digits) == 10 and digits[0] in "6789":
        return f"+91{digits}"
    if len(digits) == 12 and digits[:2] == "91":
        return f"+{digits}"
    if len(digits) == 11 and digits[0] == "0":
        return f"+91{digits[1:]}"
    return f"+{digits}" if digits else ""


def is_valid_indian_mobile(phone: str | None) -> bool:
    """Check if a normalised phone looks like a valid Indian mobile."""
    if not phone:
        return False
    return bool(_INDIAN_MOBILE_RE.match(phone.replace(" ", "").replace("-", "")))


def resolve_city_code(text: str) -> str | None:
    """Map a city name / alias to its canonical code (BLR, MUM, …)."""
    from config.settings import CITIES

    if not text:
        return None
    t = text.strip().upper()
    if t in CITIES:
        return t
    for code, info in CITIES.items():
        for alias in info["aliases"]:
            if alias.upper() == t or alias.upper() in t:
                return code
    return None
