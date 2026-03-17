"""Tests for MD5 deduplication logic."""

import pytest
from pipeline.utils import generate_dedup_hash


class TestDeduplication:
    def test_same_inputs_same_hash(self):
        h1 = generate_dedup_hash("Razorpay", "seed", "BLR")
        h2 = generate_dedup_hash("Razorpay", "seed", "BLR")
        assert h1 == h2

    def test_different_inputs_different_hash(self):
        h1 = generate_dedup_hash("Razorpay", "seed", "BLR")
        h2 = generate_dedup_hash("Razorpay", "series_a", "BLR")
        assert h1 != h2

    def test_case_insensitive(self):
        h1 = generate_dedup_hash("Razorpay", "seed", "BLR")
        h2 = generate_dedup_hash("razorpay", "seed", "BLR")
        assert h1 == h2

    def test_whitespace_stripped(self):
        h1 = generate_dedup_hash(" Razorpay ", "seed", "BLR")
        h2 = generate_dedup_hash("Razorpay", "seed", "BLR")
        assert h1 == h2

    def test_empty_fields(self):
        h1 = generate_dedup_hash("", "", "")
        h2 = generate_dedup_hash("", "", "")
        assert h1 == h2

    def test_empty_vs_nonempty(self):
        h1 = generate_dedup_hash("", "seed", "BLR")
        h2 = generate_dedup_hash("Razorpay", "seed", "BLR")
        assert h1 != h2

    def test_hash_is_md5(self):
        h = generate_dedup_hash("test", "test", "test")
        assert len(h) == 32  # MD5 hex length
        assert all(c in "0123456789abcdef" for c in h)

    def test_funding_dedup_key(self):
        """Same company + round + city from different sources should dedup."""
        h_entrackr = generate_dedup_hash("PayRight", "seed", "BLR")
        h_inc42 = generate_dedup_hash("PayRight", "seed", "BLR")
        assert h_entrackr == h_inc42

    def test_hiring_dedup_key(self):
        """Hiring uses company + city + week_number."""
        h1 = generate_dedup_hash("CRED", "BLR", "12")
        h2 = generate_dedup_hash("CRED", "BLR", "12")
        assert h1 == h2
        h3 = generate_dedup_hash("CRED", "BLR", "13")
        assert h1 != h3

    def test_cross_source_dedup(self):
        """Same company from Entrackr and Inc42 should produce same hash."""
        h1 = generate_dedup_hash("MedAssist AI", "series_a", "BLR")
        h2 = generate_dedup_hash("MedAssist AI", "series_a", "BLR")
        assert h1 == h2

    def test_different_cities_different_hash(self):
        h1 = generate_dedup_hash("Razorpay", "seed", "BLR")
        h2 = generate_dedup_hash("Razorpay", "seed", "MUM")
        assert h1 != h2
