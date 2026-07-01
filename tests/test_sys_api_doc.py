"""Unit tests for ``api_doc.py`` — live Fusion-API documentation search.

The tool introspects the real ``adsk.*`` modules at call time via
``importlib.import_module`` + ``inspect``. To exercise its logic deterministically
without a live Fusion, the tests below register tiny FAKE ``adsk.*`` modules
(holding real Python classes with docstrings/signatures) into ``sys.modules`` and
point the tool's ``_API_MODULES`` at them. That drives the actual collection path
— class-name vs. member-name vs. docstring matching, the namespace/class filter,
result caps, and regex/empty-input validation — over known content.

The pure string helpers (``_class_filter_from``, ``_trim``, ``_signature``,
``_load_modules`` filtering) are tested directly.
"""

import json
import sys
import types

from conftest import load_tool

ad = load_tool("sys_api_doc")


# ── fake adsk.* modules with real introspectable classes ────────────────────

class _FakeCore:
    class Vector3D:
        """A 3D vector."""
        def normalize(self):
            """Normalizes the vector in place. Returns bool success."""
            return True

    class MeasureManager:
        """Access to measurement operations."""
        def getOrientedBoundingBox(self, geometry, lengthVector, widthVector):
            """Calculates an oriented bounding box. The height direction is
            automatically determined."""
            return None


class _FakeFusion:
    class ExtrudeFeatureInput:
        """Defines an extrude feature's inputs."""
        @property
        def profile(self):
            """Gets and sets the profiles used to define the extrude."""
            return None

    class ExtrudeFeatures:
        """Collection of extrude features."""
        def add(self, inp):
            """Creates a new extrude feature."""
            return None


def _install_fake_api(monkeypatch):
    """Register fake adsk.core / adsk.fusion modules and point the tool at them."""
    core_mod = types.ModuleType("adsk.core")
    fusion_mod = types.ModuleType("adsk.fusion")
    # Classes must report __module__ starting with 'adsk' (the tool filters on that).
    for name, cls in vars(_FakeCore).items():
        if isinstance(cls, type):
            cls.__module__ = "adsk.core"
            setattr(core_mod, name, cls)
    for name, cls in vars(_FakeFusion).items():
        if isinstance(cls, type):
            cls.__module__ = "adsk.fusion"
            setattr(fusion_mod, name, cls)
    monkeypatch.setitem(sys.modules, "adsk.core", core_mod)
    monkeypatch.setitem(sys.modules, "adsk.fusion", fusion_mod)
    # Restrict the search universe to just these two so other (mocked) submodules
    # don't bleed in.
    monkeypatch.setattr(ad, "_API_MODULES", ("adsk.core", "adsk.fusion"))


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── pure helpers ─────────────────────────────────────────────────────────────

class TestClassFilterFrom:
    def test_extracts_titlecase_class(self):
        assert ad._class_filter_from("adsk.fusion.Extrude") == "Extrude"

    def test_namespace_only_has_no_class(self):
        assert ad._class_filter_from("adsk.cam") is None

    def test_empty_is_none(self):
        assert ad._class_filter_from("") is None


class TestTrim:
    def test_none_is_empty(self):
        assert ad._trim(None) == ""

    def test_short_doc_unchanged(self):
        assert ad._trim("hello") == "hello"

    def test_long_doc_truncated_with_ellipsis(self):
        out = ad._trim("x" * (ad._DOC_CHARS + 50))
        assert out.endswith(" ...")                  # ASCII ellipsis
        assert len(out) <= ad._DOC_CHARS + 4


class TestSignature:
    def test_returns_signature_string_for_function(self):
        def f(a, b=1):
            pass
        assert ad._signature(f) == "(a, b=1)"

    def test_unsignable_returns_none(self):
        assert ad._signature(42) is None


class TestLoadModulesFilter:
    def test_namespace_filter_scopes_modules(self, monkeypatch):
        _install_fake_api(monkeypatch)
        mods = ad._load_modules("adsk.fusion")
        names = [n for n, _ in mods]
        assert names == ["adsk.fusion"]          # core excluded

    def test_no_filter_loads_all_in_scope(self, monkeypatch):
        _install_fake_api(monkeypatch)
        mods = ad._load_modules("")
        assert {n for n, _ in mods} == {"adsk.core", "adsk.fusion"}

    def test_class_filter_keeps_its_namespace(self, monkeypatch):
        _install_fake_api(monkeypatch)
        # 'adsk.fusion.Extrude' -> module part is adsk.fusion
        mods = ad._load_modules("adsk.fusion.ExtrudeFeatures")
        assert [n for n, _ in mods] == ["adsk.fusion"]


# ── handler validation ───────────────────────────────────────────────────────

class TestValidation:
    def test_empty_pattern_errors(self, monkeypatch):
        _install_fake_api(monkeypatch)
        res = ad.handler(searchPattern="")
        assert res["isError"] is True and "Provide 'searchPattern'" in res["message"]

    def test_invalid_regex_errors(self, monkeypatch):
        _install_fake_api(monkeypatch)
        res = ad.handler(searchPattern="bound[")
        assert res["isError"] is True and "Invalid regex" in res["message"]

    def test_bad_category_errors(self, monkeypatch):
        _install_fake_api(monkeypatch)
        res = ad.handler(searchPattern="x", apiCategory="bogus")
        assert res["isError"] is True and "apiCategory must be one of" in res["message"]

    def test_unknown_filter_namespace_errors(self, monkeypatch):
        _install_fake_api(monkeypatch)
        res = ad.handler(searchPattern="x", filter="adsk.nonsense")
        assert res["isError"] is True and "No API modules in scope" in res["message"]


# ── handler search behaviour ─────────────────────────────────────────────────

class TestClassSearch:
    def test_class_name_match(self, monkeypatch):
        _install_fake_api(monkeypatch)
        out = _payload(ad.handler(searchPattern="^Extrude", apiCategory="class"))
        names = {c["name"] for c in out["classes"]}
        assert names == {"ExtrudeFeatureInput", "ExtrudeFeatures"}
        assert out["members"] == []
        # each carries namespace + doc
        assert all(c["namespace"] == "adsk.fusion" for c in out["classes"])

    def test_namespace_filter_scopes_classes(self, monkeypatch):
        _install_fake_api(monkeypatch)
        out = _payload(ad.handler(searchPattern=".", apiCategory="class", filter="adsk.core"))
        assert {c["name"] for c in out["classes"]} == {"Vector3D", "MeasureManager"}


class TestMemberSearch:
    def test_member_name_match_carries_signature(self, monkeypatch):
        _install_fake_api(monkeypatch)
        out = _payload(ad.handler(searchPattern="getOrientedBoundingBox",
                                  apiCategory="member"))
        assert len(out["members"]) == 1
        m = out["members"][0]
        assert m["name"] == "getOrientedBoundingBox"
        assert m["class"] == "MeasureManager"
        assert "lengthVector" in m["signature"]      # real signature introspected
        assert out["classes"] == []

    def test_class_filter_scopes_members(self, monkeypatch):
        _install_fake_api(monkeypatch)
        out = _payload(ad.handler(searchPattern="profile", apiCategory="member",
                                  filter="adsk.fusion.ExtrudeFeatureInput"))
        names = {m["name"] for m in out["members"]}
        assert names == {"profile"}
        assert out["members"][0]["class"] == "ExtrudeFeatureInput"

    def test_property_vs_function_kind(self, monkeypatch):
        _install_fake_api(monkeypatch)
        out = _payload(ad.handler(searchPattern="profile", apiCategory="member",
                                  filter="adsk.fusion.ExtrudeFeatureInput"))
        assert out["members"][0]["type"] == "property"


class TestDescriptionSearch:
    def test_matches_docstring_text_not_name(self, monkeypatch):
        _install_fake_api(monkeypatch)
        # 'height direction' appears only in getOrientedBoundingBox's DOCSTRING.
        out = _payload(ad.handler(searchPattern="height direction",
                                  apiCategory="description"))
        member_names = {m["name"] for m in out["members"]}
        assert "getOrientedBoundingBox" in member_names


class TestCaps:
    def test_max_results_clamped_and_truncation_flagged(self, monkeypatch):
        _install_fake_api(monkeypatch)
        # Ask for 1 class; there are 2 matching 'Extrude' -> truncated.
        out = _payload(ad.handler(searchPattern="^Extrude", apiCategory="class",
                                  max_results=1))
        assert len(out["classes"]) == 1
        assert out["truncated"] is True

    def test_max_results_never_exceeds_hard_cap(self, monkeypatch):
        _install_fake_api(monkeypatch)
        out = _payload(ad.handler(searchPattern=".", max_results=99999))
        # hard cap is _MAX_RESULTS; we can't return more than the universe has,
        # but the cap must not be exceeded.
        assert len(out["classes"]) <= ad._MAX_RESULTS
        assert len(out["members"]) <= ad._MAX_RESULTS
