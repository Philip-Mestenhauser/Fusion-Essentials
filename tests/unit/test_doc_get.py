"""Tests for `doc_get` — the session rich read (active doc identity + open-doc list).

Pins the handler's job: surface the active document's URN/save-state, list the open docs with the terse
razor (a healthy doc collapses to {name, is_active}; an unsaved/modified one keeps its flag), and the
no-active-doc guard. The adsk Document fakes capture the read so a regression to a wrong attribute fails.
"""

import json

from conftest import load_tool, error_message

dg = load_tool("doc_get")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _DataFile:
    def __init__(self, urn="urn:lineage:abc", vnum=3, latest=3):
        self.id = urn
        self.versionId = "urn:version:xyz"
        self.versionNumber = vnum
        self.latestVersionNumber = latest
        self.fusionWebURL = "https://fusion.example/x"


class _Doc:
    def __init__(self, name, saved=True, modified=False, visible=True, data_file=None):
        self.name = name
        self.isSaved = saved
        self.isModified = modified
        self.isVisible = visible
        self.version = "2.0.21"
        self.dataFile = data_file


class _Docs:
    def __init__(self, docs): self._d = docs
    @property
    def count(self): return len(self._d)
    def item(self, i): return self._d[i]


def _install(active=None, open_docs=None):
    """Point dg.app at a fake Application with the given active doc + open-docs list."""
    open_docs = open_docs if open_docs is not None else ([active] if active else [])
    class _App:
        activeDocument = active
        documents = _Docs(open_docs) if open_docs is not None else None
    dg.app = _App()


class TestActiveIdentity:
    def test_saved_doc_surfaces_urn_and_state(self):
        d = _Doc("Bracket", data_file=_DataFile(urn="urn:lineage:abc", vnum=3))
        _install(d)
        out = _payload(dg.handler())
        assert out["active"]["name"] == "Bracket"
        assert out["active"]["document_id"] == "urn:lineage:abc"
        assert out["document_id"] == "urn:lineage:abc"             # also hoisted to top level
        assert out["active"]["has_data_file"] is True
        assert "saved and unmodified" in out["active"]["save_state"]

    def test_unsaved_doc_has_no_urn(self):
        d = _Doc("Untitled", saved=False, data_file=None)
        _install(d)
        out = _payload(dg.handler())
        assert out["active"]["document_id"] is None
        assert out["active"]["has_data_file"] is False
        assert "never saved" in out["active"]["save_state"]

    def test_modified_doc_flags_stale_urn(self):
        d = _Doc("WIP", modified=True, data_file=_DataFile(vnum=5))
        _install(d)
        out = _payload(dg.handler())
        assert "unsaved changes" in out["active"]["save_state"]
        assert "5" in out["active"]["save_state"]


class TestOpenList:
    def test_terse_healthy_doc_collapses(self):
        active = _Doc("A", data_file=_DataFile())
        other = _Doc("B", data_file=_DataFile())          # healthy, not active
        _install(active, [active, other])
        out = _payload(dg.handler())
        assert out["open_count"] == 2
        rows = {r["name"]: r for r in out["open_documents"]}
        # B is healthy + not active -> collapses to just its name (no is_visible/is_saved noise)
        assert rows["B"] == {"name": "B"}
        # A is active -> keeps the is_active flag
        assert rows["A"]["is_active"] is True

    def test_summary_leads_with_unsaved_exceptions(self):
        # the summary names the docs with unsaved work (what close-all would lose) before the
        # full list. Healthy docs are NOT exceptions.
        active = _Doc("Main", data_file=_DataFile())
        clean = _Doc("Clean", data_file=_DataFile())
        never = _Doc("Untitled", saved=False, data_file=None)
        dirty = _Doc("WIP", modified=True, data_file=_DataFile())
        _install(active, [active, clean, never, dirty])
        out = _payload(dg.handler())
        s = out["summary"]
        assert s["open_count"] == 4
        names = {e["name"]: e for e in s["exceptions"]}
        assert set(names) == {"Untitled", "WIP"}                 # clean + active(saved) excluded
        assert names["Untitled"]["unsaved"] == ["never_saved"]
        assert names["WIP"]["unsaved"] == ["modified"]

    def test_modified_dependency_doc_keeps_its_flag(self):
        active = _Doc("Main", data_file=_DataFile())
        dep = _Doc("Ref", modified=True, data_file=_DataFile())
        _install(active, [active, dep])
        rows = {r["name"]: r for r in _payload(dg.handler())["open_documents"]}
        assert rows["Ref"]["is_modified"] is True          # the interesting flag survives the razor


class TestGuards:
    def test_no_active_document_errors(self):
        _install(active=None, open_docs=[])
        res = dg.handler()
        assert "no active document" in error_message(res).lower()


class TestCaps:
    def test_under_cap_untruncated_and_unchanged(self):
        active = _Doc("Main", data_file=_DataFile())
        others = [_Doc(f"D{i}", data_file=_DataFile()) for i in range(5)]
        _install(active, [active] + others)
        out = _payload(dg.handler())
        assert out["truncated"] is False
        assert len(out["open_documents"]) == 6
        assert out["open_count"] == 6

    def test_at_cap_truncates_and_flags(self):
        active = _Doc("Main", data_file=_DataFile())
        others = [_Doc(f"D{i}", data_file=_DataFile()) for i in range(60)]
        _install(active, [active] + others)
        out = _payload(dg.handler(max_results=50))
        assert out["truncated"] is True
        assert len(out["open_documents"]) == 50
        # the full count is still honest, even though the array is capped
        assert out["open_count"] == 61

    def test_unsaved_exceptions_computed_over_the_full_list_even_when_capped(self):
        # a doc with unsaved work beyond the cap must still show up in 'summary.exceptions'.
        active = _Doc("Main", data_file=_DataFile())
        clean = [_Doc(f"D{i}", data_file=_DataFile()) for i in range(60)]
        dirty = _Doc("WIP", modified=True, data_file=_DataFile())
        _install(active, [active] + clean + [dirty])
        out = _payload(dg.handler(max_results=50))
        assert out["truncated"] is True
        names = {e["name"] for e in out["summary"]["exceptions"]}
        assert "WIP" in names


# ── (A) versions slice ────────────────────────────────────────────────────────

class _Ver:
    def __init__(self, num, vid=None, date=1_700_000_000, desc=""):
        self.versionNumber = num
        self.versionId = vid or f"urn:v:{num}"
        self.dateCreated = date
        self.description = desc


class _VerColl:
    def __init__(self, vers): self._v = vers
    @property
    def count(self): return len(self._v)
    def item(self, i): return self._v[i]


class _DFileVers(_Ver):
    """A DataFile that is itself the tip version and carries df.versions for the older ones."""
    def __init__(self, open_num, latest, others, desc="open"):
        super().__init__(open_num, desc=desc)
        self.latestVersionNumber = latest
        self.id = "urn:lineage"
        self.versions = _VerColl(others)


class TestVersions:
    def test_newest_first_and_capped(self):
        # versions given out of order; the slice sorts them newest-first and caps the list.
        df = _DFileVers(open_num=3, latest=5, others=[_Ver(1), _Ver(4), _Ver(2), _Ver(5)])
        _install(_Doc("Bracket", data_file=df))
        out = dg._slice_versions(versions_max=3)
        assert out["available"] is True
        assert [r["version_number"] for r in out["versions"]] == [5, 4, 3]
        assert out["truncated"] is True
        assert out["version_count"] == 5          # 1,2,3(open),4,5 - df merged + deduped

    def test_flags_latest_and_open_version(self):
        df = _DFileVers(open_num=3, latest=5, others=[_Ver(5, desc="tip"), _Ver(4)])
        _install(_Doc("Bracket", data_file=df))
        rows = {r["version_number"]: r for r in dg._slice_versions()["versions"]}
        assert rows[5]["is_latest"] is True
        assert rows[3]["is_open_in_session"] is True
        assert rows[5]["date_utc"] is not None    # epoch -> ISO string

    def test_unsaved_document_has_no_history(self):
        _install(_Doc("Untitled", saved=False, data_file=None))
        out = dg._slice_versions()
        assert out["available"] is False
        assert "never saved" in out["note"].lower()


# ── (B) xref_tree slice ───────────────────────────────────────────────────────

class _OccList:
    def __init__(self, items): self._i = items
    @property
    def count(self): return len(self._i)
    def item(self, i): return self._i[i]


class _DF2:
    def __init__(self, name, latest): self.name = name; self.latestVersionNumber = latest


class _DRef:
    def __init__(self, source, current, latest, ood):
        self.dataFile = _DF2(source, latest)
        self.version = current
        self.isOutOfDate = ood


class _Occ:
    def __init__(self, path, is_ref=False, dref=None, children=None):
        self.fullPathName = path
        self.isReferencedComponent = is_ref
        self.documentReference = dref
        self.childOccurrences = _OccList(children or [])


class _Root:
    def __init__(self, occs): self.occurrences = _OccList(occs)


class _Des:
    def __init__(self, root): self.rootComponent = root


def _use_design(monkeypatch, root):
    monkeypatch.setattr(dg, "design", lambda: _Des(root))


class TestXrefTree:
    def test_deep_stale_reference_is_found(self, monkeypatch):
        # a stale xref nested inside a LOCAL sub-assembly must still be walked and flagged.
        stale = _Occ("Sub:1+Part:1", is_ref=True, dref=_DRef("Part", 2, 5, True))
        sub = _Occ("Sub:1", is_ref=False, children=[stale])
        _use_design(monkeypatch, _Root([sub]))
        out = dg._slice_xref_tree()
        assert out["reference_count"] == 1
        r = out["references"][0]
        assert r["depth"] == 2 and r["out_of_date"] is True and r["source_document"] == "Part"
        assert out["stale_count"] == 1
        assert out["all_current"] is False

    def test_all_current_true_only_when_every_ref_fresh(self, monkeypatch):
        a = _Occ("A:1", is_ref=True, dref=_DRef("A", 3, 3, False))
        b = _Occ("B:1", is_ref=True, dref=_DRef("B", 1, 1, False))
        _use_design(monkeypatch, _Root([a, b]))
        out = dg._slice_xref_tree()
        assert out["reference_count"] == 2
        assert out["stale_count"] == 0
        assert out["all_current"] is True

    def test_cap_truncates_and_blocks_all_current(self, monkeypatch):
        # every EXAMINED ref is fresh, but a capped walk cannot claim all-current (partial knowledge).
        occs = [_Occ(f"R{i}:1", is_ref=True, dref=_DRef(f"R{i}", 1, 1, False)) for i in range(5)]
        _use_design(monkeypatch, _Root(occs))
        out = dg._slice_xref_tree(xref_max=2)
        assert out["truncated"] is True
        assert len(out["references"]) == 2
        assert out["all_current"] is False

    def test_unreadable_reference_blocks_all_current(self, monkeypatch):
        good = _Occ("A:1", is_ref=True, dref=_DRef("A", 1, 1, False))
        bad = _Occ("B:1", is_ref=True, dref=None)   # referenced, but documentReference unreadable
        _use_design(monkeypatch, _Root([good, bad]))
        out = dg._slice_xref_tree()
        assert out["unreadable_count"] == 1
        assert out["all_current"] is False
        bad_row = {x["path"]: x for x in out["references"]}["B:1"]
        assert bad_row["readable"] is False and "warning" in bad_row

    def test_max_depth_bounds_walk_and_flags_partial(self, monkeypatch):
        deep = _Occ("Sub:1+Part:1", is_ref=True, dref=_DRef("Part", 1, 1, False))
        sub = _Occ("Sub:1", is_ref=False, children=[deep])
        _use_design(monkeypatch, _Root([sub]))
        out = dg._slice_xref_tree(max_depth=1)
        assert out["depth_capped"] is True
        assert out["reference_count"] == 0        # the depth-2 ref was not walked
        assert out["all_current"] is False        # a depth-capped walk is partial


# ── (C) used_in slice (where-used / reverse references) ───────────────────────

class _Parent:
    """A parent DataFile - a document that REFERENCES the active one (drawing / assembly)."""
    def __init__(self, name="P", urn="urn:p", ext="f3d", vnum=2, latest=3):
        self.name = name
        self.id = urn
        self.fileExtension = ext
        self.versionNumber = vnum
        self.latestVersionNumber = latest
        self.fusionWebURL = "https://fusion.example/p"


class _Parents:
    """A DataFiles collection (parentReferences); item(i) may return None for an unreadable slot."""
    def __init__(self, items): self._i = items
    @property
    def count(self): return len(self._i)
    def item(self, i): return self._i[i]


class _DFileParents:
    """A DataFile carrying its incoming (parent) references for the used_in slice."""
    def __init__(self, parents, has=True):
        self.hasParentReferences = has
        self.parentReferences = _Parents(parents)


class TestUsedIn:
    def test_drawing_reference_appears_and_is_typed(self):
        # the prove-it bite: a drawing made from this design shows up in used_in, typed 'drawing'.
        drawing = _Parent(name="Bracket Drawing", urn="urn:draw", ext="f2d", vnum=1, latest=1)
        _install(_Doc("Bracket", data_file=_DFileParents([drawing])))
        out = dg._slice_used_in()
        assert out["available"] is True
        assert out["query_complete"] is True
        assert out["reference_count"] == 1
        r = out["references"][0]
        assert r["name"] == "Bracket Drawing"
        assert r["type"] == "drawing"
        assert r["document_id"] == "urn:draw"
        assert out["by_type"] == {"drawing": 1}

    def test_mixed_parents_rolled_up_by_type(self):
        parents = [_Parent(name="Asm", ext="f3d"), _Parent(name="Dwg", ext="f2d")]
        _install(_Doc("Part", data_file=_DFileParents(parents)))
        out = dg._slice_used_in()
        assert out["by_type"] == {"design": 1, "drawing": 1}
        assert out["parent_count"] == 2

    def test_no_references_is_empty_not_error(self):
        # a design nothing uses -> empty list, query_complete True, NOT an error.
        _install(_Doc("Lonely", data_file=_DFileParents([], has=False)))
        out = dg._slice_used_in()
        assert out["available"] is True
        assert out["reference_count"] == 0
        assert out["references"] == []
        assert out["query_complete"] is True
        assert out["has_parent_references"] is False

    def test_cap_truncates_and_blocks_query_complete(self):
        parents = [_Parent(name=f"P{i}") for i in range(5)]
        _install(_Doc("Hub", data_file=_DFileParents(parents)))
        out = dg._slice_used_in(used_in_max=2)
        assert out["truncated"] is True
        assert len(out["references"]) == 2
        assert out["parent_count"] == 5          # the true total stays honest
        assert out["query_complete"] is False    # a capped walk is partial knowledge

    def test_unreadable_parent_does_not_read_as_none(self):
        # a parent slot that can't be resolved must be flagged, not silently dropped - so a
        # short/empty list is never mistaken for 'nothing uses this'.
        good = _Parent(name="Asm")
        _install(_Doc("Part", data_file=_DFileParents([good, None])))
        out = dg._slice_used_in()
        assert out["unreadable_count"] == 1
        assert out["query_complete"] is False

    def test_unsaved_document_has_no_where_used(self):
        _install(_Doc("Untitled", saved=False, data_file=None))
        out = dg._slice_used_in()
        assert out["available"] is False
        assert "never saved" in out["note"].lower()

    def test_query_failure_is_unknown_not_empty(self):
        # parentReferences unreadable -> the relationship is UNKNOWN; must not imply nothing uses this.
        class _DFNoQuery:
            hasParentReferences = None
            @property
            def parentReferences(self): raise RuntimeError("cloud read failed")
        _install(_Doc("Part", data_file=_DFNoQuery()))
        out = dg._slice_used_in()
        assert out["available"] is True
        assert out["query_complete"] is False
        assert "unknown" in out["note"].lower()


# ── router composition ────────────────────────────────────────────────────────

class TestSliceRouter:
    def test_default_projection_omits_cloud_slices_but_advertises_them(self):
        _install(_Doc("A", data_file=_DataFile()))
        out = _payload(dg.handler())
        assert "versions" not in out and "xref_tree" not in out and "used_in" not in out
        assert "include=['versions']" in out["note"]
        assert "include=['xref_tree']" in out["note"]
        assert "include=['used_in']" in out["note"]

    def test_include_adds_only_the_requested_slice(self, monkeypatch):
        _install(_Doc("A", data_file=_DataFile()))
        monkeypatch.setattr(dg, "_slice_versions", lambda versions_max=25: {"marker": "V"})
        monkeypatch.setattr(dg, "_slice_xref_tree", lambda xref_max=50, max_depth=None: {"marker": "X"})
        monkeypatch.setattr(dg, "_slice_used_in", lambda used_in_max=50: {"marker": "U"})
        out = _payload(dg.handler(include=["versions"]))
        assert out["versions"] == {"marker": "V"}
        assert "xref_tree" not in out and "used_in" not in out

    def test_include_used_in_adds_where_used_slice(self, monkeypatch):
        _install(_Doc("A", data_file=_DataFile()))
        monkeypatch.setattr(dg, "_slice_used_in", lambda used_in_max=50: {"marker": "U"})
        out = _payload(dg.handler(include=["used_in"]))
        assert out["used_in"] == {"marker": "U"}
        assert "versions" not in out and "xref_tree" not in out
