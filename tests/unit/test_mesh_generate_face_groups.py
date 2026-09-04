"""Unit tests for mesh_generate_face_groups.py - the planar face-group segmentation."""

import json
from conftest import load_tool
import adsk.fusion  # noqa: E402

me = load_tool("mesh_generate_face_groups")
mesh_to_brep = load_tool("mesh_to_brep")


_FG = adsk.fusion.MeshGenerateFaceGroupsMethodTypes


inp = me._inputs


class TriangleMesh:
    def __init__(self, tri, nodes):
        self.triangleCount = tri
        self.nodeCount = nodes


class _FaceGroups:
    """MeshBody.faceGroups — a counted collection; .count is the side-effect signal for face groups."""
    def __init__(self, count=0):
        self.count = count


class MeshBody:
    """Stands in for adsk.fusion.MeshBody (a SEPARATE type from BRepBody)."""
    def __init__(self, name="Mesh1", tri=1000, nodes=502, is_closed=True, token=None, parent=None,
                 face_groups=0, bbox=None, area=150.0, volume=125.0):
        self.name = name
        self.displayMesh = TriangleMesh(tri, nodes)
        # The SECOND signal the in-place-cut gate reads beside the triangle count (Fusion's internal
        # cm2/cm3 - here a 5 cm cube). None models a build where the field cannot be read at all.
        self.area = area
        self.volume = volume
        self.isClosed = is_closed
        self.entityToken = token or f"MTOK::{name}"
        self.parentComponent = parent
        # boundingBox - what the pre-flight plane/box test reads (None = unreadable, guard skips)
        self.boundingBox = bbox
        # faceGroups.count — observable proof that generate-face-groups applied (non-parametric path).
        self.faceGroups = _FaceGroups(face_groups)


class BRepBody:
    """Stands in for adsk.fusion.BRepBody — the WRONG kind for a mesh input."""
    def __init__(self, name="Body1", is_solid=True, token=None):
        self.name = name
        self.isSolid = is_solid
        self.entityToken = token or f"BTOK::{name}"


class ConstructionPlane:
    """Stands in for adsk.fusion.ConstructionPlane — a valid cut plane, passed through verbatim.
    `geometry` (a conftest Plane: origin + normal) and `component` are what the pre-flight guard reads;
    leaving either unset is the unreadable case the guard must skip on."""
    def __init__(self, name="Plane1", geometry=None, component=None):
        self.name = name
        self.geometry = geometry
        self.component = component


class BRepFace:
    """A planar face whose .geometry is the core.Plane the cut actually wants."""
    def __init__(self, plane):
        self.geometry = plane


class _Coll:
    def __init__(self, items=()):
        self._items = list(items)

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None


class _Timeline:
    """design.timeline - only .count matters here: a rollback is proven by this number DROPPING, which
    is the one read that discriminates on the unchanged-count refusal (where the triangle count
    equals its pre-cut value whether the rollback took or not)."""
    def __init__(self, count=3):
        self.count = count


class _FeatureResult:
    """A mesh feature: .bodies, plus the deleteMe() the cut's refusal paths roll back through.
    `deletable` is what deleteMe() returns, `raises` makes it throw (a raise is a different answer
    from a decline), and `on_delete` fires the model-side effects of a real rollback (the timeline
    shrinking, the triangle count coming back) so honest and dishonest wordings can be told apart."""
    def __init__(self, name, bodies, deletable=True, raises=False, on_delete=None):
        self.name = name
        self.bodies = _Coll(bodies)
        self.deletable = deletable
        self.raises = raises
        self.delete_called = False
        self._on_delete = on_delete

    def deleteMe(self):
        self.delete_called = True
        if self.raises:
            raise RuntimeError("deleteMe blew up")
        if self._on_delete is not None:
            self._on_delete()
        return self.deletable


class _FaceGroupsFeatures:
    def __init__(self, feat_name="FaceGroups1", raise_on_add=False, none_feature=False):
        self._feat_name = feat_name
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self.last_input = None
        self.add_called = False

    def createInput(self, mesh):
        self.last_input = type("Inp", (), {"mesh": mesh})()
        return self.last_input

    def add(self, inp):
        self.add_called = True
        if self.raise_on_add:
            raise RuntimeError("face groups failed")
        if self.none_feature:
            return None
        return _FeatureResult(self._feat_name, [])


class _Features:
    def __init__(self, face_groups=None, plane_cut=None, base_features=None):
        self.meshGenerateFaceGroupsFeatures = face_groups
        self.meshPlaneCutFeatures = plane_cut
        self.baseFeatures = base_features


class _BaseFeature:
    def __init__(self):
        self.name = "BaseFeature1"
        self.started = False
        self.finished = False

    def startEdit(self):
        self.started = True
        return True

    def finishEdit(self):
        self.finished = True
        return True


class _BaseFeatures:
    def __init__(self, made):
        self._made = made

    def add(self):
        return self._made


class FakeComp:
    def __init__(self, name="Comp", features=None, origin_planes=None, mesh_bodies=None):
        self.name = name
        self.features = features
        # comp.meshBodies — the non-parametric side-effect probe for plane cut reads its .count.
        self.meshBodies = mesh_bodies if mesh_bodies is not None else _Coll()
        # PlaneRef origin alias 'xy' -> key 'xY' -> getattr(comp, 'xYConstructionPlane')
        for attr, pl in (origin_planes or {}).items():
            setattr(self, attr, pl)


class FakeDesign:
    def __init__(self, comp, design_type=0, edit_object=None, all_comps=None, timeline=None):
        self.activeComponent = comp
        self.rootComponent = comp
        self.designType = design_type           # 0 direct, 1 parametric
        self.activeEditObject = edit_object
        self._all = all_comps if all_comps is not None else [comp]
        # design.timeline - the rollback's proof read (its .count dropping)
        self.timeline = timeline if timeline is not None else _Timeline()

    @property
    def allComponents(self):
        return self._all

    @property
    def allOccurrences(self):
        return []

    def findEntityByToken(self, tok):
        return self._handle_map.get(tok, [])

    _handle_map = {}


def _wire_adsk():
    """Install the adsk.fusion type identities + enums the tools/kinds read."""
    import adsk.fusion
    adsk.fusion.MeshBody = MeshBody
    adsk.fusion.BRepBody = BRepBody
    adsk.fusion.ConstructionPlane = ConstructionPlane
    adsk.fusion.BRepFace = BRepFace
    adsk.fusion.BaseFeature = _BaseFeature
    return adsk.fusion


def _install(module, design, handle_map=None):
    """Point the tool module + the input kinds at a fake design and a token resolver."""
    handle_map = handle_map or {}
    design._handle_map = {k: [v] for k, v in handle_map.items()}
    module.app = type("A", (), {"activeProduct": design})()
    module._common.app = module.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    inp._common.design = lambda: design
    inp._common.target_component = lambda d: design.activeComponent
    # _design_common (imported by this tool) reads via its own _inputs too — same _common, already patched
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


class TestFaceGroups:

    def _setup(self, parametric=False, base_feature=None, raise_on_add=False, none_feature=False,
               face_groups=0):
        _wire_adsk()
        fg = _FaceGroupsFeatures(raise_on_add=raise_on_add, none_feature=none_feature)
        bf = base_feature
        feats = _Features(face_groups=fg, base_features=_BaseFeatures(made=bf) if bf else None)
        src = MeshBody("Scan", face_groups=face_groups)
        comp = FakeComp("Comp", features=feats)
        src.parentComponent = comp
        edit_obj = bf if parametric else None
        des = FakeDesign(comp, design_type=1 if parametric else 0, edit_object=edit_obj)
        _install(me, des, handle_map={"H": src})
        return src, fg, bf

    def test_direct_generates_without_scope(self):
        # DIRECT design -> run_in_base_feature runs inner_op(None) directly, NO base-feature touched.
        src, fg, _ = self._setup(parametric=False)
        out = _payload(me.handler(mesh="H", method="accurate"))
        assert out["generated"] is True
        assert out["method"] == "accurate"
        assert out["feature"] == "FaceGroups1"
        assert fg.add_called is True
        # the accurate enum was set on the input
        assert getattr(fg.last_input, "meshGenerateFaceGroupsMethodType", None) == _FG.AccurateGenerateFaceGroupsType
        # the convert-now-works note is present
        assert "prismatic" in out["note"].lower()

    def test_fast_method_resolves_enum(self):
        src, fg, _ = self._setup(parametric=False)
        out = _payload(me.handler(mesh="H", method="fast"))
        assert out["method"] == "fast"
        assert getattr(fg.last_input, "meshGenerateFaceGroupsMethodType", None) == _FG.FastGenerateFaceGroupsType

    def test_parametric_routes_through_base_feature_scope(self):
        # PARAMETRIC -> run_in_base_feature opens the scope: the captured BaseFeature is started AND
        # finished (atomic), and the add lands inside it.
        bf = _BaseFeature()
        src, fg, _ = self._setup(parametric=True, base_feature=bf)
        out = _payload(me.handler(mesh="H"))
        assert out["generated"] is True
        assert bf.started is True and bf.finished is True   # scope opened AND closed (leak-proof)
        assert fg.add_called is True

    def test_direct_does_not_open_a_scope(self):
        # Even though a baseFeatures collection exists, DIRECT mode must NOT open/touch it.
        bf = _BaseFeature()
        src, fg, _ = self._setup(parametric=False, base_feature=bf)
        out = _payload(me.handler(mesh="H"))
        assert out["generated"] is True
        assert bf.started is False and bf.finished is False  # no scope used in direct

    def test_add_failure_surfaces_not_swallowed(self):
        self._setup(parametric=False, raise_on_add=True)
        res = me.handler(mesh="H")
        assert res["isError"] is True and "face groups failed" in res["message"]

    def test_none_feature_with_face_groups_is_success(self):
        # add() returns None in a DIRECT design but the face groups were created
        # (faceGroups.count > 0): success is judged by the side effect, not the feature return.
        self._setup(parametric=False, none_feature=True, face_groups=7)
        out = _payload(me.handler(mesh="H"))
        assert out["generated"] is True
        assert out["design_mode"] == "direct"
        assert out["base_feature"] is None          # direct opens no scope
        assert out["feature"] is None
        assert out["face_group_count"] == 7
        assert me._common.DIRECT_FEATURE_NOTE in out["note"]

    def test_none_feature_in_parametric_scope_is_success(self):
        # PARAMETRIC: the scoped add returns None (the base-feature scope suppresses the feature) and
        # the side effect is present -> SUCCESS, with the scope opened/closed around it. The design's
        # mode is reported as PARAMETRIC - the null feature says nothing about the mode.
        bf = _BaseFeature()
        self._setup(parametric=True, base_feature=bf, none_feature=True, face_groups=3)
        out = _payload(me.handler(mesh="H"))
        assert out["generated"] is True and out["feature"] is None
        assert out["design_mode"] == "parametric"
        assert out["base_feature"] == "BaseFeature1"    # where the generation actually landed
        assert "BaseFeature1" in out["note"]
        assert "direct" not in out["note"].lower()
        assert out["face_group_count"] == 3
        assert bf.started is True and bf.finished is True

    def test_mode_is_read_before_the_scope_opens(self):
        # designType reads DIRECT while a base-feature edit scope is open, so a mode read taken after
        # the generation would report 'direct' for a parametric design.
        bf = _BaseFeature()
        src, fg, _ = self._setup(parametric=True, base_feature=bf, none_feature=True, face_groups=3)
        des = me.app.activeProduct
        real_start = bf.startEdit

        def start_and_flip():
            des.designType = 0        # what the platform reports while the scope is open
            return real_start()

        bf.startEdit = start_and_flip
        out = _payload(me.handler(mesh="H"))
        assert out["design_mode"] == "parametric"

    def test_brep_handle_rejected_with_redirect(self):
        _wire_adsk()
        brep = BRepBody("SolidBody", is_solid=True)
        comp = FakeComp("Comp", features=_Features())
        _install(me, FakeDesign(comp, design_type=0), handle_map={"H": brep})
        res = me.handler(mesh="H")
        assert res["isError"] is True
        assert "must be a MESH body" in res["message"]
        assert "SOLID body" in res["message"]

    def test_missing_features_collection_errors(self):
        # comp.features has no meshGenerateFaceGroupsFeatures -> honest error
        _wire_adsk()
        comp = FakeComp("Comp", features=_Features(face_groups=None))
        src = MeshBody("Scan")
        src.parentComponent = comp
        _install(me, FakeDesign(comp, design_type=0), handle_map={"H": src})
        res = me.handler(mesh="H")
        assert res["isError"] is True
        assert "meshGenerateFaceGroupsFeatures collection" in res["message"]

    def test_create_input_none_errors(self):
        src, fg, _ = self._setup(parametric=False)
        fg.createInput = lambda mesh: None
        res = me.handler(mesh="H")
        assert res["isError"] is True and "returned nothing" in res["message"]

    def test_create_input_raise_surfaces(self):
        src, fg, _ = self._setup(parametric=False)
        def _boom(mesh):
            raise RuntimeError("createInput blew up")
        fg.createInput = _boom
        res = me.handler(mesh="H")
        assert res["isError"] is True and "Could not create the face-groups input" in res["message"]

    def test_none_feature_with_zero_face_groups_is_a_failure(self):
        # 'generated: true' must be gated on the observed face_group_count, not reported unconditionally
        # when add() returned nothing and the mesh carries no face groups afterward - that would be a
        # silent no-op reported as success.
        self._setup(parametric=False, none_feature=True, face_groups=0)
        res = me.handler(mesh="H")
        assert res["isError"] is True
        assert "no face groups" in res["message"].lower()


class TestMeshToBrepHint:

    def _setup_convert(self, raise_on_add=True):
        """A mesh_to_brep design whose convert add() RAISES, so the prismatic error path fires."""
        import adsk.fusion
        adsk.fusion.MeshBody = MeshBody
        adsk.fusion.BRepBody = BRepBody

        class _ConvFeatures:
            def __init__(self):
                self.last_input = None

            def createInput(self, meshes):
                self.last_input = type("Inp", (), {})()
                return self.last_input

            def add(self, inp):
                if raise_on_add:
                    raise RuntimeError("MESH_FAILED_BREP")
                return None

        class _MOFeatures:
            def __init__(self, convert):
                self.meshConvertFeatures = convert

        class _MOComp:
            def __init__(self, features):
                self.name = "Comp"
                self.features = features

        class _MODesign:
            def __init__(self, comp):
                self.activeComponent = comp
                self.rootComponent = comp
                self.designType = 0

            @property
            def allComponents(self):
                return [self.activeComponent]

            @property
            def allOccurrences(self):
                return []

            def findEntityByToken(self, tok):
                return self._hm.get(tok, [])

            _hm = {}

        conv = _ConvFeatures()
        comp = _MOComp(_MOFeatures(conv))
        src = MeshBody("Scan", is_closed=True)
        src.parentComponent = comp
        des = _MODesign(comp)
        des._hm = {"H": [src]}
        mesh_to_brep.app = type("A", (), {"activeProduct": des})()
        mesh_to_brep._common.app = mesh_to_brep.app
        adsk.fusion.Design.cast = lambda x: x if isinstance(x, _MODesign) else None
        mesh_to_brep._inputs._common.design = lambda: des
        mesh_to_brep._inputs._common.target_component = lambda d: comp
        return des

    def test_prismatic_convert_failure_mentions_face_groups_tool(self):
        self._setup_convert(raise_on_add=True)
        res = mesh_to_brep.handler(mesh="H", method="prismatic")
        assert res["isError"] is True
        assert "mesh_generate_face_groups" in res["message"]

    def test_faceted_convert_failure_omits_the_hint(self):
        # the hint is prismatic-specific (face groups are a prismatic requirement)
        self._setup_convert(raise_on_add=True)
        res = mesh_to_brep.handler(mesh="H", method="faceted")
        assert res["isError"] is True
        assert "mesh_generate_face_groups" not in res["message"]
