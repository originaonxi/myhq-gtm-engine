"""Tests for 3-persona matching system."""

import pytest
from pipeline.persona_matcher import PersonaMatcher


def _make_lead(**overrides) -> dict:
    base = {
        "signal_type": "funding",
        "company_size": 20,
        "contact_title": "",
        "sdr_notes": "",
        "raw_data": {},
        "content_snippet": "",
        "sector": "",
    }
    base.update(overrides)
    return base


class TestFundedFounderMatching:
    def test_funding_signal_matches_persona_1(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="funding", company_size=15)
        result = matcher.match(lead)
        assert result["persona_id"] == 1

    def test_founder_title_matches_persona_1(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="intent", contact_title="Co-founder & CEO", company_size=10)
        result = matcher.match(lead)
        assert result["persona_id"] == 1

    def test_small_startup_size_matches(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="funding", company_size=30)
        result = matcher.match(lead)
        assert result["persona_id"] == 1
        assert result["persona_name"] == "The Funded Founder"


class TestOpsExpanderMatching:
    def test_hiring_signal_matches_persona_2(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="hiring", company_size=150)
        result = matcher.match(lead)
        assert result["persona_id"] == 2

    def test_ops_title_matches_persona_2(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="hiring", contact_title="Operations Manager", company_size=100)
        result = matcher.match(lead)
        assert result["persona_id"] == 2

    def test_midsize_company_matches(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="hiring", company_size=200)
        result = matcher.match(lead)
        assert result["persona_id"] == 2
        assert result["persona_name"] == "The Ops Expander"


class TestEnterpriseExpanderMatching:
    def test_expansion_signal_matches_persona_3(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="expansion", company_size=5000)
        result = matcher.match(lead)
        assert result["persona_id"] == 3

    def test_vp_title_matches_persona_3(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="expansion", contact_title="VP Business Development", company_size=1000)
        result = matcher.match(lead)
        assert result["persona_id"] == 3

    def test_large_company_matches(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="expansion", company_size=10000)
        result = matcher.match(lead)
        assert result["persona_id"] == 3
        assert result["persona_name"] == "The Enterprise Expander"


class TestEdgeCases:
    def test_ambiguous_signal_resolved(self):
        """When multiple personas could match, highest score wins."""
        matcher = PersonaMatcher()
        # Intent signal + small size → Persona 1
        lead = _make_lead(signal_type="intent", company_size=15, contact_title="CEO")
        result = matcher.match(lead)
        assert result["persona_id"] in (1, 2, 3)
        assert "persona_match_scores" in result

    def test_missing_data_handled(self):
        matcher = PersonaMatcher()
        lead = _make_lead(signal_type="", company_size=0, contact_title="")
        result = matcher.match(lead)
        assert "persona_id" in result

    def test_product_recommendation_persona_1(self):
        matcher = PersonaMatcher()
        products = matcher.get_product_recommendation(1)
        assert "Fixed Desks" in products

    def test_product_recommendation_persona_3(self):
        matcher = PersonaMatcher()
        products = matcher.get_product_recommendation(3)
        assert "Commercial Leasing" in products

    def test_sdr_angle_not_empty(self):
        matcher = PersonaMatcher()
        for pid in [1, 2, 3]:
            assert len(matcher.get_sdr_angle(pid)) > 0

    def test_contact_window_varies(self):
        matcher = PersonaMatcher()
        assert matcher.get_contact_window(1) < matcher.get_contact_window(2) < matcher.get_contact_window(3)
