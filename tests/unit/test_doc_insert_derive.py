"""Unit tests for ``doc_insert_derive.py``: guards, the open-or-reuse-source-document bookkeeping,
and the inline verify (healthState / documentReference.isOutOfDate / isDerived / parameter delta).

The cloud URN resolution itself is covered by test_doc_insert_occurrence.py (the shared
_data_common._resolve_data_file); here it is monkeypatched so these tests stay offline. Occurrence
resolution (into_component) goes through the real _inputs.OccurrenceRef kind against a fake design
(dual seam: both io._common.design and io._inputs._common.design point at the same fake).
"""

import json

from conftest import load_tool

io = load_tool("doc_insert_derive")


# ── fakes ──────────────────────────────────────────────────────────────────────────────────────

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
        self.isIncludeComponentParameters = None
        self.isIncludeFavoriteParameters = None
        self.isPlaceObjectsAtOrigin = None


def _mk_body(spec):
    return FakeBody(*spec) if isinstance(spec, tuple) else FakeBody(spec)


class FakeDeriveFeatures:
    """result: 'default' (build+return a feature) | 'null' (add() returns None) | 'raise'.
    on_add: optional no-arg callback fired the instant add() would create the feature - lets a test
    simulate a side effect that happens DURING the derive (a new occurrence landing, the destination
    user-parameter count changing)."""

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
    def __init__(self, name, is_derived=True, token=None):
        self.name = name
        self.isDerived = is_derived
        self.entityToken = token if token is not None else f"tok-{id(self)}"


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
    """The DESTINATION design (what _common.design() returns). designType: 1 = parametric, 0 = direct."""

    def __init__(self, root_comp, design_type=1, user_param_count=0):
        self.rootComponent = root_comp
        self.designType = design_type
        self.userParameters = FakeUserParameters(user_param_count)


class FakeSourceDesign:
    """The SOURCE design: only .rootComponent is ever read (passed into an ObjectCollection)."""
    def __init__(self):
        self.rootComponent = object()


class FakeProducts:
    def __init__(self, design):
        self._design = design

    def itemByProductType(self, kind):
        return self._design if kind == 'DesignProductType' else None


_NO_DEFAULT = object()


class FakeSourceDoc:
    def __init__(self, name="Source", data_file=None, design=_NO_DEFAULT, close_result=True):
        self.name = name
        self.dataFile = data_file
        self.products = FakeProducts(FakeSourceDesign() if design is _NO_DEFAULT else design)
        self._close_result = close_result
        self.closed_with = "never_closed"   # sentinel distinct from close(False) actually firing

    def close(self, save_changes):
        self.closed_with = save_changes
        return self._close_result


class FakeDocuments:
    def __init__(self, open_docs=(), new_doc=_NO_DEFAULT):
        self._open_docs = list(open_docs)
        self._new_doc = new_doc
        self.opened_with = None

    @property
    def count(self):
        return len(self._open_docs)

    def item(self, i):
        return self._open_docs[i]

    def open(self, data_file, visible):
        self.opened_with = (data_file, visible)
        if self._new_doc is _NO_DEFAULT:
            return FakeSourceDoc(data_file=data_file)
        return self._new_doc


class FakeApp:
    def __init__(self, documents):
        self.documents = documents


def _install(monkeypatch, *, design_type=1, user_param_count=0, occurrences=(),
             derive_features=None, comp_name="Root", open_docs=(), new_doc=_NO_DEFAULT,
             data_file=None):
    """Wire both design seams + a fake app.documents + a stubbed _resolve_data_file. Returns
    (design, comp, docs, data_file, derive_features) so a test can post-configure derive_features
    (e.g. .on_add) before calling io.handler(...)."""
    derive_features = derive_features if derive_features is not None else FakeDeriveFeatures()
    comp = FakeComp(comp_name, derive_features=derive_features, occurrences=occurrences)
    design = FakeDesign(comp, design_type=design_type, user_param_count=user_param_count)
    monkeypatch.setattr(io._common, "design", lambda: design)
    monkeypatch.setattr(io._inputs._common, "design", lambda: design)
    docs = FakeDocuments(open_docs=open_docs, new_doc=new_doc)
    monkeypatch.setattr(io, "app", FakeApp(docs))
    df = data_file or FakeDataFile()
    monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, df.id, [raw]))
    return design, comp, docs, df, derive_features


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


# ── open-or-reuse the source document ─────────────────────────────────────────────────────────

class TestSourceDocumentBookkeeping:
    def test_opens_when_not_already_open_then_closes_on_success(self, monkeypatch):
        design, comp, docs, df, _dfs = _install(monkeypatch, open_docs=())
        out = _payload(io.handler(document_id="urn:x"))
        assert docs.opened_with == (df, False)          # visible=False - a background open
        assert out["source_was_already_open"] is False
        assert out["source_closed"] is True

    def test_reuses_already_open_document_and_leaves_it_open(self, monkeypatch):
        df = FakeDataFile()
        existing = FakeSourceDoc(data_file=df)
        design, comp, docs, _df, _dfs = _install(monkeypatch, open_docs=[existing], data_file=df)
        out = _payload(io.handler(document_id="urn:x"))
        assert docs.opened_with is None                 # never called documents.open
        assert existing.closed_with == "never_closed"    # left exactly as found
        assert out["source_was_already_open"] is True
        assert out["source_closed"] is None

    def test_open_returning_nothing_errors(self, monkeypatch):
        _install(monkeypatch, new_doc=None)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "Could not open source document" in res["message"]

    def test_source_with_no_design_product_errors_and_closes(self, monkeypatch):
        doc = FakeSourceDoc(design=None)      # itemByProductType returns None for every kind
        design, comp, docs, df, _dfs = _install(monkeypatch, new_doc=doc)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "has no Design product" in res["message"]
        assert doc.closed_with is False       # opened by us -> cleaned up even on this failure path


# ── null add() / exceptions (bite-proofed) ────────────────────────────────────────────────────

class TestAddFailures:
    def test_null_add_errors_not_false_ok(self, monkeypatch):
        doc = FakeSourceDoc()
        derive_features = FakeDeriveFeatures(result="null")
        design, comp, docs, df, _dfs = _install(monkeypatch, derive_features=derive_features, new_doc=doc)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "add returned nothing" in res["message"]
        assert doc.closed_with is False       # cleanup still ran

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
        monkeypatch.setattr(io, "app", FakeApp(FakeDocuments()))
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (FakeDataFile(), raw, [raw]))
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "deriveFeatures" in res["message"]


# ── healthState verify (bite-proofed) ─────────────────────────────────────────────────────────

class TestHealthStateVerify:
    def test_error_health_state_bites(self, monkeypatch):
        derive_features = FakeDeriveFeatures(health=2, message="boundary edges do not match")
        doc = FakeSourceDoc()
        design, comp, docs, df, _dfs = _install(monkeypatch, derive_features=derive_features, new_doc=doc)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "FAILED to compute" in res["message"]
        assert "boundary edges do not match" in res["message"]
        assert doc.closed_with is False

    def test_warning_health_state_does_not_error(self, monkeypatch):
        derive_features = FakeDeriveFeatures(health=1, message="a minor warning")
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is False


# ── documentReference.isOutOfDate verify ──────────────────────────────────────────────────────

class TestDocumentReferenceVerify:
    def test_out_of_date_at_creation_errors(self, monkeypatch):
        derive_features = FakeDeriveFeatures(out_of_date=True)
        doc = FakeSourceDoc()
        design, comp, docs, df, _dfs = _install(monkeypatch, derive_features=derive_features, new_doc=doc)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "isOutOfDate=true" in res["message"]
        assert doc.closed_with is False


# ── isDerived verify (body first, occurrence fallback) ────────────────────────────────────────

class TestIsDerivedVerify:
    def test_no_derived_body_or_occurrence_errors(self, monkeypatch):
        derive_features = FakeDeriveFeatures(bodies=[("Body1", False)])
        _install(monkeypatch, derive_features=derive_features)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "isDerived=true" in res["message"]

    def test_derived_body_reported_in_payload(self, monkeypatch):
        derive_features = FakeDeriveFeatures(bodies=[("Body1", True), ("Body2", False)])
        _install(monkeypatch, derive_features=derive_features)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["derived_occurrence"] == "Body1"
        assert out["derived_bodies"] == [
            {"name": "Body1", "is_derived": True}, {"name": "Body2", "is_derived": False}]

    def test_occurrence_fallback_when_no_bodies(self, monkeypatch):
        derive_features = FakeDeriveFeatures(bodies=())
        design, comp, docs, df, dfs = _install(monkeypatch, derive_features=derive_features, occurrences=[])
        dfs.on_add = lambda: comp.occurrences._items.append(FakeOcc("SourcePart:1", is_derived=True))
        out = _payload(io.handler(document_id="urn:x"))
        assert out["derived_bodies"] == []
        assert out["derived_occurrence_names"] == ["SourcePart:1"]
        assert out["derived_occurrence"] == "SourcePart:1"

    def test_preexisting_occurrence_not_counted_as_newly_derived(self, monkeypatch):
        # An occurrence already in the target component (isDerived=true from a PRIOR call) must not
        # be mistaken for this call's result if this derive produced nothing new.
        pre_existing = FakeOcc("OldDerived:1", is_derived=True, token="stable-token")
        derive_features = FakeDeriveFeatures(bodies=())
        _install(monkeypatch, derive_features=derive_features, occurrences=[pre_existing])
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "isDerived=true" in res["message"]


# ── parameter-flag verify ─────────────────────────────────────────────────────────────────────

class TestParameterVerify:
    def test_warns_when_flags_set_and_zero_landed(self, monkeypatch):
        _install(monkeypatch, user_param_count=4)   # count never changes -> 0 imported
        out = _payload(io.handler(document_id="urn:x"))
        assert out["parameters_imported"] == 0
        assert "parameter_warning" in out
        assert "flaky" in out["parameter_warning"]

    def test_no_warning_when_both_flags_false(self, monkeypatch):
        _install(monkeypatch, user_param_count=4)
        out = _payload(io.handler(document_id="urn:x", include_parameters=False,
                                  include_favorite_parameters=False))
        assert out["parameters_imported"] == 0
        assert "parameter_warning" not in out

    def test_reports_imported_count_honestly(self, monkeypatch):
        design, comp, docs, df, dfs = _install(monkeypatch, user_param_count=2)
        dfs.on_add = lambda: setattr(design.userParameters, "_count", 5)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["parameters_imported"] == 3
        assert "parameter_warning" not in out

    def test_flags_forwarded_to_derive_input(self, monkeypatch):
        design, comp, docs, df, dfs = _install(monkeypatch)
        _payload(io.handler(document_id="urn:x", include_parameters=False,
                            include_favorite_parameters=True, place_at_origin=False))
        di = dfs.last_input
        assert di.isIncludeComponentParameters is False
        assert di.isIncludeFavoriteParameters is True
        assert di.isPlaceObjectsAtOrigin is False


# ── into_component resolution (shared OccurrenceRef kind) ────────────────────────────────────

class TestIntoComponent:
    def test_empty_uses_root(self, monkeypatch):
        _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["into_component"] == "root component"

    def test_named_occurrence_resolves_its_component(self, monkeypatch):
        chassis_derive = FakeDeriveFeatures()
        chassis_comp = FakeComp("Chassis", derive_features=chassis_derive)
        occ = type("Occ", (), {"name": "Chassis:1", "fullPathName": "Chassis:1",
                               "component": chassis_comp})()
        root_comp = FakeComp("Root")
        design = FakeDesign(root_comp)
        design.rootComponent = type("Root", (), {
            "name": "Root", "allOccurrences": [occ], "occurrences": root_comp.occurrences,
        })()
        monkeypatch.setattr(io._common, "design", lambda: design)
        monkeypatch.setattr(io._inputs._common, "design", lambda: design)
        monkeypatch.setattr(io, "app", FakeApp(FakeDocuments()))
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (FakeDataFile(), raw, [raw]))
        out = _payload(io.handler(document_id="urn:x", into_component="Chassis:1"))
        assert "Chassis" in out["into_component"]
        assert chassis_derive.created is not None

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

    def test_document_metadata_reported(self, monkeypatch):
        df = FakeDataFile(id_="urn:adsk.wipprod:dm.lineage:abc", name="Gimbal", version=7)
        _install(monkeypatch, data_file=df)
        out = _payload(io.handler(document_id="whatever-resolves"))
        assert out["document_id"] == df.id
        assert out["document_name"] == "Gimbal"
        assert out["source_version"] == 7
        assert out["is_parametric"] is True
