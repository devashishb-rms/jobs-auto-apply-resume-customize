"""Unit tests for tailoring/variant_store.py — similarity matching at the
reuse_threshold boundary (SDA §A8). Uses a temp dir and a fake, deterministic
embedding function instead of loading the real sentence-transformers model
(slow, and not the thing under test here — the boundary/threshold logic is).

Run: python -m unittest tests.test_variant_store -v
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jobs_auto_apply.config import load_config
from jobs_auto_apply.tailoring import variant_store
from jobs_auto_apply.tailoring.jd_extractor import StructuredJD


def _jd(keywords: list[str]) -> StructuredJD:
    return StructuredJD(must_have=keywords, keywords=keywords)


class TestVariantStore(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.base_dir = Path(self._tmp.name)
        (self.base_dir / "profile").mkdir(parents=True)
        (self.base_dir / "profile" / "resume_facts.yaml").write_text("name: Test\n", encoding="utf-8")
        (self.base_dir / "config.yaml").write_text(
            "user:\n  name: Test\n  email: a@b.com\n  phone: \"1\"\n  linkedin: \"\"\n  expected_display_name: Test\n"
            "resume:\n  path: resume.pdf\n",
            encoding="utf-8",
        )
        self.config = load_config(self.base_dir / "config.yaml")
        self.config.base_dir = self.base_dir

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _fake_embed(self, vector):
        return mock.patch.object(variant_store, "embed_structured_jd", return_value=vector)

    def test_no_match_on_empty_store(self) -> None:
        with self._fake_embed([1.0, 0.0, 0.0]):
            self.assertIsNone(variant_store.find_similar(_jd(["aws"]), self.config))

    def test_reuse_above_threshold(self) -> None:
        self.config.tailoring.reuse_threshold = 0.86
        pdf = self.base_dir / "data" / "resume_variants" / "v1.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake")

        with self._fake_embed([1.0, 0.0, 0.0]):
            vid = variant_store.save(
                _jd(["aws", "kubernetes"]), resume_pdf_path=pdf, ats_keyword_coverage=0.9, config=self.config
            )
        self.assertIsNotNone(vid)

        # Near-identical vector (cosine similarity ~1.0) -> above threshold -> HIT
        with self._fake_embed([0.99, 0.01, 0.0]):
            match = variant_store.find_similar(_jd(["aws", "kubernetes"]), self.config)
        self.assertIsNotNone(match)
        self.assertEqual(match.variant_id, vid)
        self.assertGreaterEqual(match.similarity, self.config.tailoring.reuse_threshold)

    def test_miss_below_threshold(self) -> None:
        self.config.tailoring.reuse_threshold = 0.86
        pdf = self.base_dir / "data" / "resume_variants" / "v2.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake")

        with self._fake_embed([1.0, 0.0, 0.0]):
            variant_store.save(_jd(["aws"]), resume_pdf_path=pdf, ats_keyword_coverage=0.9, config=self.config)

        # Orthogonal vector -> cosine similarity 0.0 -> below threshold -> MISS
        with self._fake_embed([0.0, 1.0, 0.0]):
            match = variant_store.find_similar(_jd(["golang"]), self.config)
        self.assertIsNone(match)

    def test_stale_base_resume_version_never_matches(self) -> None:
        self.config.tailoring.reuse_threshold = 0.5
        pdf = self.base_dir / "data" / "resume_variants" / "v3.pdf"
        pdf.parent.mkdir(parents=True, exist_ok=True)
        pdf.write_bytes(b"%PDF-1.4 fake")
        with self._fake_embed([1.0, 0.0, 0.0]):
            variant_store.save(_jd(["aws"]), resume_pdf_path=pdf, ats_keyword_coverage=0.9, config=self.config)

        # Simulate the user editing resume_facts.yaml — base_resume_version changes.
        (self.base_dir / "profile" / "resume_facts.yaml").write_text("name: Test Updated\n", encoding="utf-8")

        with self._fake_embed([1.0, 0.0, 0.0]):
            match = variant_store.find_similar(_jd(["aws"]), self.config)
        self.assertIsNone(match, "a stale variant (old base_resume_version) must never be matched")


if __name__ == "__main__":
    unittest.main()
