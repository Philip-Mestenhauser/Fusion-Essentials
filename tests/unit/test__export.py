"""Unit tests for ``_export.py`` - the export-to-disk substrate shared by design_export/mesh_export:
filename sanitizing, the component-by-name resolver, the file-landed verifier, and the
one-file-per-top-level-occurrence split orchestration.
"""

from conftest import load_tool

ex = load_tool("_export")


# ── sanitize ─────────────────────────────────────────────────────────────────

class TestSanitize:
    def test_drops_instance_suffix(self):
        assert ex.sanitize("Loader Arm:1") == "Loader_Arm"

    def test_keeps_safe_chars(self):
        assert ex.sanitize("Part-A_1.v2") == "Part-A_1.v2"

    def test_swaps_illegal_chars(self):
        assert ex.sanitize("A/B\\C:1") == "A_B_C"

    def test_empty_becomes_part(self):
        assert ex.sanitize("") == "part"
        assert ex.sanitize(None) == "part"

    def test_all_illegal_becomes_part(self):
        # base reduces to all-underscore (still non-empty), so it stays underscores, not "part"
        assert ex.sanitize("***") == "___"


# ── component_by_name ─────────────────────────────────────────────────────────

class _Comp:
    def __init__(self, name):
        self.name = name


class _CompColl:
    """allComponents is a COUNTED collection (count + item(i)), not a plain list."""
    def __init__(self, items):
        self._l = list(items)

    @property
    def count(self):
        return len(self._l)

    def item(self, i):
        return self._l[i] if 0 <= i < len(self._l) else None


class _Design:
    def __init__(self, comps):
        self.rootComponent = _Comp("Root")
        self.allComponents = _CompColl(comps)


class TestComponentByName:
    def test_finds_matching_component(self):
        a, b = _Comp("A"), _Comp("B")
        assert ex.component_by_name(_Design([a, b]), "B") is b

    def test_no_match_returns_none(self):
        assert ex.component_by_name(_Design([_Comp("A")]), "Nope") is None

    def test_empty_component_list_returns_none(self):
        # all_components falls back to [root] on an empty collection; "A" still misses
        assert ex.component_by_name(_Design([]), "A") is None


# ── verify_written ─────────────────────────────────────────────────────────────

class TestVerifyWritten:
    def test_existing_nonempty_file_passes(self, tmp_path):
        p = tmp_path / "out.step"
        p.write_text("data")
        size, err = ex.verify_written(str(p))
        assert err is None
        assert size == p.stat().st_size > 0

    def test_missing_file_is_an_error(self, tmp_path):
        size, err = ex.verify_written(str(tmp_path / "missing.step"))
        assert size == 0
        assert "no file was written" in err.lower()
        assert "file_exists=False" in err

    def test_empty_file_is_an_error(self, tmp_path):
        p = tmp_path / "empty.step"
        p.write_text("")
        size, err = ex.verify_written(str(p))
        assert size == 0
        assert "no file was written" in err.lower()
        assert "size_bytes=0" in err


# ── top_level_occurrences ──────────────────────────────────────────────────────

class _Occs:
    def __init__(self, items):
        self._items = list(items)
        self.count = len(self._items)

    def item(self, i):
        return self._items[i]


class _Root:
    def __init__(self, occs):
        self.occurrences = occs


class _OccDesign:
    def __init__(self, occs):
        self.rootComponent = _Root(occs)


class TestTopLevelOccurrences:
    def test_lists_every_occurrence_in_order(self):
        o1, o2 = object(), object()
        occs = ex.top_level_occurrences(_OccDesign(_Occs([o1, o2])))
        assert occs == [o1, o2]

    def test_no_occurrences_is_empty_list(self):
        assert ex.top_level_occurrences(_OccDesign(_Occs([]))) == []

    def test_unreadable_occurrences_collection_is_empty_list(self):
        class _BadRoot:
            @property
            def occurrences(self):
                raise RuntimeError("boom")
        design = _OccDesign(_Occs([]))
        design.rootComponent = _BadRoot()
        assert ex.top_level_occurrences(design) == []


# ── split_by_occurrence ────────────────────────────────────────────────────────

class _Occ:
    def __init__(self, name):
        self.name = name


class TestSplitByOccurrence:
    def test_one_record_per_occurrence_named_and_extensioned(self):
        occs = [_Occ("Body:1"), _Occ("Cab:1")]
        calls = []

        def write_one(occ, path):
            calls.append((occ.name, path))
            return 42, None

        files, errors = ex.split_by_occurrence(occs, "C:/out", ".stl", write_one)
        assert errors == []
        assert [f["occurrence"] for f in files] == ["Body:1", "Cab:1"]
        assert all(f["file_path"].endswith(".stl") for f in files)
        assert all(f["size_bytes"] == 42 for f in files)
        assert [c[0] for c in calls] == ["Body:1", "Cab:1"]

    def test_duplicate_stems_disambiguated(self):
        occs = [_Occ("Wheel:1"), _Occ("Wheel:2")]
        files, errors = ex.split_by_occurrence(occs, "C:/out", ".stl",
                                               lambda occ, path: (1, None))
        paths = [f["file_path"].replace("\\", "/") for f in files]
        assert paths[0].endswith("/Wheel.stl")
        assert paths[1].endswith("/Wheel_2.stl")

    def test_failure_lands_in_errors_not_files(self):
        occs = [_Occ("Good:1"), _Occ("Bad:1")]

        def write_one(occ, path):
            if occ.name == "Bad:1":
                return None, "it exploded"
            return 5, None

        files, errors = ex.split_by_occurrence(occs, "C:/out", ".stl", write_one)
        assert [f["occurrence"] for f in files] == ["Good:1"]
        assert errors == [{"occurrence": "Bad:1", "error": "it exploded"}]

    def test_empty_occurrence_list_yields_nothing(self):
        files, errors = ex.split_by_occurrence([], "C:/out", ".stl", lambda occ, path: (1, None))
        assert files == [] and errors == []
