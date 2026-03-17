"""myHQ GTM Engine — WhatsApp Business API message formatting.

India-first outreach channel. All messages formatted for WhatsApp Business API.
Rules: Under 100 words, first name always, 1-2 emojis max, opt-out included.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from config.settings import DRY_RUN, MAX_OUTREACH_TOUCHES, WHATSAPP_PHONE_ID, WHATSAPP_TOKEN
from pipeline.utils import IST, format_phone_india, is_valid_indian_mobile, upsert_to_supabase

logger = logging.getLogger(__name__)


class WhatsAppFormatter:
    """Formats and manages WhatsApp Business API messages for Indian market."""

    OPT_OUT = "\n\nReply STOP to unsubscribe"
    WORD_LIMIT = 100

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.templates = self._generate_default_templates()

    def format_message(self, lead: dict, message: str, template_category: str = "marketing") -> dict:
        """Format a message for WhatsApp Business API submission."""
        phone = format_phone_india(lead.get("contact_phone") or lead.get("contact_whatsapp", ""))
        valid, issues = self.validate_message(message)
        if not valid:
            message = self._fix_message(message, issues)

        return {
            "to": phone,
            "type": "template" if template_category else "text",
            "template_category": template_category,
            "body": message,
            "variables": {
                "1": (lead.get("contact_name") or "there").split()[0],
                "2": lead.get("company_name", "your company"),
            },
            "lead_id": lead.get("id"),
            "persona_id": lead.get("persona_id"),
        }

    def validate_message(self, message: str) -> tuple[bool, list[str]]:
        """Validate message against WhatsApp rules."""
        issues: list[str] = []
        words = message.split()
        if len(words) > self.WORD_LIMIT:
            issues.append(f"Too long: {len(words)} words (max {self.WORD_LIMIT})")
        if "dear sir" in message.lower() or "dear madam" in message.lower():
            issues.append("Formal greeting detected — use first name")
        if "stop" not in message.lower() and "unsubscribe" not in message.lower():
            issues.append("Missing opt-out text")
        return (len(issues) == 0, issues)

    def _fix_message(self, message: str, issues: list[str]) -> str:
        """Auto-fix common message issues."""
        if any("opt-out" in i.lower() or "missing" in i.lower() for i in issues):
            message = self._add_opt_out(message)
        if any("too long" in i.lower() for i in issues):
            message = self._truncate_to_limit(message)
        return message

    def _add_opt_out(self, message: str) -> str:
        if "stop" not in message.lower():
            return message.rstrip() + self.OPT_OUT
        return message

    def _truncate_to_limit(self, message: str, limit: int = 100) -> str:
        words = message.split()
        if len(words) <= limit:
            return message
        truncated = " ".join(words[:limit - 5])
        # Keep opt-out
        if "stop" not in truncated.lower():
            truncated += self.OPT_OUT
        return truncated

    def format_for_api(self, phone: str, template_name: str, variables: dict) -> dict:
        """Format payload for WhatsApp Cloud API."""
        return {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": "en"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": v} for v in variables.values()
                        ],
                    }
                ],
            },
        }

    def send_message(self, phone: str, message: str, template_name: str | None = None) -> dict:
        """Send via WhatsApp Business API (dry run: log only)."""
        phone = format_phone_india(phone)
        if not is_valid_indian_mobile(phone):
            return {"sent": False, "error": "Invalid Indian mobile number"}

        if self.dry_run:
            logger.info("[DRY RUN] WhatsApp to %s: %s", phone, message[:80])
            return {"sent": True, "dry_run": True, "to": phone}

        if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
            return {"sent": False, "error": "WhatsApp credentials not configured"}

        import requests
        url = f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages"
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}", "Content-Type": "application/json"}

        if template_name:
            payload = self.format_for_api(phone, template_name, {"1": message})
        else:
            payload = {"messaging_product": "whatsapp", "to": phone, "type": "text", "text": {"body": message}}

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            return {"sent": True, "response": resp.json()}
        except Exception as exc:
            logger.error("WhatsApp send failed: %s", exc)
            return {"sent": False, "error": str(exc)}

    def get_templates(self) -> list[dict]:
        return self.templates

    def _generate_default_templates(self) -> list[dict]:
        return [
            {
                "template_name": "funded_founder_intro",
                "category": "marketing",
                "language": "en",
                "body_text": "Hi {{1}} 👋 Congrats on the raise at {{2}}! We help funded startups get office-ready in 48 hours across 5 cities. Most founders need space within 60 days. Worth a quick call? Reply STOP to unsubscribe",
                "variables": ["contact_first_name", "company_name"],
                "status": "draft",
            },
            {
                "template_name": "funded_founder_followup",
                "category": "marketing",
                "language": "en",
                "body_text": "Hi {{1}}, following up — we recently set up a 20-seat office for a startup like {{2}} in just 72 hours. GST billing from day one. Happy to share options. Reply STOP to unsubscribe",
                "variables": ["contact_first_name", "company_name"],
                "status": "draft",
            },
            {
                "template_name": "ops_expander_intro",
                "category": "marketing",
                "language": "en",
                "body_text": "Hi {{1}} 👋 Noticed {{2}} is scaling up in {{3}}. At myHQ we handle workspace end-to-end — shortlisting, site visits, GST invoicing. One platform, zero hassle. Quick call? Reply STOP to unsubscribe",
                "variables": ["contact_first_name", "company_name", "city"],
                "status": "draft",
            },
            {
                "template_name": "ops_expander_followup",
                "category": "marketing",
                "language": "en",
                "body_text": "Hi {{1}}, just a quick follow-up. We work with ops teams at companies like {{2}} — handling everything so you can present one vetted recommendation to leadership. Interested? Reply STOP to unsubscribe",
                "variables": ["contact_first_name", "company_name"],
                "status": "draft",
            },
            {
                "template_name": "enterprise_intro",
                "category": "marketing",
                "language": "en",
                "body_text": "Hi {{1}} 👋 Saw that {{2}} is expanding to {{3}}. myHQ specialises in enterprise managed offices — full compliance docs, dedicated account manager, setup in days. Can we schedule a call? Reply STOP to unsubscribe",
                "variables": ["contact_first_name", "company_name", "city"],
                "status": "draft",
            },
            {
                "template_name": "enterprise_followup",
                "category": "marketing",
                "language": "en",
                "body_text": "Hi {{1}}, circling back on workspace options for {{2}} in {{3}}. We have 100+ seat managed offices available with all compliance documentation ready. Happy to share a shortlist. Reply STOP to unsubscribe",
                "variables": ["contact_first_name", "company_name", "city"],
                "status": "draft",
            },
        ]

    def store_templates(self, templates: list[dict] | None = None) -> int:
        templates = templates or self.templates
        if self.dry_run:
            return len(templates)
        count = 0
        for t in templates:
            if upsert_to_supabase("whatsapp_templates", t, dedup_field="template_name"):
                count += 1
        return count


def format_whatsapp_messages(leads: list[dict], dry_run: bool = False) -> list[dict]:
    """Entry point for WhatsApp formatting."""
    formatter = WhatsAppFormatter(dry_run=dry_run)
    formatted: list[dict] = []
    for lead in leads:
        if lead.get("contact_whatsapp") or lead.get("contact_phone"):
            msg = formatter.format_message(lead, lead.get("_whatsapp_text", ""))
            formatted.append(msg)
    return formatted
