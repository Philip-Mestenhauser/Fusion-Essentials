"""Unit tests for surface_offset.py - the created-face read-back and the chaining report."""

import json
import types
from conftest import MakeComp, body_proxy, load_tool, make_source_document, _NamedCollection

se = load_tool("surface_offset")


inp = se._inputs


class FakeBody:
    def __init__(self, name="Body1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeFeature:
    def __init__(self, name="Feat1", bodies=None, faces=None, distance_cm=None, thickness_cm=None):
        self.name = name
        self.bodies = _NamedCollection(bodies if bodies is not None else [FakeBody()])
        if faces is not None:
            self.faces = _NamedCollection(faces)   # a counted collection of created faces
        # ExtendFeature.distance and ThickenFeature.thickness are ModelParameters reading CM; None
        # gives a feature whose length parameter cannot be read at all.
        if distance_cm is not None:
            self.distance = types.SimpleNamespace(value=distance_cm)
        if thickness_cm is not None:
            self.thickness = types.SimpleNamespace(value=thickness_cm)


def _face_on(body):
    """A face the feature CREATED, owned by `body` - the read _created_bodies walks."""
    return types.SimpleNamespace(body=body)


class FakeOffsetInput:
    def __init__(self, ents, dist, op, chain):
        self.ents = ents
        self.dist = dist
        self.op = op
        self.chain = chain


class FakeOffsetFeatures:
    def __init__(self, result_bodies=None, created_faces=None):
        self.last_input = None
        self._result = result_bodies
        self._faces = created_faces
    def createInput(self, ents, dist, op, chain):
        self.last_input = FakeOffsetInput(ents, dist, op, chain)
        return self.last_input
    def add(self, inp):
        return FakeFeature(name="Offset1", bodies=self._result, faces=self._faces)


class FakeFeatures:
    def __init__(self, trim=None, extend=None, offset=None, thicken=None):
        self.trimFeatures = trim
        self.extendFeatures = extend
        self.offsetFeatures = offset
        self.thickenFeatures = thicken


class FakeComp:
    def __init__(self, features):
        self.features = features


class FakeDesign:
    def __init__(self, comp):
        self.rootComponent = comp
        self.activeComponent = comp


class _OC:
    def __init__(self):
        self.items = []
    def add(self, x):
        self.items.append(x)


class FakeBRepBody:
    """adsk.fusion.BRepBody stand-in for SurfaceBodyRef kind validation."""
    def __init__(self, name="Surf1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class FakeFace:
    pass


class FakeEdge:
    def __init__(self, body=None):
        self.body = body


def _wire(comp, handle_map=None):
    design = FakeDesign(comp)
    se.app = type("A", (), {"activeProduct": design})()
    se._common.app = se.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    fo = adsk.fusion.FeatureOperations
    for n in ("NewBodyFeatureOperation", "JoinFeatureOperation",
              "CutFeatureOperation", "NewComponentFeatureOperation"):
        setattr(fo, n, n)
    sxt = adsk.fusion.SurfaceExtendTypes
    for n in ("NaturalSurfaceExtendType", "TangentSurfaceExtendType", "PerpendicularSurfaceExtendType"):
        setattr(sxt, n, n)
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ObjectCollection.create = staticmethod(_OC)
    adsk.fusion.BRepBody = FakeBRepBody
    adsk.fusion.BRepFace = FakeFace
    adsk.fusion.BRepEdge = FakeEdge
    handle_map = handle_map or {}

    class _D:
        rootComponent = comp
        activeComponent = comp
        def findEntityByToken(self, t):
            e = handle_map.get(t)
            return [e] if e is not None else []
    inp._common.design = lambda: _D()
    inp._common.target_component = lambda d: comp


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class _UnreadableSolidBody:
    """A result body whose isSolid will not read - the shape bool(safe(...)) turned into a confident
    'this is a surface'."""
    def __init__(self, name="Wall1"):
        self.name = name

    @property
    def isSolid(self):
        raise RuntimeError("4 : An API Object refers to a deleted Object")


# The x-ref shape, measured on a host holding two x-refs of one design: two DISTINCT bodies read one
# byte-identical entityToken while their source documents' lineage ids differ.
_URN_XREF = "urn:adsk.wipprod:dm.lineage:K3I2nkywRlaWPHJexysOdA"


_URN_HOST = "urn:adsk.wipprod:dm.lineage:N_QoPrrrSJmF__f9BZV86A"


_SHARED_TOKEN = "/vB+AAEAAwAAAAAAAAAAAAAA"


def _body_from_document(name, token, urn):
    """A solid body owned by a component in the document with lineage id `urn` - the chain a body's
    source document is read through (parentComponent -> parentDesign -> parentDocument ->
    dataFile.id)."""
    b = FakeBody(name, is_solid=True)
    b.entityToken = token
    b.parentComponent = MakeComp(name=name, parent_design=make_source_document(urn))
    return b


class TestOffsetThickenKind:

    def test_offset_produces_a_surface(self):
        # LIVE SHAPE: feature.bodies lists the pre-existing SOURCE solid alongside the new surface
        # (verified live: [Body1, Body2]). is_solid/result_bodies must be read off the CREATED
        # surface (via the created faces), never the source solid - an any_solid read over
        # feature.bodies reports is_solid=true for a genuine open surface.
        f1 = FakeFace()
        surf = FakeBody("Surf2", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Body1", is_solid=True), surf],
                                created_faces=[_face_on(surf)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], distance=2, units="mm"))
        assert out["offset"] is True
        assert out["is_solid"] is False           # the CREATED surface, not the polluting source solid
        assert out["result_bodies"] == ["Surf2"]  # source solid excluded
        assert out["faces_requested"] == 1 and out["faces_offset"] == 1
        assert of.last_input.dist == ("real", 0.2)

    def test_offset_default_chaining_is_off(self):
        # chaining=true silently swept a filleted body's whole tangent-connected skin (live: one
        # picked face -> a surface wrapping the entire box). The pick is explicit; expansion is opt-in.
        f1 = FakeFace()
        surf = FakeBody("Surf2", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[surf], created_faces=[_face_on(surf)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        _payload(se.handler(faces=["F1"], distance=2))
        assert of.last_input.chain is False

    def test_offset_chaining_expansion_is_reported(self):
        # chaining=true: 1 face requested, 6 tangent-connected faces offset -> the result SAYS so.
        f1 = FakeFace()
        surf = FakeBody("Skin1", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[surf],
                                created_faces=[_face_on(surf) for _ in range(6)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], distance=2, chaining=True))
        assert out["faces_requested"] == 1 and out["faces_offset"] == 6
        assert "EXPANDED" in out["note"] and "chaining=false" in out["note"]

    def test_offset_that_creates_no_faces_bites(self):
        # add() 'succeeded' but the feature created NO faces -> error, never a silent ok
        f1 = FakeFace()
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Body1", is_solid=True)], created_faces=[])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        res = se.handler(faces=["F1"], distance=2)
        assert res["isError"] is True and "created no faces" in res["message"]

    def test_offset_unknown_operation_rejected(self):
        comp = FakeComp(FakeFeatures(offset=FakeOffsetFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], distance=2, operation="cut")
        assert res["isError"] is True and "new, new_component" in res["message"]

    def test_offset_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(offset=FakeOffsetFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], distance=2, units="leagues")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]


class TestOffsetZeroDistance:

    def test_zero_distance_copies_the_face_as_a_coincident_surface(self):
        # distance=0 is legal (measured live: the feature lands, the copy reads coincident) - the
        # machining-prep copy-face idiom. The note names the coincident copy so a caller knows the
        # surface is indistinguishable from its source by eye.
        f1 = FakeFace()
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Copy1", is_solid=False)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], distance=0))
        assert out["offset"] is True
        assert out["distance"] == 0.0
        assert "COINCIDENT" in out["note"]


class TestOffsetUnreadableFaces:

    def test_unreadable_feature_faces_publish_null_plus_an_unverified_marker(self):
        # feature.faces would not read, so the zero-faces refusal never ran: publishing
        # faces_offset 0 (and an empty body list, and is_solid false) fabricates the very reads
        # that failed
        of = FakeOffsetFeatures(result_bodies=[FakeBody("Copy1", is_solid=False)])  # no created faces
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["faces_offset"] is None
        assert out["result_bodies"] is None
        assert out["is_solid"] is None
        assert out["unverified"] == ["faces_offset", "result_bodies", "is_solid"]
        assert "Not read back off the feature: faces_offset" in out["note"]

    def test_readable_faces_carry_no_unverified_marker(self):
        surf = FakeBody("Surf2", is_solid=False)
        of = FakeOffsetFeatures(result_bodies=[surf], created_faces=[_face_on(surf)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["faces_offset"] == 1
        assert "unverified" not in out

    def test_unreadable_is_solid_on_a_created_body_is_null_not_false(self):
        wall = _UnreadableSolidBody("Copy1")
        of = FakeOffsetFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["is_solid"] is None
        assert out["unverified"] == ["is_solid"]
        assert "isSolid=false" not in out["note"]


class TestCreatedBodyWalkKeysOnPhysicalIdentity:

    """_created_bodies delegates its owning-body walk to _geom.owning_bodies, whose de-dup key is
    _common.native_identity. A key built on the wrapper's own entityToken is wrong in two
    directions: it MERGES two distinct bodies whose document-local tokens collide, and it SPLITS one
    body reached both natively and through an occurrence proxy."""

    def test_two_created_bodies_sharing_a_document_local_token_are_both_published(self):
        # Keyed on the bare token these two DISTINCT bodies collapse to one entry, and the offset
        # publishes a single result body while the second disappears from the payload with no trace.
        a = _body_from_document("SurfA", _SHARED_TOKEN, _URN_XREF)
        b = _body_from_document("SurfB", _SHARED_TOKEN, _URN_HOST)
        assert a.entityToken == b.entityToken            # the fixture really models the collision
        of = FakeOffsetFeatures(result_bodies=[a, b], created_faces=[_face_on(a), _face_on(b)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["SurfA", "SurfB"]

    def test_one_body_reached_natively_and_through_its_proxy_is_ONE_result_body(self):
        native = _body_from_document("Surf1", _SHARED_TOKEN, _URN_HOST)
        proxy = body_proxy(native, types.SimpleNamespace(name="Surf1:1"))
        # the other half of the fixture: the proxy's OWN token differs, so a wrapper-token key would
        # report one physical body twice
        assert proxy.entityToken != native.entityToken
        of = FakeOffsetFeatures(result_bodies=[native],
                                created_faces=[_face_on(native), _face_on(proxy)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["Surf1"]
        assert out["faces_offset"] == 2      # the FACE count is the collection's own, not the walk's

    def test_two_created_bodies_with_no_readable_identity_stay_distinct(self):
        # The `or id(b)` last resort, unchanged by the delegation: two bodies nothing can be
        # identified from must over-count rather than merge into one entry.
        a, b = FakeBody("SurfA", is_solid=False), FakeBody("SurfB", is_solid=False)
        assert not hasattr(a, "entityToken") and not hasattr(b, "entityToken")
        of = FakeOffsetFeatures(result_bodies=[a, b], created_faces=[_face_on(a), _face_on(b)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["SurfA", "SurfB"]

    def test_several_faces_of_ONE_body_still_collapse_to_one(self):
        # The de-dup's day job, unchanged: three faces of one surface are one result body.
        surf = _body_from_document("Skin1", _SHARED_TOKEN, _URN_HOST)
        of = FakeOffsetFeatures(result_bodies=[surf],
                                created_faces=[_face_on(surf) for _ in range(3)])
        comp = FakeComp(FakeFeatures(offset=of))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], distance=2))
        assert out["result_bodies"] == ["Skin1"] and out["faces_offset"] == 3
