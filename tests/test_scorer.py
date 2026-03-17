"""Tests for myHQ GTM Engine intent scoring with Indian market data."""

import pytest
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))


def _make_lead(**overrides) -> dict:
    """Helper to build a lead dict with sensible defaults."""
    base = {
        "signal_type": "funding",
        "announcement_date": datetime.now(IST).isoformat(),
        "company_size": 20,
        "persona_id": 1,
        "contact_phone": "+919876543210",
        "contact_whatsapp": "+919876543210",
        "contact_email": "founder@startup.in",
        "contact_linkedin": "linkedin.com/in/founder",
        "city": "BLR",
        "sector": "B2B SaaS",
        "delta": None,
        "urgency_level": None,
        "source": "entrackr",
    }
    base.update(overrides)
    return base


class TestTriggerRecency:
    def test_fresh_signal_scores_20(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(announcement_date=(datetime.now(IST) - timedelta(hours=6)).isoformat())
        assert scorer._score_trigger_recency(lead) == 20

    def test_one_day_old_scores_17(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(announcement_date=(datetime.now(IST) - timedelta(days=2)).isoformat())
        assert scorer._score_trigger_recency(lead) == 17

    def test_week_old_scores_13(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(announcement_date=(datetime.now(IST) - timedelta(days=5)).isoformat())
        assert scorer._score_trigger_recency(lead) == 13

    def test_two_weeks_old_scores_8(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(announcement_date=(datetime.now(IST) - timedelta(days=10)).isoformat())
        assert scorer._score_trigger_recency(lead) == 8

    def test_stale_signal_scores_3(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(announcement_date=(datetime.now(IST) - timedelta(days=30)).isoformat())
        assert scorer._score_trigger_recency(lead) == 3


class TestTriggerStrength:
    def test_funding_signal_scores_20(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(signal_type="funding")
        assert scorer._score_trigger_strength(lead) == 20

    def test_high_hiring_velocity_scores_20(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(signal_type="hiring", delta=12)
        assert scorer._score_trigger_strength(lead) == 20

    def test_medium_hiring_velocity_scores_15(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(signal_type="hiring", delta=7)
        assert scorer._score_trigger_strength(lead) == 15

    def test_social_intent_high_urgency_scores_18(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(signal_type="intent", urgency_level="high")
        assert scorer._score_trigger_strength(lead) == 18

    def test_expansion_press_release_scores_18(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(signal_type="expansion", source="press_release")
        assert scorer._score_trigger_strength(lead) == 18


class TestCompanyFit:
    def test_funded_startup_perfect_fit(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(persona_id=1, company_size=15)
        assert scorer._score_company_fit(lead) == 20

    def test_ops_expander_fit(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(persona_id=2, company_size=120)
        assert scorer._score_company_fit(lead) == 18

    def test_enterprise_fit(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(persona_id=3, company_size=5000)
        assert scorer._score_company_fit(lead) == 18

    def test_freelancer_low_fit(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(persona_id=1, company_size=2)
        assert scorer._score_company_fit(lead) == 8

    def test_outside_icp(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(persona_id=None, company_size=0)
        assert scorer._score_company_fit(lead) == 3


class TestReachability:
    def test_mobile_whatsapp_max(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(contact_phone="+919876543210", contact_whatsapp="+919876543210")
        assert scorer._score_reachability(lead) == 20

    def test_email_linkedin(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(contact_phone="", contact_whatsapp="", contact_email="a@b.com", contact_linkedin="linkedin.com/in/x")
        assert scorer._score_reachability(lead) == 17

    def test_linkedin_only(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(contact_phone="", contact_whatsapp="", contact_email="", contact_linkedin="linkedin.com/in/x")
        assert scorer._score_reachability(lead) == 12

    def test_no_contact(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(contact_phone="", contact_whatsapp="", contact_email="", contact_linkedin="")
        assert scorer._score_reachability(lead) == 0


class TestCityProductFit:
    def test_bangalore_tech(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(city="BLR", sector="tech startup")
        # BLR base=20, could get bonus for tech
        assert scorer._score_city_product_fit(lead) == 20

    def test_mumbai(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(city="MUM", sector="finance")
        assert scorer._score_city_product_fit(lead) >= 18

    def test_non_target_city(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(city="CHN")
        assert scorer._score_city_product_fit(lead) == 0


class TestTierDetermination:
    def test_hot_tier(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        assert scorer._determine_tier(85) == "HOT"

    def test_warm_tier(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        assert scorer._determine_tier(70) == "WARM"

    def test_nurture_tier(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        assert scorer._determine_tier(50) == "NURTURE"

    def test_monitor_tier(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        assert scorer._determine_tier(25) == "MONITOR"


class TestEndToEnd:
    def test_perfect_bengaluru_funded_startup_is_hot(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        lead = _make_lead(
            signal_type="funding",
            announcement_date=(datetime.now(IST) - timedelta(hours=6)).isoformat(),
            company_size=15,
            persona_id=1,
            contact_phone="+919876543210",
            contact_whatsapp="+919876543210",
            contact_email="founder@startup.in",
            contact_linkedin="linkedin.com/in/founder",
            city="BLR",
            sector="B2B SaaS",
        )
        result = scorer.score_lead(lead)
        assert result["intent_score"] >= 80
        assert result["tier"] == "HOT"

    def test_batch_scoring_sorts_descending(self):
        from pipeline.scorer import IntentScorer
        scorer = IntentScorer()
        leads = [
            _make_lead(signal_type="intent", urgency_level="low", company_size=2, contact_phone="", contact_whatsapp="", contact_email="", contact_linkedin="", city="PUN"),
            _make_lead(signal_type="funding", company_size=15, city="BLR"),
        ]
        result = scorer.score_batch(leads)
        assert result[0]["intent_score"] >= result[1]["intent_score"]
