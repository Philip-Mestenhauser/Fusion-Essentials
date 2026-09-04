"""Unit tests for surface_thicken.py - the solid gate, the thickness and the join disclosure."""

import json
import types
from conftest import MakeComp, load_tool, make_source_document, _NamedCollection

se = load_tool("surface_thicken")


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


class FakeThickenInput:
    def __init__(self, faces, thick, sym, op, chain):
        self.faces = faces
        self.thick = thick
        self.sym = sym
        self.op = op
        self.chain = chain


class _SwallowingThickenInput(FakeThickenInput):
    """A ThickenFeatureInput that ACCEPTS the thickenType write and keeps its default anyway."""
    def __setattr__(self, name, value):
        object.__setattr__(self, name, "SharpThickenType" if name == "thickenType" else value)


class FakeThickenFeatures:
    def __init__(self, result_bodies=None, created_faces=None, input_cls=FakeThickenInput,
                 landed_cm=None, thickness_readable=True):
        # landed_cm / thickness_readable play the same roles for the wall's own thickness parameter
        # as their counterparts on FakeExtendFeatures do for the extend distance.
        self.last_input = None
        self._result = result_bodies
        self._faces = created_faces
        self._input_cls = input_cls
        self._landed_cm = landed_cm
        self._thickness_readable = thickness_readable
    def createInput(self, faces, thick, sym, op, chain):
        self.last_input = self._input_cls(faces, thick, sym, op, chain)
        return self.last_input
    def add(self, inp):
        landed = None
        if self._thickness_readable:
            landed = self._landed_cm if self._landed_cm is not None else inp.thick[1]
        return FakeFeature(name="Thicken1", bodies=self._result, faces=self._faces,
                           thickness_cm=landed)


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

    def test_thicken_produces_a_solid(self):
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3, units="mm"))
        assert out["thickened"] is True
        assert out["is_solid"] is True            # thicken makes a solid wall
        assert tf.last_input.thick[0] == "real" and abs(tf.last_input.thick[1] - 0.3) < 1e-9
        assert tf.last_input.op == "NewBodyFeatureOperation"

    def test_thicken_that_stays_a_surface_bites(self):
        # the wall did not close into a solid: no result body reads isSolid=true -> error, not ok
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=False)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        res = se.handler(faces=["F1"], thickness=3)
        assert res["isError"] is True
        assert "did not close into a solid" in res["message"]

    def test_thicken_beside_preexisting_solid_bites(self):
        # feature.bodies can carry a PRE-EXISTING solid beside the failed wall (the same class as
        # the offset read) - the gate must read the bodies owning the CREATED faces, or the source
        # solid false-passes the closed-into-a-solid check.
        f1 = FakeFace()
        wall = FakeBody("Wall1", is_solid=False)
        tf = FakeThickenFeatures(
            result_bodies=[FakeBody("Source", is_solid=True), wall],
            created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        res = se.handler(faces=["F1"], thickness=3)
        assert res["isError"] is True
        assert "CREATED body" in res["message"]

    def test_thicken_gate_names_the_created_body(self):
        # with created faces readable, result_bodies is the CREATED body - not the whole
        # feature.bodies list with the source solid in it.
        f1 = FakeFace()
        wall = FakeBody("Wall1", is_solid=True)
        tf = FakeThickenFeatures(
            result_bodies=[FakeBody("Source", is_solid=True), wall],
            created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3))
        assert out["result_bodies"] == ["Wall1"]
        assert out["is_solid"] is True

    def test_thickness_that_reads_back_wrong_is_an_error(self):
        # the solid gate passes (a solid wall landed) while the wall is the WRONG thickness - only
        # the feature's own parameter catches that, so it is an error, not an echoed request
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)], landed_cm=0.5)
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], thickness=3, units="mm")
        assert res["isError"] is True
        assert "reads back 5.0" in res["message"] and "requested 3.0" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_thickness_read_off_the_feature_is_published(self):
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3, units="mm"))
        assert out["thickness"] == 3.0           # the wall's own parameter, in the caller's units
        assert "unverified" not in out

    def test_unreadable_thickness_is_flagged_unverified_not_silently_echoed(self):
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)],
                                 thickness_readable=False)
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3, units="mm"))
        assert out["unverified"] == ["thickness"]
        assert "Not read back off the feature: thickness." in out["note"]
        assert out["thickness"] == 3.0           # the request, published only because it is flagged

    def test_thicken_type_set_on_input_and_reported(self):
        import adsk.fusion
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3, thicken_type="rounded"))
        assert tf.last_input.thickenType is adsk.fusion.ThickenTypes.RoundedThickenType
        assert out["thicken_type"] == "rounded"

    def test_thicken_type_omitted_writes_nothing_and_reports_nothing(self):
        # a fresh input's thickenType reads 0 (measured), so an unwritten one keeps that; the
        # payload must not claim a value nobody set
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3))
        assert not hasattr(tf.last_input, "thickenType")
        assert "thicken_type" not in out

    def test_unknown_thicken_type_rejected(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], thickness=3, thicken_type="chamfered")
        assert res["isError"] is True and "sharp, rounded" in res["message"]

    def test_thicken_type_that_does_not_take_is_refused(self):
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)],
                                 input_cls=_SwallowingThickenInput)
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        res = se.handler(faces=["F1"], thickness=3, thicken_type="rounded")
        assert res["isError"] is True
        assert "thicken_type=rounded" in res["message"]
        assert "reads back unchanged" in res["message"]

    def test_thicken_symmetric_passed(self):
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        _payload(se.handler(faces=["F1"], thickness=3, symmetric=True))
        assert tf.last_input.sym is True

    def test_thicken_zero_thickness_guard(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], thickness=0)
        assert res["isError"] is True and "non-zero" in res["message"]

    def test_thicken_unknown_units_rejected(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], thickness=3, units="parsec")
        assert res["isError"] is True and "mm, cm, or in" in res["message"]

    def test_thicken_unknown_operation_rejected(self):
        comp = FakeComp(FakeFeatures(thicken=FakeThickenFeatures()))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], thickness=3, operation="intersect")
        assert res["isError"] is True and "new, join, cut" in res["message"]

    def test_thicken_join_op_maps_enum(self):
        # operation=join resolves to JoinFeatureOperation onto the thicken input
        f1 = FakeFace()
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3, operation="join"))
        assert out["operation"] == "join"
        assert tf.last_input.op == "JoinFeatureOperation"


class TestEmptyResultSetIsAnError:

    def test_thicken_with_no_result_body_is_an_error(self):
        # with no body there is nothing to read isSolid off, so a payload here can only assert a
        # SOLID wall it cannot show - result_bodies [] and is_solid false beside it
        tf = FakeThickenFeatures(result_bodies=[])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        res = se.handler(faces=["F1"], thickness=3)
        assert res["isError"] is True
        assert "owns no result body" in res["message"]
        assert "design_delete_feature" in res["message"]

    def test_one_result_body_is_the_boundary_that_passes(self):
        # exactly one body is the smallest non-empty set - the gate must bite at 0 and only at 0
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3))
        assert out["result_bodies"] == ["Wall1"]

    def test_thicken_note_states_the_flag_it_read_back(self):
        tf = FakeThickenFeatures(result_bodies=[FakeBody("Wall1", is_solid=True)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3))
        assert out["is_solid"] is True
        assert "reading back isSolid=true" in out["note"]

    def test_unreadable_is_solid_is_null_and_unverified_not_a_solid_claim(self):
        # nothing read the flag, so the note may not narrate a SOLID wall and is_solid may not be
        # a fabricated false either
        tf = FakeThickenFeatures(result_bodies=[_UnreadableSolidBody("Wall1")])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3))
        assert out["is_solid"] is None
        assert "is_solid" in out["unverified"]
        assert "UNVERIFIED" in out["note"]
        assert "isSolid=true" not in out["note"]


class TestThickenJoinDisclosure:

    def test_join_that_fused_nothing_is_disclosed(self):
        # operation='join' with a sheet touching no solid mints a NEW free-floating body while
        # publishing operation:'join' (measured) - the token diff discloses it.
        f1 = FakeFace()
        wall = FakeBody("Wall1", is_solid=True)
        tf = FakeThickenFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3, operation="join"))
        assert out["fused"] is False and out["disjoint_join"] is True
        assert "fused NOTHING" in out["note"]

    def test_join_into_an_existing_solid_is_not_flagged(self, monkeypatch):
        # The created faces land on a body whose token stood in the pre-add census - a real fuse.
        import types as _t
        f1 = FakeFace()
        target = FakeBody("Target", is_solid=True)
        target.entityToken = "tok-target"
        tf = FakeThickenFeatures(result_bodies=[target], created_faces=[_face_on(target)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: target)
        _wire(comp, handle_map={"F1": f1})
        out = _payload(se.handler(faces=["F1"], thickness=3, operation="join"))
        assert "fused" not in out and "disjoint_join" not in out


class TestThickenJoinAcrossDocuments:

    """The pre-add census is taken on the FACE's own component (census_host) while the thicken is
    added to the ACTIVE component's features, so the two sides of the before/after diff need not be
    one document - and an entityToken is DOCUMENT-LOCAL."""

    def _scene(self):
        import types as _t
        standing = _body_from_document("Xref_Frame", _SHARED_TOKEN, _URN_XREF)
        wall = _body_from_document("Wall1", _SHARED_TOKEN, _URN_HOST)
        tf = FakeThickenFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: standing)
        _wire(comp, handle_map={"F1": FakeFace()})
        return standing, wall

    def test_the_x_ref_fixture_really_models_the_collision(self):
        # Both halves must be real: with no token collision the created body was never going to be
        # mistaken for a standing one, and with one document there is nothing to tell them apart by.
        standing, wall = self._scene()
        assert standing is not wall and standing.entityToken == wall.entityToken
        assert (standing.parentComponent.parentDesign.parentDocument.dataFile.id
                != wall.parentComponent.parentDesign.parentDocument.dataFile.id)

    def test_a_new_body_whose_token_collides_with_a_censused_one_is_still_disclosed(self):
        # Keyed on the bare token the created wall reads as a body that already stood there, so the
        # join is reported as a fuse that never happened - a wrong report, not a refusal.
        self._scene()
        out = _payload(se.handler(faces=["F1"], thickness=3, operation="join"))
        assert out["fused"] is False and out["disjoint_join"] is True
        assert "Wall1" in out["note"] and "fused NOTHING" in out["note"]

    def test_a_created_body_with_NO_readable_identity_is_still_disclosed(self):
        # An unreadable identity is None, and None must not enter the census: admitted there, a
        # created body nobody could identify matches a standing body nobody could identify, and the
        # join publishes a fuse that was never verified. Both bodies here answer no entityToken.
        import types as _t
        standing = FakeBody("Standing", is_solid=True)
        wall = FakeBody("Wall1", is_solid=True)
        assert not hasattr(standing, "entityToken") and not hasattr(wall, "entityToken")
        tf = FakeThickenFeatures(result_bodies=[wall], created_faces=[_face_on(wall)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: standing)
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3, operation="join"))
        assert out["fused"] is False and out["disjoint_join"] is True

    def test_a_real_fuse_inside_ONE_saved_document_is_still_not_flagged(self):
        # The other direction: reading the document must not split a body from itself, or every join
        # in a saved document would publish a fuse-nothing warning.
        import types as _t
        target = _body_from_document("Target", _SHARED_TOKEN, _URN_HOST)
        tf = FakeThickenFeatures(result_bodies=[target], created_faces=[_face_on(target)])
        comp = FakeComp(FakeFeatures(thicken=tf))
        comp.bRepBodies = _t.SimpleNamespace(count=1, item=lambda i: target)
        _wire(comp, handle_map={"F1": FakeFace()})
        out = _payload(se.handler(faces=["F1"], thickness=3, operation="join"))
        assert "fused" not in out and "disjoint_join" not in out
