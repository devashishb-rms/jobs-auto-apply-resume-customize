"""Unit tests for tailoring/ats_validator.py — fail-closed fact-checking and
keyword coverage (SDA §A4.3).

Run: python -m unittest tests.test_ats_validator -v
"""

from __future__ import annotations

import unittest

from jobs_auto_apply.profile_data import ResumeFacts
from jobs_auto_apply.tailoring import ats_validator
from jobs_auto_apply.tailoring.jd_extractor import StructuredJD


def _facts() -> ResumeFacts:
    return ResumeFacts(
        name="Test Candidate",
        headline="Solution Architect",
        email="test@example.com",
        phone="0000000000",
        location="Bangalore",
        education="B.Tech",
        technical_skills={"languages": ["Python", "Java"], "cloud": ["AWS", "Kubernetes"]},
        experience=[
            {
                "company": "Acme Corp",
                "title": "Architect",
                "period": "2020-2024",
                "highlights": [
                    "Led a 250+ person team, contributing to GMV growth from ₹4 Trillion to over ₹5.5 Trillion",
                    "Reduced case volumes by 40% while keeping false positive rate under 3%",
                ],
            }
        ],
        profile_summary="Experienced architect with AWS and Kubernetes expertise.",
    )


class TestKeywordCoverage(unittest.TestCase):
    def test_full_coverage(self) -> None:
        jd = StructuredJD(must_have=["aws", "kubernetes"])
        score = ats_validator.keyword_coverage("Expert in AWS and Kubernetes deployments.", jd)
        self.assertEqual(score, 1.0)

    def test_partial_coverage(self) -> None:
        jd = StructuredJD(must_have=["aws", "kubernetes", "terraform", "kafka"])
        score = ats_validator.keyword_coverage("Expert in AWS and Kubernetes deployments.", jd)
        self.assertEqual(score, 0.5)

    def test_no_must_have_is_full_coverage(self) -> None:
        jd = StructuredJD(must_have=[])
        self.assertEqual(ats_validator.keyword_coverage("anything", jd), 1.0)

    def test_zero_coverage(self) -> None:
        jd = StructuredJD(must_have=["golang", "rust"])
        score = ats_validator.keyword_coverage("Expert in AWS and Kubernetes.", jd)
        self.assertEqual(score, 0.0)


class TestStripFabricatedClaims(unittest.TestCase):
    def test_known_numeric_claim_is_kept(self) -> None:
        facts = _facts()
        text = "Led a 250+ person team, contributing to GMV growth from ₹4 Trillion to over ₹5.5 Trillion."
        clean, stripped = ats_validator.strip_fabricated_claims(text, facts, ["AWS", "Kubernetes"])
        self.assertEqual(stripped, [])
        self.assertIn("250", clean)

    def test_fabricated_numeric_claim_is_stripped(self) -> None:
        facts = _facts()
        text = "Managed a team of 999 engineers across 12 countries."
        clean, stripped = ats_validator.strip_fabricated_claims(text, facts, ["AWS", "Kubernetes"])
        self.assertEqual(clean, "")
        self.assertEqual(len(stripped), 1)

    def test_mixed_sentences_only_strips_the_bad_one(self) -> None:
        facts = _facts()
        text = (
            "Led a 250+ person team, contributing to GMV growth from ₹4 Trillion to over ₹5.5 Trillion. "
            "Also personally closed 500 enterprise deals."
        )
        clean, stripped = ats_validator.strip_fabricated_claims(text, facts, ["AWS", "Kubernetes"])
        self.assertIn("250", clean)
        self.assertNotIn("500", clean)
        self.assertEqual(len(stripped), 1)

    def test_fabricated_employer_is_stripped(self) -> None:
        facts = _facts()
        text = "Previously led engineering at FakeCompany Technologies."
        clean, stripped = ats_validator.strip_fabricated_claims(text, facts, [])
        self.assertEqual(len(stripped), 1)
        self.assertEqual(clean, "")

    def test_real_employer_reference_is_kept(self) -> None:
        facts = _facts()
        text = "Drove architecture decisions while at Acme Corp."
        clean, stripped = ats_validator.strip_fabricated_claims(text, facts, [])
        self.assertEqual(stripped, [])
        self.assertIn("Acme Corp", clean)


class TestValidateTextSections(unittest.TestCase):
    def test_ok_when_coverage_met_and_no_fabrication(self) -> None:
        facts = _facts()
        jd = StructuredJD(must_have=["aws"])
        sections = {"summary": "Experienced in AWS and Kubernetes."}
        result = ats_validator.validate_text_sections(sections, jd, facts, [], min_keyword_coverage=0.5)
        self.assertTrue(result.ok)
        self.assertEqual(result.stripped_claims, [])

    def test_not_ok_when_coverage_below_target(self) -> None:
        facts = _facts()
        jd = StructuredJD(must_have=["golang", "rust"])
        sections = {"summary": "Experienced in AWS and Kubernetes."}
        result = ats_validator.validate_text_sections(sections, jd, facts, [], min_keyword_coverage=0.5)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
