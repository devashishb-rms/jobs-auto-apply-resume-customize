"""Tests for the Tier 1 self-registering PlatformSpec auto-discovery mechanism
(jobs_auto_apply/platform_adapter.py) and the Tier 2 ATS-vendor YAML loader.
"""

from __future__ import annotations

from jobs_auto_apply import platform_adapter
from jobs_auto_apply.ats.detector import detect_ats, load_vendors

EXPECTED_PLATFORMS = {"naukri", "hirist", "wellfound", "instahyre", "cutshort", "uplers", "indeed"}


def test_discover_platforms_finds_exactly_the_seven_known_platforms():
    specs = platform_adapter.discover_platforms()
    assert set(specs) == EXPECTED_PLATFORMS


def test_discover_platforms_skips_non_platform_subpackages():
    specs = platform_adapter.discover_platforms()
    for non_platform in ("answers", "ats", "tailoring", "resilience", "profile"):
        assert non_platform not in specs


def test_every_discovered_spec_has_the_expected_shape():
    specs = platform_adapter.discover_platforms()
    for name, spec in specs.items():
        assert spec.name == name
        assert spec.auth_method in ("browser", "cookies")
        assert callable(spec.collect_listings)
        assert callable(spec.apply_batch)
        assert callable(spec.verify_logged_in)
        assert callable(spec.inject_cookies)
        assert callable(spec.parse_filters)
        assert spec.filters_cls is not object


def test_adapters_registry_matches_discovered_platforms():
    specs = platform_adapter.discover_platforms()
    assert set(platform_adapter.ADAPTERS) == set(specs)
    platform_adapter.assert_adapters_valid()


def test_ats_vendor_yaml_loads_and_matches_known_vendors():
    vendors = load_vendors()
    names = {v.name for v in vendors}
    assert {"greenhouse", "lever", "ashby", "workday", "smartrecruiters"} <= names
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert detect_ats("https://foo.myworkdayjobs.com/en-US/careers/job/1") == "workday"
    assert detect_ats("https://careers.acme.com/job/1") == "careers_portal"
    assert detect_ats("https://acme.com/jobs/1") == "generic"
