"""Tests for TRAI DND and PDPB compliance."""

import pytest
import os
import json
from compliance.india import IndiaCompliance


class TestDNDCheck:
    def test_valid_number_passes_format(self):
        checker = IndiaCompliance(dry_run=True)
        result = checker.check_dnd("+919876543210")
        assert "is_dnd" in result
        assert result["checked_at"] != ""

    def test_invalid_number_not_dnd(self):
        checker = IndiaCompliance(dry_run=True)
        result = checker.check_dnd("12345")
        assert result["is_dnd"] is False
        assert result["source"] == "invalid_number"

    def test_dnd_result_cached(self):
        checker = IndiaCompliance(dry_run=True)
        result1 = checker.check_dnd("+919876543210")
        result2 = checker.check_dnd("+919876543210")
        assert result2["source"] == "cache"


class TestSuppressionList:
    def test_dry_run_not_suppressed(self):
        checker = IndiaCompliance(dry_run=True)
        assert checker.check_suppression_list(phone="+919876543210") is False

    def test_add_to_suppression_dry_run(self):
        checker = IndiaCompliance(dry_run=True)
        assert checker.add_to_suppression(phone="+919876543210", reason="opt_out") is True


class TestOutreachLimits:
    def test_dry_run_always_can_contact(self):
        checker = IndiaCompliance(dry_run=True)
        result = checker.check_outreach_limits("test-lead-123")
        assert result["can_contact"] is True
        assert result["touches_remaining"] == 3


class TestConsent:
    def test_record_consent(self, tmp_path, monkeypatch):
        import compliance.india as ci
        monkeypatch.setattr(ci, "CONSENT_DIR", str(tmp_path))
        checker = IndiaCompliance(dry_run=True)
        assert checker.record_consent("lead-001", "implicit", "whatsapp") is True
        assert os.path.exists(tmp_path / "lead-001_whatsapp.json")

    def test_check_consent(self, tmp_path, monkeypatch):
        import compliance.india as ci
        monkeypatch.setattr(ci, "CONSENT_DIR", str(tmp_path))
        checker = IndiaCompliance(dry_run=True)
        checker.record_consent("lead-002", "explicit", "email")
        assert checker.check_consent("lead-002", "email") is True
        assert checker.check_consent("lead-002", "phone") is False


class TestErasure:
    def test_erasure_request(self, tmp_path, monkeypatch):
        import compliance.india as ci
        monkeypatch.setattr(ci, "CONSENT_DIR", str(tmp_path))
        monkeypatch.setattr(ci, "DND_CACHE_FILE", str(tmp_path / "dnd_cache.json"))
        checker = IndiaCompliance(dry_run=True)
        checker.record_consent("erase-me", "explicit", "whatsapp")
        result = checker.handle_erasure_request("erase-me")
        assert result["erased"] is True
        assert result["records_removed"] >= 1


class TestFullValidation:
    def test_compliant_lead_approved(self):
        checker = IndiaCompliance(dry_run=True)
        lead = {
            "contact_phone": "+919876543210",
            "contact_whatsapp": "+919876543210",
            "contact_email": "test@example.com",
            "dedup_hash": "test-hash-001",
        }
        # In dry run, ~90% should pass DND
        # Run multiple times to get at least one pass
        approved = False
        for _ in range(20):
            checker._dnd_cache = {}  # Reset cache
            result = checker.validate_lead_for_outreach(lead)
            if result["approved"]:
                approved = True
                break
        assert approved

    def test_validation_returns_checks(self):
        checker = IndiaCompliance(dry_run=True)
        lead = {"contact_phone": "+919876543210", "contact_email": "t@e.com", "dedup_hash": "x"}
        result = checker.validate_lead_for_outreach(lead)
        assert "checks" in result
        assert "dnd" in result["checks"]
        assert "suppression" in result["checks"]
        assert "limits" in result["checks"]

    def test_compliance_report(self):
        checker = IndiaCompliance(dry_run=True)
        leads = [
            {"contact_phone": f"+9198765432{i:02d}", "contact_email": f"t{i}@e.com", "dedup_hash": f"h{i}"}
            for i in range(10)
        ]
        report = checker.generate_compliance_report(leads)
        assert report["total_checked"] == 10
        assert report["approved"] + report["dnd_blocked"] + report["suppressed"] + report["limit_blocked"] >= report["total_checked"]
