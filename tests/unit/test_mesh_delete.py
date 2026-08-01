"""Tests for `mesh_delete` - the mode branch (MeshRemoveFeatures parametric / deleteMe direct), the
gated delete, and the design-wide survivor re-check: a delete only claims success when the mesh
body is actually gone. Modeled on pmi_delete's test file (gated delete, survivor re-read, ambiguity
refusal) - resolution itself is monkeypatched at `_MESH.resolve` (the same seam pmi_delete patches
at `_pmi.find_annotation`) for most tests, so they exercise mesh_delete's OWN logic, not
MeshBodyRef's; `TestDesignWideMeshResolution` at the bottom exercises the REAL resolver against a
multi-component design to prove mesh name resolution is design-wide, not just active-component.

LIVE-VERIFIED facts these tests encode (a Fusion sweep, not a guess):
  - an Occurrence proxy exposes bRepBodies but NOT meshBodies, so a mesh outside the active/root
    component is invisible to an occurrence-based name walk.
  - right after a successful delete, a held wrapper's `isValid` stays TRUE and an entityToken lookup
    still resolves the PRE-REMOVE body - neither is evidence of survival. Only a FRESH collection
    walk, matched by NAME, is trustworthy."""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, error_message

md = load_tool("mesh_delete")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── fakes ──────────────────────────────────────────────────────────────────────────────────────

class _Coll:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


class _FakeMesh:
    """Stands in for adsk.fusion.MeshBody. A REALISTIC successful deleteMe() removes itself from its
    owning component's meshBodies collection - a genuine disappearance from the collection a fresh
    walk would see. isValid is LIVE-VERIFIED to stay TRUE even after a successful delete (a held
    wrapper never reports its own removal) - it is NEVER flipped here, on purpose: any test/handler
    logic that trusts it must be proven wrong, not accidentally right. A test simulating the platform
    LYING (nothing actually removed) overrides .deleteMe directly."""
    def __init__(self, name="Scan1", token="MTOK::Scan1", delete_result=True):
        self.name = name
        self.entityToken = token
        self._delete_result = delete_result
        self.deleted = False
        self.parentComponent = None
        self.isValid = True   # stays True even after a real delete - see class docstring

    def deleteMe(self):
        self.deleted = self._delete_result
        if self._delete_result and self.parentComponent is not None:
            items = self.parentComponent.meshBodies._items
            if self in items:
                items.remove(self)
        return self._delete_result


class _FakeComp:
    def __init__(self, name="Comp", meshes=(), features=None):
        self.name = name
        self.meshBodies = _Coll(meshes)
        self.features = features


class _FakeDesign:
    """findEntityByToken LIVE-VERIFIED returns the PRE-REMOVE body even after a successful delete (a
    historical-resolution artifact) - by DEFAULT it stays wired that way (returns whatever `_tokens`
    says, never auto-clearing on delete), so a test trusting it would get the wrong (stale) answer;
    only the mesh_delete tests exercise this deliberately."""
    def __init__(self, comp, design_type=0, tokens=None, all_components=None):
        self.rootComponent = comp
        self.activeComponent = comp
        self.designType = design_type   # 0 direct, 1 parametric (current_design_type's int fallback)
        self._tokens = dict(tokens or {})
        self._all_components = list(all_components) if all_components is not None else [comp]

    @property
    def allComponents(self):
        return _Coll(self._all_components)

    def findEntityByToken(self, token):
        e = self._tokens.get(token)
        return [e] if e is not None else []


class _FakeMeshRemoveFeatures:
    """meshRemoveFeatures - createInput(coll) -> input; add(input) -> feature or None (non-parametric
    contract, matching mesh_reduce/mesh_remesh). on_add is the in-place removal side effect."""
    def __init__(self, raise_on_add=False, none_feature=False, feat_name="MeshRemove1", on_add=None,
                 create_input_returns_none=False):
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self._feat_name = feat_name
        self._on_add = on_add
        self._create_input_returns_none = create_input_returns_none
        self.last_input = None
        self.last_coll = None

    def createInput(self, bodies):
        # LIVE-VERIFIED: MeshRemoveFeatures.createInput wants a plain Python LIST of MeshBody (the
        # std::vector<Ptr<MeshBody>> SWIG binding) - an ObjectCollection raises live with "argument 2
        # of type 'std::vector< adsk::core::Ptr< adsk::fusion::MeshBody > >'". Reject anything else so
        # a regression back to ObjectCollection.create() surfaces here, not just in a downstream assert.
        if not isinstance(bodies, list):
            raise TypeError(
                f"MeshRemoveFeatures.createInput requires a plain list of MeshBody, got "
                f"{type(bodies).__name__}")
        self.last_coll = bodies
        if self._create_input_returns_none:
            return None
        self.last_input = SimpleNamespace(inputBodies=bodies)
        return self.last_input

    def add(self, inp):
        if self.raise_on_add:
            raise RuntimeError("remove failed")
        if self._on_add is not None:
            self._on_add()
        if self.none_feature:
            return None
        return SimpleNamespace(name=self._feat_name)


class _FakeFeatures:
    def __init__(self, mesh_remove=None):
        self.meshRemoveFeatures = mesh_remove


# ── DIRECT mode: MeshBody.deleteMe() ─────────────────────────────────────────────────────────────

class TestDirectDelete:
    @pytest.fixture
    def rig(self, monkeypatch):
        comp = _FakeComp("Comp", meshes=[])
        mesh = _FakeMesh()
        mesh.parentComponent = comp
        comp.meshBodies._items.append(mesh)
        design = _FakeDesign(comp, design_type=0)
        monkeypatch.setattr(md._common, "design", lambda: design)
        monkeypatch.setattr(md._MESH, "resolve", lambda raw: (mesh, None))
        state = SimpleNamespace(mesh=mesh, comp=comp, design=design, monkeypatch=monkeypatch)
        return state

    def test_deletes_and_confirms_gone(self, rig):
        out = _payload(md.handler(mesh="H"))
        assert out["deleted"] == "Scan1"
        assert out["deleted_via"] == "MeshBody.deleteMe"
        assert out["component"] == "Comp"
        assert out["remaining_meshes"] == 0
        assert rig.mesh.deleted is True

    def test_a_declined_delete_is_an_error(self, rig):
        rig.mesh._delete_result = False
        assert "declined" in error_message(md.handler(mesh="H"))
        # nothing changed - the mesh is still in the collection
        assert rig.mesh in rig.comp.meshBodies._items

    def test_deleteMe_raising_surfaces_not_swallowed(self, rig):
        def boom():
            raise RuntimeError("boom")
        rig.mesh.deleteMe = boom
        assert "deleteMe() failed" in error_message(md.handler(mesh="H"))

    def test_a_survivor_after_success_is_an_error(self, rig):
        # deleteMe() LIES (returns True) but the mesh still resolves afterwards - never trust the bool
        # alone; the survivor re-read must catch it.
        rig.mesh.deleteMe = lambda: True
        assert "still resolves" in error_message(md.handler(mesh="H"))

    def test_stale_isValid_true_is_not_a_false_alarm(self, rig):
        # LIVE FACT: a held wrapper's isValid stays TRUE even after a genuinely successful delete -
        # the fixture never flips it (see _FakeMesh). A real delete (collection emptied) must still
        # report success; trusting isValid here would be the exact false alarm the sweep caught.
        out = _payload(md.handler(mesh="H"))
        assert out["deleted"] == "Scan1"
        assert rig.mesh.isValid is True   # still true - and that's fine, it isn't consulted

    def test_stale_entity_token_lookup_is_not_a_false_alarm(self, rig):
        # LIVE FACT: findEntityByToken(token) still resolves the PRE-REMOVE body after a genuine
        # delete (a historical-resolution artifact, the same trap pmi_delete documents for a
        # suppressed PMI). The collection is genuinely empty (a fresh walk would see that) - the
        # survivor verdict must come from that walk alone, never from a token re-resolution.
        rig.design._tokens[rig.mesh.entityToken] = rig.mesh
        out = _payload(md.handler(mesh="H"))
        assert out["deleted"] == "Scan1"
        assert out["remaining_meshes"] == 0
        # the stale lookup still "finds" it - proving the handler never consulted this path
        assert rig.design.findEntityByToken(rig.mesh.entityToken) == [rig.mesh]

    def test_same_named_survivor_from_a_different_object_is_an_error(self, rig):
        # the RESOLVED mesh is genuinely deleted (removed from the collection, a different token) but
        # a DIFFERENT mesh object sharing the SAME NAME is still sitting in the fresh collection walk
        # - proving the check is a NAME match over the collection, not tied to the specific
        # object/token that was resolved.
        duplicate = _FakeMesh(name="Scan1", token="MTOK::Other")
        duplicate.parentComponent = rig.comp
        rig.comp.meshBodies._items.append(duplicate)   # a second "Scan1" already present pre-delete
        assert "still resolves" in error_message(md.handler(mesh="H"))
        assert rig.mesh not in rig.comp.meshBodies._items   # the resolved one really is gone
        assert duplicate in rig.comp.meshBodies._items       # the name-alike survivor triggered it

    def test_no_active_design_is_an_error(self, rig):
        rig.monkeypatch.setattr(md._common, "design", lambda: None)
        assert "No active design" in error_message(md.handler(mesh="H"))

    def test_resolver_error_surfaces(self, rig):
        rig.monkeypatch.setattr(md._MESH, "resolve", lambda raw: (None, "No mesh named 'X'."))
        assert "No mesh named" in error_message(md.handler(mesh="X"))

    def test_ambiguous_name_is_refused_not_guessed(self, rig):
        # the resolver refuses an ambiguous name with candidates rather than grabbing the first -
        # mesh_delete must surface that refusal as-is and attempt no deletion.
        rig.monkeypatch.setattr(
            md._MESH, "resolve",
            lambda raw: (None, "'mesh': 'Scan' is ambiguous - it names 2 bodies ('Scan' in Root, "
                                "'Scan' in Sub). Pass a find_geometry 'handle' to pick the exact one."))
        msg = error_message(md.handler(mesh="Scan"))
        assert "ambiguous" in msg
        assert rig.mesh.deleted is False   # no deletion was attempted


# ── PARAMETRIC mode: MeshRemoveFeatures (at NORMAL parametric scope, NO base-feature edit scope) ───
#
# LIVE-VERIFIED: wrapping this add() in a base-feature edit scope raises "Mesh remove only available
# in parametric mode" - inside an open scope the design PRESENTS as direct, so the feature refuses.
# These tests assert the handler calls createInput/add DIRECTLY (no scope-open/close machinery).

class TestParametricDelete:
    def _setup(self, monkeypatch, raise_on_add=False, none_feature=False,
               create_input_returns_none=False, remove_on_delete=True):
        comp = _FakeComp("Comp", meshes=[])
        mesh = _FakeMesh()
        mesh.parentComponent = comp
        comp.meshBodies._items.append(mesh)

        def _on_add():
            if remove_on_delete and mesh in comp.meshBodies._items:
                comp.meshBodies._items.remove(mesh)   # isValid intentionally left True - see _FakeMesh

        mrf = _FakeMeshRemoveFeatures(raise_on_add=raise_on_add, none_feature=none_feature,
                                      create_input_returns_none=create_input_returns_none,
                                      on_add=_on_add)
        comp.features = _FakeFeatures(mesh_remove=mrf)
        design = _FakeDesign(comp, design_type=1)
        monkeypatch.setattr(md._common, "design", lambda: design)
        monkeypatch.setattr(md._MESH, "resolve", lambda raw: (mesh, None))
        return mesh, comp, design, mrf

    def test_deletes_via_mesh_remove_features_at_normal_scope(self, monkeypatch):
        mesh, comp, design, mrf = self._setup(monkeypatch)
        out = _payload(md.handler(mesh="H"))
        assert out["deleted"] == "Scan1"
        assert out["deleted_via"] == "meshRemoveFeatures"
        assert out["feature"] == "MeshRemove1"
        assert out["remaining_meshes"] == 0
        # the mesh was handed to createInput as a PLAIN LIST (live-verified call shape) - an
        # ObjectCollection regression makes the fake raise TypeError, which mesh_delete's try/except
        # turns into an error, and this success assertion goes red.
        assert mrf.last_coll == [mesh]
        assert isinstance(mrf.last_coll, list)

    def test_none_feature_return_is_still_success_when_mesh_is_gone(self, monkeypatch):
        # add() returning None is the documented non-parametric-feature contract - success is judged
        # by the mesh actually being gone, not the return.
        mesh, comp, design, mrf = self._setup(monkeypatch, none_feature=True)
        out = _payload(md.handler(mesh="H"))
        assert out["deleted"] == "Scan1"
        assert out["feature"] is None
        assert out["remaining_meshes"] == 0

    def test_add_failure_surfaces_not_swallowed(self, monkeypatch):
        mesh, comp, design, mrf = self._setup(monkeypatch, raise_on_add=True)
        assert "failed" in error_message(md.handler(mesh="H")).lower()
        assert mesh in comp.meshBodies._items   # nothing was removed

    def test_create_input_none_errors(self, monkeypatch):
        mesh, comp, design, mrf = self._setup(monkeypatch, create_input_returns_none=True)
        assert "returned nothing" in error_message(md.handler(mesh="H"))

    def test_missing_remove_features_collection_errors(self, monkeypatch):
        comp = _FakeComp("Comp", meshes=[])
        mesh = _FakeMesh()
        mesh.parentComponent = comp
        comp.meshBodies._items.append(mesh)
        comp.features = _FakeFeatures(mesh_remove=None)
        design = _FakeDesign(comp, design_type=1)
        monkeypatch.setattr(md._common, "design", lambda: design)
        monkeypatch.setattr(md._MESH, "resolve", lambda raw: (mesh, None))
        res = md.handler(mesh="H")
        assert res["isError"] is True
        assert "meshRemoveFeatures collection" in res["message"]

    def test_survivor_after_reported_success_is_an_error(self, monkeypatch):
        # add() succeeds but the mesh was NOT actually removed from the collection - the survivor
        # re-read must catch the lie, mirroring the direct-mode case.
        mesh, comp, design, mrf = self._setup(monkeypatch, remove_on_delete=False)
        assert "still resolves" in error_message(md.handler(mesh="H"))

    def test_add_does_not_touch_base_features_collection(self, monkeypatch):
        # REGRESSION guard for the base-feature-scope trap: comp.features has NO baseFeatures
        # attribute at all in this rig, so any code path that tries to open a scope (e.g. a
        # reintroduced run_in_base_feature wrapper) raises AttributeError instead of silently
        # succeeding - proving the handler never reaches for it.
        mesh, comp, design, mrf = self._setup(monkeypatch)
        assert not hasattr(comp.features, "baseFeatures")
        out = _payload(md.handler(mesh="H"))
        assert out["deleted"] == "Scan1"


# ── design-wide mesh NAME resolution (exercises the REAL _inputs.MeshBodyRef, not monkeypatched) ───
#
# LIVE-VERIFIED: an Occurrence proxy exposes bRepBodies but NOT meshBodies, so with the mesh's owner
# component NOT active, an occurrence-based walk finds nothing (root.allOccurrences is deliberately
# [] here - no occurrence proxies are modeled at all, so this can only pass via the design-wide
# component sweep, never the occurrence path).

class TestDesignWideMeshResolution:
    def test_child_component_mesh_resolves_with_root_active(self, monkeypatch):
        root = _FakeComp("Root", meshes=[])
        root.allOccurrences = []
        child = _FakeComp("MeshChild", meshes=[])
        child_mesh = _FakeMesh(name="ChildMesh", token="MTOK::ChildMesh")
        child_mesh.parentComponent = child
        child.meshBodies._items.append(child_mesh)

        design = _FakeDesign(root, design_type=0, all_components=[root, child])
        assert design.activeComponent is root   # ROOT is active, not the mesh's owner

        import adsk.fusion
        monkeypatch.setattr(adsk.fusion, "MeshBody", _FakeMesh)
        monkeypatch.setattr(md._common, "design", lambda: design)
        monkeypatch.setattr(md._inputs._common, "design", lambda: design)   # the dual-seam trap

        out = _payload(md.handler(mesh="ChildMesh"))
        assert out["deleted"] == "ChildMesh"
        assert out["component"] == "MeshChild"
        assert out["remaining_meshes"] == 0
        assert child_mesh not in child.meshBodies._items
