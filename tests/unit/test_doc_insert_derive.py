"""Unit tests for ``doc_insert_derive.py``: the source-open precondition, the SOURCE-side name
resolvers (component/body - exact match, miss lists names, ambiguity refused), the scoping that turns
names into sourceEntities, the landed read-back (derived_components + body counts, empty-landing is an
error), and the inline verify (healthState / documentReference.isOutOfDate / isDerived / params).

The cloud URN resolution is covered by test_doc_insert_occurrence.py (the shared _resolve_data_file);
here it is monkeypatched so these tests stay offline. Occurrence resolution (into_component) goes
through the real _inputs.OccurrenceRef kind against a fake design (dual seam patched). SOURCE-side
fakes are built with SimpleNamespace + conftest's shared _NamedCollection so no new Fake* class is
added (the bespoke-fake ratchet); the destination fakes are the existing ones in this file.
"""

import json
import types

from conftest import load_tool, _NamedCollection

io = load_tool("doc_insert_derive")


# ── destination-side fakes ───────────────────────────────────────────────────────────────────────

class FakeDataFile:
    def __init__(self, id_="urn:adsk.wipprod:dm.lineage:src", name="SourcePart", version=3):
        self.id = id_
        self.name = name
        self.versionNumber = version


class FakeDocRef:
    def __init__(self, is_out_of_date=False):
        self.isOutOfDate = is_out_of_date


class FakeBody:
    def __init__(self, name, is_derived=True):
        self.name = name
        self.isDerived = is_derived


class FakeBodyCollection:
    def __init__(self, bodies):
        self._bodies = list(bodies)

    @property
    def count(self):
        return len(self._bodies)

    def item(self, i):
        return self._bodies[i]


class FakeDeriveFeature:
    def __init__(self, name="Derive1", health=0, message="", bodies=(), is_parametric=True,
                 out_of_date=False):
        self.name = name
        self.healthState = health
        self.errorOrWarningMessage = message
        self.bodies = FakeBodyCollection(bodies)
        self.isParametric = is_parametric
        self.documentReference = FakeDocRef(out_of_date)


class FakeDeriveFeatureInput:
    def __init__(self):
        self.sourceEntities = None
        self.excludedEntities = None
        self.isIncludeComponentParameters = None
        self.isIncludeFavoriteParameters = None
        self.isPlaceObjectsAtOrigin = None


def _mk_body(spec):
    return FakeBody(*spec) if isinstance(spec, tuple) else FakeBody(spec)


class FakeDeriveFeatures:
    """result: 'default' (build+return a feature) | 'null' (add() returns None) | 'raise'.
    on_add: optional no-arg callback fired the instant add() would create the feature - lets a test
    simulate a side effect that happens DURING the derive (a new occurrence landing, a param count
    change). last_input captures the DeriveFeatureInput the handler populated."""

    def __init__(self, result="default", bodies=("Body1",), health=0, message="",
                 out_of_date=False, is_parametric=True, on_add=None):
        self.result = result
        self.bodies = bodies
        self.health = health
        self.message = message
        self.out_of_date = out_of_date
        self.is_parametric = is_parametric
        self.on_add = on_add
        self.last_input = None
        self.created = None

    def createInput(self, source_design):
        di = FakeDeriveFeatureInput()
        self.last_input = di
        return di

    def add(self, di):
        if self.result == "null":
            return None
        if self.result == "raise":
            raise RuntimeError("derive add failed")
        if self.on_add:
            self.on_add()
        feat = FakeDeriveFeature(health=self.health, message=self.message,
                                 bodies=[_mk_body(b) for b in self.bodies],
                                 is_parametric=self.is_parametric, out_of_date=self.out_of_date)
        self.created = feat
        return feat


class FakeFeatures:
    def __init__(self, derive_features):
        self.deriveFeatures = derive_features


class FakeOcc:
    """A destination occurrence. body_count + children back _subtree_body_count; isDerived + token
    back the new-derived-occurrence diff."""

    def __init__(self, name, is_derived=True, token=None, body_count=0, children=()):
        self.name = name
        self.isDerived = is_derived
        self.entityToken = token if token is not None else f"tok-{id(self)}"
        self.bRepBodies = FakeBodyCollection([FakeBody(f"{name}_b{i}") for i in range(body_count)])
        self.childOccurrences = FakeOccurrences(children)


class FakeOccurrences:
    def __init__(self, items):
        self._items = list(items)   # comp.occurrences._items - tests mutate this to simulate add()

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]


class FakeComp:
    def __init__(self, name="Root", derive_features=None, occurrences=()):
        self.name = name
        self.features = FakeFeatures(derive_features if derive_features is not None else FakeDeriveFeatures())
        self.occurrences = FakeOccurrences(occurrences)


class FakeUserParameters:
    def __init__(self, count=0):
        self._count = count

    @property
    def count(self):
        return self._count


class FakeDesign:
    """The DESTINATION design (what _common.design() returns). designType: 1 = parametric, 0 = direct.
    No allComponents/computeAll surface: design_wide_counts falls back to the root (0 bodies) and
    computeAll is swallowed by safe() - the landed body DELTA is asserted live, not here."""

    def __init__(self, root_comp, design_type=1, user_param_count=0):
        self.rootComponent = root_comp
        self.designType = design_type
        self.userParameters = FakeUserParameters(user_param_count)


class FakeProducts:
    def __init__(self, design):
        self._design = design

    def itemByProductType(self, kind):
        return self._design if kind == 'DesignProductType' else None


_NO_DEFAULT = object()


class FakeSourceDoc:
    def __init__(self, name="Source", data_file=None, design=_NO_DEFAULT):
        self.name = name
        self.dataFile = data_file
        self.products = FakeProducts(_make_source() if design is _NO_DEFAULT else design)


class FakeDocuments:
    def __init__(self, open_docs=()):
        self._open_docs = list(open_docs)

    @property
    def count(self):
        return len(self._open_docs)

    def item(self, i):
        return self._open_docs[i]


class FakeApp:
    def __init__(self, documents):
        self.documents = documents


# ── source-side builders (SimpleNamespace + the shared _NamedCollection - no new Fake* class) ──────

def _src_comp(name, bodies=()):
    """A SOURCE component: name + bRepBodies/meshBodies collections (count/item)."""
    return types.SimpleNamespace(
        name=name,
        bRepBodies=_NamedCollection([types.SimpleNamespace(name=b) for b in bodies]),
        meshBodies=_NamedCollection([]))


def _src_occ(name):
    """A SOURCE occurrence - opaque; the handler just forwards it into sourceEntities."""
    return types.SimpleNamespace(name=name)


def _make_source(components=(), occ_by_comp=None, root_name="SrcRoot"):
    """A SOURCE design: rootComponent (with allOccurrencesByComponent) + allComponents (root + subs)."""
    occ_by_comp = dict(occ_by_comp or {})
    root = types.SimpleNamespace(name=root_name,
                                 bRepBodies=_NamedCollection([]), meshBodies=_NamedCollection([]))
    root.allOccurrencesByComponent = lambda c: _NamedCollection(
        list(occ_by_comp.get(getattr(c, "name", None), [])))
    return types.SimpleNamespace(rootComponent=root,
                                 allComponents=_NamedCollection([root] + list(components)))


def _install(monkeypatch, *, design_type=1, user_param_count=0, occurrences=(),
             derive_features=None, comp_name="Root", data_file=None,
             source_design=_NO_DEFAULT, source_open=True):
    """Wire both design seams + a fake app.documents holding the (open) source doc + a stubbed
    _resolve_data_file. Returns (design, comp, docs, data_file, derive_features, source_doc)."""
    derive_features = derive_features if derive_features is not None else FakeDeriveFeatures()
    comp = FakeComp(comp_name, derive_features=derive_features, occurrences=occurrences)
    design = FakeDesign(comp, design_type=design_type, user_param_count=user_param_count)
    monkeypatch.setattr(io._common, "design", lambda: design)
    monkeypatch.setattr(io._inputs._common, "design", lambda: design)
    df = data_file or FakeDataFile()
    src = _make_source() if source_design is _NO_DEFAULT else source_design
    source_doc = FakeSourceDoc(data_file=df, design=src)
    docs = FakeDocuments(open_docs=[source_doc] if source_open else [])
    monkeypatch.setattr(io, "app", FakeApp(docs))
    monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, df.id, [raw]))
    return design, comp, docs, df, derive_features, source_doc


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── guards ─────────────────────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_empty_document_id_errors(self):
        res = io.handler(document_id="")
        assert res["isError"] is True and "document_id" in res["message"]

    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(io._common, "design", lambda: None)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_direct_mode_refused(self, monkeypatch):
        _install(monkeypatch, design_type=0)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "parametric" in res["message"].lower()
        assert "design_set_mode" in res["message"]

    def test_unresolvable_document_id(self, monkeypatch):
        _install(monkeypatch)
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (None, None, ["urn:adsk:1", "urn:adsk:2"]))
        res = io.handler(document_id="urn:nope")
        assert res["isError"] is True
        assert "Could not resolve" in res["message"]
        assert "urn:adsk:1" in res["message"]


# ── source-open precondition (createInput needs the source open - async load) ─────────────────────

class TestSourceOpenPrecondition:
    def test_source_not_open_errors_with_doc_open_guidance(self, monkeypatch):
        _, _, _, _, dfs, _ = _install(monkeypatch, source_open=False)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "not open" in res["message"]
        assert "doc_open" in res["message"]          # points the caller at doc_open
        assert dfs.created is None                    # no partial state - never reached add()

    def test_source_open_is_used(self, monkeypatch):
        _install(monkeypatch)                         # source_open=True by default
        out = _payload(io.handler(document_id="urn:x"))
        assert out["derived"] is True


# ── SOURCE component resolver: exact match, miss lists names, ambiguity refused ───────────────────

class TestSourceComponentResolver:
    def test_exact_match_case_insensitive(self):
        src = _make_source([_src_comp("OuterRing"), _src_comp("InnerRing")])
        comps, err = io._resolve_source_components(src, ["outerring"])
        assert err is None
        assert [c.name for c in comps] == ["OuterRing"]

    def test_miss_lists_available_names(self):
        src = _make_source([_src_comp("OuterRing"), _src_comp("Rotor")])
        comps, err = io._resolve_source_components(src, ["NoSuchPart"])
        assert comps is None
        assert "not found" in err
        assert "OuterRing" in err and "Rotor" in err   # available names surfaced

    def test_ambiguous_name_refused_not_first_match(self):
        # TWO distinct components share the name - the resolver must REFUSE, never grab the first.
        a, b = _src_comp("Ring"), _src_comp("Ring")
        src = _make_source([a, b])
        comps, err = io._resolve_source_components(src, ["Ring"])
        assert comps is None                           # did NOT return [a]
        assert "ambiguous" in err and "2" in err

    def test_size_zero_returns_empty(self):
        comps, err = io._resolve_source_components(_make_source([_src_comp("A")]), [])
        assert err is None and comps == []

    def test_size_two_resolves_both_in_order(self):
        src = _make_source([_src_comp("A"), _src_comp("B"), _src_comp("C")])
        comps, err = io._resolve_source_components(src, ["B", "A"])
        assert err is None
        assert [c.name for c in comps] == ["B", "A"]   # order preserved


# ── SOURCE body resolver: name, Component/Body scoping, ambiguity refused ─────────────────────────

class TestSourceBodyResolver:
    def test_body_by_name(self):
        src = _make_source([_src_comp("OuterRing", bodies=["RingSolid"])])
        bodies, err = io._resolve_source_bodies(src, ["RingSolid"])
        assert err is None
        assert [b.name for b in bodies] == ["RingSolid"]

    def test_ambiguous_body_name_refused(self):
        src = _make_source([_src_comp("A", bodies=["Shared"]), _src_comp("B", bodies=["Shared"])])
        bodies, err = io._resolve_source_bodies(src, ["Shared"])
        assert bodies is None
        assert "ambiguous" in err
        assert "A" in err and "B" in err               # names the holders

    def test_component_slash_body_scopes_to_owner(self):
        # 'Shared' is ambiguous unqualified, but 'B/Shared' pins the owner.
        src = _make_source([_src_comp("A", bodies=["Shared"]), _src_comp("B", bodies=["Shared"])])
        bodies, err = io._resolve_source_bodies(src, ["B/Shared"])
        assert err is None and [b.name for b in bodies] == ["Shared"]

    def test_missing_body_named(self):
        src = _make_source([_src_comp("A", bodies=["X"])])
        bodies, err = io._resolve_source_bodies(src, ["Ghost"])
        assert bodies is None and "Ghost" in err


# ── collecting sourceEntities: component -> occurrences, root -> whole, no-occurrence error ────────

class TestCollectSourceEntities:
    def test_component_contributes_its_occurrences(self):
        occ = _src_occ("OuterRing:1")
        src = _make_source([_src_comp("OuterRing")], occ_by_comp={"OuterRing": [occ]})
        ents, labels, err = io._collect_source_entities(src, ["OuterRing"], [])
        assert err is None
        assert ents == [occ]                           # the occurrence, not the component
        assert labels == ["OuterRing"]

    def test_two_components_both_occurrences(self):
        o1, o2 = _src_occ("OuterRing:1"), _src_occ("InnerRing:1")
        src = _make_source([_src_comp("OuterRing"), _src_comp("InnerRing")],
                           occ_by_comp={"OuterRing": [o1], "InnerRing": [o2]})
        ents, labels, err = io._collect_source_entities(src, ["OuterRing", "InnerRing"], [])
        assert err is None and ents == [o1, o2]

    def test_naming_the_root_derives_whole_design(self):
        src = _make_source([_src_comp("Sub")], root_name="Assembly")
        ents, labels, err = io._collect_source_entities(src, ["Assembly"], [])
        assert err is None
        assert ents == [src.rootComponent]             # the root Component itself (whole design)
        assert "whole design" in labels[0]

    def test_component_without_occurrence_errors(self):
        src = _make_source([_src_comp("Lonely")], occ_by_comp={})   # defined, not instanced
        ents, labels, err = io._collect_source_entities(src, ["Lonely"], [])
        assert ents is None and "no occurrence" in err


# ── read-back: subtree body count + new-derived-occurrence diff ───────────────────────────────────

class TestReadBackWalk:
    def test_subtree_body_count_sums_descendants(self):
        # a top occurrence with 0 direct bodies but children holding 1 + 2 -> 3 (the whole-design shape)
        child_a = FakeOcc("A:1", body_count=1)
        child_b = FakeOcc("B:1", body_count=2)
        top = FakeOcc("Root:1", body_count=0, children=[child_a, child_b])
        assert io._subtree_body_count(top) == 3

    def test_new_derived_occurrence_diff_excludes_preexisting(self):
        pre = FakeOcc("Old:1", is_derived=True, token="stable", body_count=1)
        comp = FakeComp("Root", occurrences=[pre])
        before = io._occurrence_tokens(comp)
        comp.occurrences._items.append(FakeOcc("New:1", is_derived=True, body_count=2))
        found = io._new_derived_occurrences(comp, before)
        assert found == [{"name": "New:1", "body_count": 2}]   # only the NEW one


# ── scoping through the handler: no selector -> whole; names -> occurrences on sourceEntities ──────

class TestScopingHandler:
    def test_no_selector_derives_whole_design(self, monkeypatch):
        src = _make_source([_src_comp("Sub")], root_name="WholeSrc")
        _, _, _, _, dfs, _ = _install(monkeypatch, source_design=src)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["scope"] == "whole design"
        assert dfs.last_input.sourceEntities == [src.rootComponent]

    def test_scoped_components_forward_occurrences(self, monkeypatch):
        occ = _src_occ("OuterRing:1")
        src = _make_source([_src_comp("OuterRing")], occ_by_comp={"OuterRing": [occ]})
        _, _, _, _, dfs, _ = _install(monkeypatch, source_design=src)
        out = _payload(io.handler(document_id="urn:x", source_components=["OuterRing"]))
        assert dfs.last_input.sourceEntities == [occ]
        assert out["scope"] == "OuterRing"

    def test_bad_scope_name_errors_before_add(self, monkeypatch):
        src = _make_source([_src_comp("OuterRing")])
        _, _, _, _, dfs, _ = _install(monkeypatch, source_design=src)
        res = io.handler(document_id="urn:x", source_components=["Ghost"])
        assert res["isError"] is True and "Ghost" in res["message"]
        assert dfs.created is None                      # aborted before deriving - no partial state

    def test_exclude_components_set_excluded_entities(self, monkeypatch):
        drop = _src_occ("Rotor:1")
        src = _make_source([_src_comp("Rotor")], occ_by_comp={"Rotor": [drop]}, root_name="WholeSrc")
        _, _, _, _, dfs, _ = _install(monkeypatch, source_design=src)
        out = _payload(io.handler(document_id="urn:x", exclude_components=["Rotor"]))
        assert dfs.last_input.excludedEntities == [drop]
        assert out["excluded"] == "Rotor"


# ── read-back honesty: something must actually land ───────────────────────────────────────────────

class TestReadBackHonesty:
    def test_nothing_landed_is_an_error(self, monkeypatch):
        # a feature with no bodies, and no new derived occurrence, and no design-wide body delta.
        derive_features = FakeDeriveFeatures(bodies=())
        _install(monkeypatch, derive_features=derive_features, occurrences=[])
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "nothing landed" in res["message"]

    def test_geometry_without_derived_marker_is_an_error(self, monkeypatch):
        # bodies appear but none report isDerived=true -> the one-way link did not form.
        derive_features = FakeDeriveFeatures(bodies=[("Body1", False)])
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "isDerived" in res["message"]

    def test_direct_derived_bodies_reported(self, monkeypatch):
        derive_features = FakeDeriveFeatures(bodies=[("Body1", True), ("Body2", False)])
        _install(monkeypatch, derive_features=derive_features)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["derived_occurrence"] == "Body1"
        assert out["derived_bodies"] == [
            {"name": "Body1", "is_derived": True}, {"name": "Body2", "is_derived": False}]

    def test_derived_occurrence_reported_with_body_count(self, monkeypatch):
        derive_features = FakeDeriveFeatures(bodies=())
        design, comp, docs, df, dfs, _ = _install(monkeypatch, derive_features=derive_features,
                                                  occurrences=[])
        dfs.on_add = lambda: comp.occurrences._items.append(
            FakeOcc("SourcePart:1", is_derived=True, body_count=1))
        out = _payload(io.handler(document_id="urn:x"))
        assert out["derived_bodies"] == []
        assert out["derived_components"] == [{"name": "SourcePart:1", "body_count": 1}]
        assert out["derived_occurrence"] == "SourcePart:1"

    def test_preexisting_derived_occurrence_not_counted(self, monkeypatch):
        pre_existing = FakeOcc("OldDerived:1", is_derived=True, token="stable-token")
        derive_features = FakeDeriveFeatures(bodies=())
        _install(monkeypatch, derive_features=derive_features, occurrences=[pre_existing])
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "nothing landed" in res["message"]


# ── null add() / exceptions / missing collection (bite-proofed) ──────────────────────────────────

class TestAddFailures:
    def test_null_add_errors_not_false_ok(self, monkeypatch):
        derive_features = FakeDeriveFeatures(result="null")
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "add returned nothing" in res["message"]

    def test_add_raising_errors(self, monkeypatch):
        derive_features = FakeDeriveFeatures(result="raise")
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "Derive failed" in res["message"]

    def test_missing_derive_features_collection_errors(self, monkeypatch):
        comp = FakeComp()
        comp.features = FakeFeatures(None)
        design = FakeDesign(comp)
        monkeypatch.setattr(io._common, "design", lambda: design)
        monkeypatch.setattr(io._inputs._common, "design", lambda: design)
        df = FakeDataFile()
        source_doc = FakeSourceDoc(data_file=df, design=_make_source())
        monkeypatch.setattr(io, "app", FakeApp(FakeDocuments(open_docs=[source_doc])))
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, raw, [raw]))
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "deriveFeatures" in res["message"]


# ── healthState / documentReference verify ───────────────────────────────────────────────────────

class TestHealthAndReferenceVerify:
    def test_error_health_state_bites(self, monkeypatch):
        derive_features = FakeDeriveFeatures(health=2, message="boundary edges do not match")
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "FAILED to compute" in res["message"]
        assert "boundary edges do not match" in res["message"]

    def test_warning_health_state_does_not_error(self, monkeypatch):
        derive_features = FakeDeriveFeatures(health=1, message="a minor warning")
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is False

    def test_out_of_date_at_creation_errors(self, monkeypatch):
        derive_features = FakeDeriveFeatures(out_of_date=True)
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "isOutOfDate=true" in res["message"]


# ── parameter-flag verify ─────────────────────────────────────────────────────────────────────────

class TestParameterVerify:
    def test_warns_when_flags_set_and_zero_landed(self, monkeypatch):
        _install(monkeypatch, user_param_count=4)   # count never changes -> 0 imported
        out = _payload(io.handler(document_id="urn:x"))
        assert out["parameters_imported"] == 0
        assert "parameter_warning" in out and "flaky" in out["parameter_warning"]

    def test_no_warning_when_both_flags_false(self, monkeypatch):
        _install(monkeypatch, user_param_count=4)
        out = _payload(io.handler(document_id="urn:x", include_parameters=False,
                                  include_favorite_parameters=False))
        assert out["parameters_imported"] == 0
        assert "parameter_warning" not in out

    def test_reports_imported_count_honestly(self, monkeypatch):
        design, comp, docs, df, dfs, _ = _install(monkeypatch, user_param_count=2)
        dfs.on_add = lambda: setattr(design.userParameters, "_count", 5)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["parameters_imported"] == 3
        assert "parameter_warning" not in out

    def test_flags_forwarded_to_derive_input(self, monkeypatch):
        design, comp, docs, df, dfs, _ = _install(monkeypatch)
        _payload(io.handler(document_id="urn:x", include_parameters=False,
                            include_favorite_parameters=True, place_at_origin=False))
        di = dfs.last_input
        assert di.isIncludeComponentParameters is False
        assert di.isIncludeFavoriteParameters is True
        assert di.isPlaceObjectsAtOrigin is False


# ── into_component resolution (shared OccurrenceRef kind) ────────────────────────────────────────

class TestIntoComponent:
    def test_empty_uses_root(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["into_component"] == "root component"

    def _nested_setup(self, monkeypatch, *, occ_activates=True, chassis_derive=None,
                      root_restore=True):
        """A design with a Chassis:1 occurrence targetable by into_component. The occ records
        activate()/deactivate() calls (the platform routes a derive into the ACTIVE component, so
        nesting activates the target first); the design records the root-restore. root_restore is
        what activateRootComponent answers - True, False, or 'raise' for one that throws."""
        chassis_derive = chassis_derive or FakeDeriveFeatures()
        chassis_comp = FakeComp("Chassis", derive_features=chassis_derive)
        calls = {"activated": 0, "root_restored": 0, "deactivated": 0}
        occ = type("Occ", (), {
            "name": "Chassis:1", "fullPathName": "Chassis:1", "component": chassis_comp,
            "activate": lambda self=None: calls.__setitem__("activated", calls["activated"] + 1)
                        or occ_activates,
            "deactivate": lambda self=None: calls.__setitem__("deactivated",
                                                              calls["deactivated"] + 1) or True,
        })()
        root_comp = FakeComp("Root")
        design = FakeDesign(root_comp)
        design.rootComponent = type("Root", (), {
            "name": "Root", "allOccurrences": [occ], "occurrences": root_comp.occurrences,
        })()

        def _restore_root():
            calls["root_restored"] += 1
            if root_restore == "raise":
                raise RuntimeError("activateRootComponent blew up")
            return root_restore
        design.activateRootComponent = _restore_root
        monkeypatch.setattr(io._common, "design", lambda: design)
        monkeypatch.setattr(io._inputs._common, "design", lambda: design)
        df = FakeDataFile()
        source_doc = FakeSourceDoc(data_file=df, design=_make_source())
        monkeypatch.setattr(io, "app", FakeApp(FakeDocuments(open_docs=[source_doc])))
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, raw, [raw]))
        return design, chassis_comp, chassis_derive, calls

    def test_named_occurrence_activates_derives_and_restores_root(self, monkeypatch):
        design, chassis, chassis_derive, calls = self._nested_setup(monkeypatch)
        out = _payload(io.handler(document_id="urn:x", into_component="Chassis:1"))
        assert "Chassis" in out["into_component"]
        assert chassis_derive.created is not None
        assert calls["activated"] == 1       # nesting = activate the target before add()
        assert calls["root_restored"] == 1   # ...and restore the root edit target after

    def test_a_successful_root_restore_leaves_the_occurrence_activated_alone(self, monkeypatch):
        # The restore took, so there is nothing to fall back to: deactivating on top of it would
        # leave the edit target somewhere neither the tool nor the caller asked for.
        _design, _chassis, _derive, calls = self._nested_setup(monkeypatch)
        _payload(io.handler(document_id="urn:x", into_component="Chassis:1"))
        assert calls["root_restored"] == 1 and calls["deactivated"] == 0

    def test_a_FALSE_root_restore_falls_back_to_deactivating_the_occurrence(self, monkeypatch):
        # A False return means the restore did NOT take - the same outcome as a raise, and it must
        # take the same fallback, or the derive leaves Chassis:1 as the active edit target.
        _design, _chassis, _derive, calls = self._nested_setup(monkeypatch, root_restore=False)
        _payload(io.handler(document_id="urn:x", into_component="Chassis:1"))
        assert calls["root_restored"] == 1 and calls["deactivated"] == 1

    def test_a_RAISING_root_restore_falls_back_to_deactivating_the_occurrence(self, monkeypatch):
        _design, _chassis, _derive, calls = self._nested_setup(monkeypatch, root_restore="raise")
        _payload(io.handler(document_id="urn:x", into_component="Chassis:1"))
        assert calls["root_restored"] == 1 and calls["deactivated"] == 1

    def test_activation_failure_refuses_before_deriving(self, monkeypatch):
        design, chassis, chassis_derive, calls = self._nested_setup(monkeypatch,
                                                                    occ_activates=False)
        res = io.handler(document_id="urn:x", into_component="Chassis:1")
        assert res["isError"] is True and "activate" in res["message"].lower()
        assert chassis_derive.created is None       # nothing was derived

    def test_root_stray_landing_is_an_honest_error(self, monkeypatch):
        # The platform ignored the activation and landed the derive at ROOT: the read-back must
        # say so, never report a nested success over a root sibling.
        chassis_derive = FakeDeriveFeatures()
        design, chassis, _, calls = self._nested_setup(monkeypatch,
                                                       chassis_derive=chassis_derive)
        stray = FakeOcc("Stray:1", is_derived=True, body_count=1)
        chassis_derive.on_add = (
            lambda: design.rootComponent.occurrences._items.append(stray))
        design.rootComponent.occurrences = FakeOccurrences([])
        res = io.handler(document_id="urn:x", into_component="Chassis:1")
        assert res["isError"] is True
        assert "ROOT" in res["message"] and "Stray:1" in res["message"]

    def test_unknown_occurrence_errors(self, monkeypatch):
        _install(monkeypatch)
        res = io.handler(document_id="urn:x", into_component="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]


# ── payload contract ───────────────────────────────────────────────────────────────────────────

class TestPayloadContract:
    def test_payload_keys_match_returns(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x"))
        for kind in io.RETURNS:
            problem = kind.assert_present(out)
            assert problem == "", problem

    def test_saved_version_wire_sentence_present(self, monkeypatch):
        # derive reads the source's last SAVED cloud version, not live in-session edits.
        _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x"))
        assert "last SAVED cloud version" in out["note"]
        assert "last SAVED cloud version" in io.TOOL_DESCRIPTION

    def test_document_metadata_reported(self, monkeypatch):
        df = FakeDataFile(id_="urn:adsk.wipprod:dm.lineage:abc", name="Gimbal", version=7)
        _install(monkeypatch, data_file=df)
        out = _payload(io.handler(document_id="whatever-resolves"))
        assert out["document_id"] == df.id
        assert out["document_name"] == "Gimbal"
        assert out["source_version"] == 7
        assert out["is_parametric"] is True
