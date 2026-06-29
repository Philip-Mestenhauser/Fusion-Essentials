"""Unit tests for ``design_get_tree.py`` occurrence search.

``_find_occurrence_by_name`` is a bounded depth-first search matching either a
substring of the occurrence name or an exact (lowercased) component name. The
bug surface: the substring-vs-exact distinction, case-insensitivity, and
descent into child occurrences.
"""

from types import SimpleNamespace

from conftest import load_tool

ctree = load_tool("design_get_tree")


def _occ(name, comp_name=None, children=()):
    return SimpleNamespace(
        name=name,
        component=SimpleNamespace(name=comp_name or name),
        childOccurrences=list(children),
    )


def _root(occurrences):
    return SimpleNamespace(occurrences=list(occurrences))


class TestFindOccurrenceByName:
    def test_substring_match_on_occurrence_name(self):
        root = _root([_occ("Vise_Body:1", comp_name="Vise")])
        found = ctree._find_occurrence_by_name(root, "vise_body")
        assert found is not None
        assert found.name == "Vise_Body:1"

    def test_exact_match_on_component_name(self):
        # component-name match is exact (not substring): "vise" == comp.name.lower()
        root = _root([_occ("inst:1", comp_name="Vise")])
        found = ctree._find_occurrence_by_name(root, "vise")
        assert found is not None
        assert found.component.name == "Vise"

    def test_descends_into_children(self):
        child = _occ("Jaw:1", comp_name="Jaw")
        parent = _occ("Vise:1", comp_name="Vise", children=[child])
        root = _root([parent])
        found = ctree._find_occurrence_by_name(root, "jaw")
        assert found is found  # sanity
        assert found.name == "Jaw:1"

    def test_no_match_returns_none(self):
        root = _root([_occ("Vise:1", comp_name="Vise")])
        assert ctree._find_occurrence_by_name(root, "nonexistent") is None

    def test_empty_tree_returns_none(self):
        assert ctree._find_occurrence_by_name(_root([]), "anything") is None


class TestNodeShape:
    """The serialized node must carry full_path — the UNAMBIGUOUS instance key the OccurrenceRef kind
    resolves by (name alone is only locally unique). Guards the design_get_tree <-> OccurrenceRef seam."""

    def _walk_occ(self, name, full_path):
        from types import SimpleNamespace

        class _Coll(list):
            @property
            def count(self):
                return len(self)
        occ = SimpleNamespace(
            name=name,
            fullPathName=full_path,
            component=SimpleNamespace(name="Comp"),
            isReferencedComponent=False,
            bRepBodies=_Coll(),
            childOccurrences=_Coll(),
        )
        return ctree._walk_occurrence(occ, 0, 1, {"n": 0, "truncated": False})

    def test_node_emits_full_path(self):
        node = self._walk_occ("Bolt:1", "Sub-A:1+Bolt:1")
        assert node["name"] == "Bolt:1"
        assert node["full_path"] == "Sub-A:1+Bolt:1"   # the unambiguous key flows to the agent


# ── _walk_occurrence: counts, x-ref resolution, depth bounds ──────────────────

import json


class _Coll(list):
    @property
    def count(self):
        return len(self)


def _wocc(name="Occ:1", full_path=None, comp="Comp", is_ref=False, body_count=0,
          children=(), doc_ref=None):
    return SimpleNamespace(
        name=name,
        fullPathName=full_path or name,
        component=SimpleNamespace(name=comp),
        isReferencedComponent=is_ref,
        bRepBodies=_Coll([None] * body_count),
        childOccurrences=_Coll(list(children)),
        documentReference=doc_ref,
    )


class TestWalkOccurrence:
    def test_counts_bodies_and_children(self):
        kid = _wocc("Kid:1")
        occ = _wocc("Parent:1", body_count=3, children=[kid])
        node = ctree._walk_occurrence(occ, 0, 1, {"n": 0, "truncated": False})
        assert node["body_count"] == 3
        assert node["child_count"] == 1
        assert node["component"] == "Comp"
        assert node["is_reference"] is False

    def test_xref_resolves_source_fields(self):
        df = SimpleNamespace(id="urn:src", name="Vise.f3d", fusionWebURL="https://x/y")
        dr = SimpleNamespace(version=4, isOutOfDate=True, dataFile=df)
        occ = _wocc("Ref:1", is_ref=True, doc_ref=dr)
        node = ctree._walk_occurrence(occ, 0, 1, {"n": 0, "truncated": False})
        assert node["is_reference"] is True
        assert node["source_version"] == 4
        assert node["is_out_of_date"] is True
        assert node["source_id"] == "urn:src"
        assert node["source_name"] == "Vise.f3d"
        assert node["source_url"] == "https://x/y"

    def test_non_reference_has_no_source_fields(self):
        occ = _wocc("Plain:1", is_ref=False)
        node = ctree._walk_occurrence(occ, 0, 1, {"n": 0, "truncated": False})
        assert "source_id" not in node and "source_version" not in node

    def test_descends_within_depth(self):
        kid = _wocc("Kid:1")
        occ = _wocc("Parent:1", children=[kid])
        # max_depth 2 => depth 0 may descend (0+1 < 2)
        node = ctree._walk_occurrence(occ, 0, 2, {"n": 0, "truncated": False})
        assert "children" in node
        assert node["children"][0]["name"] == "Kid:1"

    def test_stops_at_depth_limit_flags_children_truncated(self):
        kid = _wocc("Kid:1")
        occ = _wocc("Parent:1", children=[kid])
        # max_depth 1 => 0+1 < 1 is False, so we do NOT descend, but children exist -> flag
        node = ctree._walk_occurrence(occ, 0, 1, {"n": 0, "truncated": False})
        assert "children" not in node
        assert node["children_truncated"] is True

    def test_node_cap_sets_truncated(self):
        kid = _wocc("Kid:1")
        occ = _wocc("Parent:1", children=[kid])
        counter = {"n": 0, "truncated": False}
        # _MAX_NODES is large, so simulate the cap by patching it down
        import types
        old = ctree._MAX_NODES
        try:
            ctree._MAX_NODES = 1
            ctree._walk_occurrence(occ, 0, 3, counter)
        finally:
            ctree._MAX_NODES = old
        assert counter["truncated"] is True


# ── handler: max_depth clamp + no-design + named start ────────────────────────

class _Root:
    def __init__(self, occs, name="Root"):
        self.name = name
        self.occurrences = list(occs)


class _Design:
    def __init__(self, root):
        self.rootComponent = root


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestTreeHandler:
    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(ctree._common, "design", lambda: None)
        res = ctree.handler()
        assert res["isError"] is True and "No active design" in res["message"]

    def test_max_depth_clamped_to_ceiling(self, monkeypatch):
        root = _Root([_wocc("A:1")])
        monkeypatch.setattr(ctree._common, "design", lambda: _Design(root))
        out = _payload(ctree.handler(max_depth=99))
        assert out["max_depth"] == ctree._MAX_DEPTH       # clamped to 8

    def test_max_depth_floored_to_one(self, monkeypatch):
        root = _Root([_wocc("A:1")])
        monkeypatch.setattr(ctree._common, "design", lambda: _Design(root))
        out = _payload(ctree.handler(max_depth=0))
        assert out["max_depth"] == 1                      # min 1

    def test_invalid_max_depth_uses_default(self, monkeypatch):
        root = _Root([_wocc("A:1")])
        monkeypatch.setattr(ctree._common, "design", lambda: _Design(root))
        out = _payload(ctree.handler(max_depth="banana"))
        assert out["max_depth"] == ctree._DEFAULT_DEPTH   # 3

    def test_root_walk_returns_children_and_node_count(self, monkeypatch):
        root = _Root([_wocc("A:1"), _wocc("B:1")])
        monkeypatch.setattr(ctree._common, "design", lambda: _Design(root))
        out = _payload(ctree.handler())
        assert out["root"] == "Root"
        assert len(out["children"]) == 2
        assert out["node_count"] == 2

    def test_named_start_not_found_errors(self, monkeypatch):
        root = _Root([_wocc("A:1", comp="Alpha")])
        monkeypatch.setattr(ctree._common, "design", lambda: _Design(root))
        res = ctree.handler(component="Ghost")
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_named_start_returns_subtree(self, monkeypatch):
        target = _wocc("Wheel:1", comp="Wheel")
        root = _Root([target])
        monkeypatch.setattr(ctree._common, "design", lambda: _Design(root))
        out = _payload(ctree.handler(component="Wheel"))
        assert out["root"] == "Wheel"
        assert out["tree"]["name"] == "Wheel:1"
