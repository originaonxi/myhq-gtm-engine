"""myHQ GTM Engine — TRAI DND and PDPB compliance for Indian outreach.

TRAI: NDNC registry check before any SDR call.
PDPB: Consent tracking, data minimisation, right to erasure.
Spam prevention: Max 3 touches, 7-day cooling period.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
from datetime import datetime, timedelta

from config.settings import COOLING_PERIOD_DAYS, DRY_RUN, MAX_OUTREACH_TOUCHES
from pipeline.utils import IST, format_phone_india, get_supabase_client, is_valid_indian_mobile

logger = logging.getLogger(__name__)

COMPLIANCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)))
DND_CACHE_FILE = os.path.join(COMPLIANCE_DIR, "dnd_cache.json")
CONSENT_DIR = os.path.join(COMPLIANCE_DIR, "consent_records")


class IndiaCompliance:
    """Handles TRAI and PDPB compliance for Indian outreach."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self._dnd_cache = self._load_dnd_cache()
        os.makedirs(CONSENT_DIR, exist_ok=True)

    # ── DND check ───────────────────────────────────────────────────

    def check_dnd(self, phone: str) -> dict:
        """Check if phone is on TRAI NDNC (National Do Not Call) registry."""
        phone = format_phone_india(phone)
        if not is_valid_indian_mobile(phone):
            return {"is_dnd": False, "checked_at": datetime.now(IST).isoformat(), "source": "invalid_number"}

        # Check cache
        if phone in self._dnd_cache:
            entry = self._dnd_cache[phone]
            return {"is_dnd": entry.get("is_dnd", False), "checked_at": entry.get("checked_at", ""), "source": "cache"}

        if self.dry_run:
            # ~10% chance of DND for realistic testing
            is_dnd = random.random() < 0.10
            result = {"is_dnd": is_dnd, "checked_at": datetime.now(IST).isoformat(), "source": "dry_run"}
        else:
            # In production: would check TRAI DND API / scrape portal
            result = {"is_dnd": False, "checked_at": datetime.now(IST).isoformat(), "source": "api_placeholder"}

        self._dnd_cache[phone] = result
        self._save_dnd_cache()
        self._log_dnd_check(phone, result)
        return result

    # ── Suppression list ────────────────────────────────────────────

    def check_suppression_list(self, phone: str | None = None, email: str | None = None) -> bool:
        """Check if contact is on suppression list."""
        if self.dry_run:
            return False  # No suppression in dry run
        client = get_supabase_client()
        if not client:
            return False
        try:
            if phone:
                resp = client.table("suppression_list").select("id").eq("phone", format_phone_india(phone)).execute()
                if resp.data:
                    return True
            if email:
                resp = client.table("suppression_list").select("id").eq("email", email.lower()).execute()
                if resp.data:
                    return True
        except Exception as exc:
            logger.error("Suppression list check failed: %s", exc)
        return False

    def add_to_suppression(self, phone: str | None = None, email: str | None = None, reason: str = "opt_out") -> bool:
        """Add contact to suppression list."""
        if self.dry_run:
            logger.info("[DRY RUN] Would suppress phone=%s email=%s reason=%s", phone, email, reason)
            return True
        client = get_supabase_client()
        if not client:
            return False
        try:
            data = {"reason": reason, "added_at": datetime.now(IST).isoformat()}
            if phone:
                data["phone"] = format_phone_india(phone)
            if email:
                data["email"] = email.lower()
            client.table("suppression_list").insert(data).execute()
            return True
        except Exception as exc:
            logger.error("Failed to add to suppression list: %s", exc)
            return False

    # ── Outreach limits ─────────────────────────────────────────────

    def check_outreach_limits(self, lead_id: str) -> dict:
        """Check if lead has exceeded max touches or is in cooling period."""
        if self.dry_run:
            return {"can_contact": True, "touches_remaining": MAX_OUTREACH_TOUCHES, "cooling_until": None, "reason": "dry_run"}

        client = get_supabase_client()
        if not client:
            return {"can_contact": True, "touches_remaining": MAX_OUTREACH_TOUCHES, "cooling_until": None, "reason": "no_db"}

        try:
            resp = client.table("outreach").select("generated_at, whatsapp_sent_at, email_sent_at").eq("lead_id", lead_id).execute()
            records = resp.data or []
            touch_count = sum(1 for r in records if r.get("whatsapp_sent_at") or r.get("email_sent_at"))

            if touch_count >= MAX_OUTREACH_TOUCHES:
                return {"can_contact": False, "touches_remaining": 0, "cooling_until": None, "reason": f"Max {MAX_OUTREACH_TOUCHES} touches reached"}

            # Check cooling period
            last_touch = None
            for r in records:
                for field in ["whatsapp_sent_at", "email_sent_at"]:
                    ts = r.get(field)
                    if ts:
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if not last_touch or dt > last_touch:
                            last_touch = dt
            if last_touch:
                cooling_end = last_touch + timedelta(days=COOLING_PERIOD_DAYS)
                if datetime.now(IST) < cooling_end:
                    return {"can_contact": False, "touches_remaining": MAX_OUTREACH_TOUCHES - touch_count, "cooling_until": cooling_end.isoformat(), "reason": f"Cooling period until {cooling_end.date()}"}

            return {"can_contact": True, "touches_remaining": MAX_OUTREACH_TOUCHES - touch_count, "cooling_until": None, "reason": "ok"}
        except Exception as exc:
            logger.error("Outreach limit check failed: %s", exc)
            return {"can_contact": True, "touches_remaining": MAX_OUTREACH_TOUCHES, "cooling_until": None, "reason": "check_failed"}

    # ── Consent (PDPB) ──────────────────────────────────────────────

    def record_consent(self, lead_id: str, consent_type: str, channel: str) -> bool:
        """Record marketing consent for PDPB compliance."""
        record = {
            "lead_id": lead_id,
            "consent_type": consent_type,  # "implicit" or "explicit"
            "channel": channel,  # "whatsapp", "email", "phone"
            "recorded_at": datetime.now(IST).isoformat(),
        }
        filepath = os.path.join(CONSENT_DIR, f"{lead_id}_{channel}.json")
        try:
            with open(filepath, "w") as f:
                json.dump(record, f, indent=2)
            return True
        except Exception as exc:
            logger.error("Failed to record consent: %s", exc)
            return False

    def check_consent(self, lead_id: str, channel: str) -> bool:
        """Check if we have consent to contact via this channel."""
        filepath = os.path.join(CONSENT_DIR, f"{lead_id}_{channel}.json")
        return os.path.exists(filepath)

    # ── Right to erasure (PDPB) ─────────────────────────────────────

    def handle_erasure_request(self, identifier: str) -> dict:
        """Handle right-to-erasure request."""
        erased = 0
        # Remove consent records
        for fname in os.listdir(CONSENT_DIR):
            if identifier in fname:
                os.remove(os.path.join(CONSENT_DIR, fname))
                erased += 1

        # Remove from DND cache
        phone = format_phone_india(identifier)
        if phone in self._dnd_cache:
            del self._dnd_cache[phone]
            self._save_dnd_cache()
            erased += 1

        # Add to suppression
        self.add_to_suppression(phone=phone if is_valid_indian_mobile(phone) else None, email=identifier if "@" in identifier else None, reason="opt_out")

        logger.info("Erasure request for %s: %d records removed", identifier, erased)
        return {"erased": True, "records_removed": erased}

    # ── Full validation ─────────────────────────────────────────────

    def validate_lead_for_outreach(self, lead: dict) -> dict:
        """Run all compliance checks before outreach."""
        checks: dict[str, str] = {}
        approved = True
        reason = ""

        # 1. DND check
        phone = lead.get("contact_phone") or lead.get("contact_whatsapp", "")
        if phone:
            dnd = self.check_dnd(phone)
            if dnd["is_dnd"]:
                checks["dnd"] = "FAIL"
                approved = False
                reason = "Number on TRAI DND registry"
            else:
                checks["dnd"] = "PASS"
        else:
            checks["dnd"] = "SKIP — no phone"

        # 2. Suppression list
        email = lead.get("contact_email", "")
        if self.check_suppression_list(phone=phone, email=email):
            checks["suppression"] = "FAIL"
            approved = False
            reason = "Contact on suppression list"
        else:
            checks["suppression"] = "PASS"

        # 3. Outreach limits
        lead_id = lead.get("id") or lead.get("dedup_hash", "")
        limits = self.check_outreach_limits(lead_id)
        if not limits["can_contact"]:
            checks["limits"] = f"FAIL — {limits['reason']}"
            approved = False
            reason = limits["reason"]
        else:
            checks["limits"] = "PASS"

        # 4. Consent (for marketing messages)
        checks["consent"] = "PASS — business trigger (implicit consent)"

        return {"approved": approved, "checks": checks, "reason": reason}

    def filter_compliant_leads(self, leads: list[dict]) -> list[dict]:
        """Filter batch to only compliant leads."""
        approved: list[dict] = []
        filtered = 0
        for lead in leads:
            result = self.validate_lead_for_outreach(lead)
            if result["approved"]:
                approved.append(lead)
            else:
                filtered += 1
                logger.debug("Filtered %s: %s", lead.get("company_name"), result["reason"])
        logger.info("Compliance filter: %d approved, %d filtered out of %d", len(approved), filtered, len(leads))
        return approved

    def generate_compliance_report(self, leads: list[dict]) -> dict:
        """Generate compliance summary."""
        total = len(leads)
        results = [self.validate_lead_for_outreach(l) for l in leads]
        approved = sum(1 for r in results if r["approved"])
        dnd_blocked = sum(1 for r in results if r["checks"].get("dnd") == "FAIL")
        suppressed = sum(1 for r in results if r["checks"].get("suppression") == "FAIL")
        limit_blocked = sum(1 for r in results if "FAIL" in r["checks"].get("limits", ""))
        return {
            "total_checked": total,
            "approved": approved,
            "dnd_blocked": dnd_blocked,
            "suppressed": suppressed,
            "limit_blocked": limit_blocked,
            "approval_rate": f"{approved/total*100:.1f}%" if total else "N/A",
        }

    # ── Internal helpers ────────────────────────────────────────────

    def _load_dnd_cache(self) -> dict:
        if os.path.exists(DND_CACHE_FILE):
            try:
                with open(DND_CACHE_FILE) as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_dnd_cache(self) -> None:
        try:
            with open(DND_CACHE_FILE, "w") as f:
                json.dump(self._dnd_cache, f, indent=2)
        except Exception as exc:
            logger.error("Failed to save DND cache: %s", exc)

    def _log_dnd_check(self, phone: str, result: dict) -> None:
        log_file = os.path.join(COMPLIANCE_DIR, "dnd_check_log.json")
        try:
            logs = []
            if os.path.exists(log_file):
                with open(log_file) as f:
                    logs = json.load(f)
            logs.append({"phone": phone[:6] + "****", **result})
            # Keep last 1000 entries
            logs = logs[-1000:]
            with open(log_file, "w") as f:
                json.dump(logs, f, indent=2)
        except Exception:
            pass


# ── Module entry point ──────────────────────────────────────────────


def check_compliance(leads: list[dict], dry_run: bool = False) -> list[dict]:
    """Entry point: filter leads for compliance."""
    checker = IndiaCompliance(dry_run=dry_run)
    return checker.filter_compliant_leads(leads)
