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
    # ADAPTERS is lazily populated on first get_adapter() call (see
    # platform_adapter._ensure_initialized's docstring — this avoids a real
    # import-order bug found in code review, where eager population at module-
    # import time could silently drop a platform if something imported that
    # platform's package before platform_adapter itself). Force it here before
    # reading ADAPTERS directly.
    platform_adapter.get_adapter("naukri")
    specs = platform_adapter.discover_platforms()
    assert set(platform_adapter.ADAPTERS) == set(specs)
    platform_adapter.assert_adapters_valid()


def test_get_adapter_on_a_platform_never_touched_yet_still_finds_it():
    # Loose regression coverage for the import-order fix: get_adapter() must
    # return every platform regardless of which one is asked for first, not
    # just the ones already touched by earlier tests/imports in this process.
    # (The precise bug this fix addresses — a platform package importing
    # platform_adapter before its own module-level PLATFORM attribute is set —
    # is inherently a fresh-process/first-import phenomenon that a
    # single-process pytest run can't reproduce faithfully; it was verified
    # directly via a fresh `python -c "import jobs_auto_apply.wellfound; ..."`
    # subprocess as part of this fix, confirming wellfound is found even when
    # its own package triggers platform_adapter's first import.)
    for name in EXPECTED_PLATFORMS:
        assert platform_adapter.get_adapter(name) is not None, f"{name} missing from registry"


def test_ats_vendor_yaml_loads_and_matches_known_vendors():
    vendors = load_vendors()
    names = {v.name for v in vendors}
    assert {"greenhouse", "lever", "ashby", "workday", "smartrecruiters"} <= names
    assert detect_ats("https://boards.greenhouse.io/acme/jobs/1") == "greenhouse"
    assert detect_ats("https://foo.myworkdayjobs.com/en-US/careers/job/1") == "workday"
    assert detect_ats("https://careers.acme.com/job/1") == "careers_portal"
    assert detect_ats("https://acme.com/jobs/1") == "generic"
