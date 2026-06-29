"""Unit tests for ``view_screenshot_multi.py`` — capture several orthographic/iso views in one call.

view_screenshot captures ONE viewport per call, so reading a model from a single iso
is error-prone. view_screenshot_multi orients to each requested view, captures each as a
separate image, and returns them interleaved with text labels — so an inference
model sees front/top/right/iso together.

Pinned here (no live Fusion): _parse_views (default set, explicit list, aliases,
unknown-view error, dedupe/order). The saveAsImageFile capture + camera
restore are live-only.
"""

from conftest import load_tool

cv = load_tool("view_screenshot_multi")


class TestParseViews:
    def test_default_set(self):
        # No argument -> a sensible multi-view default (front/top/right/iso).
        views, err = cv._parse_views("")
        assert err is None
        assert views == ["front", "top", "right", "iso-top-right"]

    def test_explicit_comma_list(self):
        views, err = cv._parse_views("front, top")
        assert err is None and views == ["front", "top"]

    def test_whitespace_and_case_tolerant(self):
        views, err = cv._parse_views("  FRONT , Iso-Top-Right ")
        assert err is None and views == ["front", "iso-top-right"]

    def test_dedupes_preserving_order(self):
        views, err = cv._parse_views("front, top, front")
        assert err is None and views == ["front", "top"]

    def test_unknown_view_errors(self):
        views, err = cv._parse_views("front, sideways")
        assert views is None
        assert "sideways" in err

    def test_all_keyword_expands_to_six_orthos(self):
        views, err = cv._parse_views("all")
        assert err is None
        assert views == ["front", "back", "left", "right", "top", "bottom"]

    def test_only_separators_falls_back_to_default(self):
        # a string of only commas/whitespace yields no real tokens -> the default set, not an empty list
        views, err = cv._parse_views(" , , ")
        assert err is None
        assert views == ["front", "top", "right", "iso-top-right"]
