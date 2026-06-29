"""Unit tests for ``visibility.py`` occurrence resolution + state read.

``_find_occurrences`` resolves a target string to occurrences, preferring exact
name/path matches and only falling back to substring matches when there's no
exact hit — the distinction that keeps "Vise" from also grabbing "Vise_Jaw"
when an exact "Vise" exists. ``_occ_state`` is the before/after snapshot the
tool returns so a caller can restore visibility.
"""

from types import SimpleNamespace

from conftest import load_tool

vis = load_tool("view_set_visibility")


def _occ(name, full_path=None, light=True, visible=True, isolated=False):
    return SimpleNamespace(
        name=name,
        fullPathName=full_path or name,
        isLightBulbOn=light,
        isVisible=visible,
        isIsolated=isolated,
    )


def _design(occs):
    return SimpleNamespace(
        rootComponent=SimpleNamespace(allOccurrences=list(occs))
    )


class TestFindOccurrences:
    def test_exact_match_preferred_over_substring(self):
        exact = _occ("Vise")
        partial = _occ("Vise_Jaw")
        design = _design([exact, partial])
        matches, names = vis._find_occurrences(design, "Vise")
        # Exact "Vise" exists, so the substring "Vise_Jaw" must NOT be included.
        assert matches == [exact]

    def test_substring_fallback_when_no_exact(self):
        a = _occ("Vise_Body")
        b = _occ("Vise_Jaw")
        design = _design([a, b])
        matches, names = vis._find_occurrences(design, "Vise")
        # No exact "Vise" -> both substring matches returned.
        assert matches == [a, b]

    def test_matches_on_full_path(self):
        occ = _occ("Jaw:1", full_path="Vise:1/Jaw:1")
        design = _design([occ])
        matches, _ = vis._find_occurrences(design, "Vise:1/Jaw:1")
        assert matches == [occ]

    def test_no_match_returns_empty(self):
        design = _design([_occ("Vise")])
        matches, names = vis._find_occurrences(design, "Clamp")
        assert matches == []
        assert "Vise" in names   # still reports available names for the agent


class TestOccState:
    def test_snapshots_all_visibility_flags(self):
        occ = _occ("Vise", light=True, visible=False, isolated=True)
        state = vis._occ_state(occ)
        assert state["name"] == "Vise"
        assert state["is_light_bulb_on"] is True
        assert state["is_visible"] is False
        assert state["is_isolated"] is True
