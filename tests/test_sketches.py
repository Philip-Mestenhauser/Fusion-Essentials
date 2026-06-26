"""Unit tests for ``sketches.py`` pure logic.

``_scale`` maps a unit string to a cm-per-unit factor (geometry is built in cm,
so a wrong factor silently mis-sizes everything). ``_resolve_plane`` maps a
plane argument — origin-plane aliases (xy/xz/yz and the top/front/right
synonyms, whitespace/case tolerant) or a named construction plane — to a planar
entity. Both are exactly where a quiet bug would put geometry in the wrong place
or scale.
"""

from types import SimpleNamespace

from conftest import load_tool

sk = load_tool("sketches")


# ── _scale: unit -> cm factor ──────────────────────────────────────────────

class TestScale:
    def test_mm(self):
        assert sk._scale("mm") == 0.1

    def test_cm(self):
        assert sk._scale("cm") == 1.0

    def test_inch_aliases_agree(self):
        assert sk._scale("in") == 2.54
        assert sk._scale("inch") == 2.54

    def test_default_when_blank_is_mm(self):
        assert sk._scale("") == 0.1
        assert sk._scale(None) == 0.1

    def test_case_and_whitespace_tolerant(self):
        assert sk._scale("  MM ") == 0.1

    def test_unknown_unit_is_none(self):
        assert sk._scale("furlongs") is None


# ── _resolve_plane: alias + named-plane resolution ─────────────────────────

class _Root:
    """Root component exposing origin construction planes + named construction planes."""
    def __init__(self, named=None):
        # The tool reads getattr(root, f"{key}ConstructionPlane"); provide each.
        self.xYConstructionPlane = SimpleNamespace(tag="xY")
        self.xZConstructionPlane = SimpleNamespace(tag="xZ")
        self.yZConstructionPlane = SimpleNamespace(tag="yZ")
        self._named = named or {}

    @property
    def constructionPlanes(self):
        named = self._named

        class _CP:
            def itemByName(self_inner, name):
                return named.get(name)
        return _CP()


def _design(named=None):
    return SimpleNamespace(rootComponent=_Root(named))


class TestResolvePlane:
    def test_xy_alias(self):
        planar, desc = sk._resolve_plane(_design(), "xy")
        assert planar.tag == "xY"
        assert "origin plane" in desc

    def test_top_alias_maps_to_xy(self):
        planar, desc = sk._resolve_plane(_design(), "top")
        assert planar.tag == "xY"

    def test_front_alias_maps_to_xz(self):
        planar, _ = sk._resolve_plane(_design(), "front")
        assert planar.tag == "xZ"

    def test_right_alias_maps_to_yz(self):
        planar, _ = sk._resolve_plane(_design(), "right")
        assert planar.tag == "yZ"

    def test_whitespace_and_case_tolerant(self):
        planar, _ = sk._resolve_plane(_design(), "  XY Plane ")
        assert planar.tag == "xY"

    def test_named_construction_plane_fallback(self):
        custom = SimpleNamespace(tag="custom")
        planar, desc = sk._resolve_plane(_design(named={"Datum1": custom}), "Datum1")
        assert planar is custom
        assert "Datum1" in desc

    def test_unresolvable_plane_returns_none(self):
        planar, desc = sk._resolve_plane(_design(), "nonsense")
        assert planar is None
        assert desc is None
