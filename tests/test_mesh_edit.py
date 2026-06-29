"""Unit tests for ``mesh_edit.py`` — the WRITE-half mesh tools (mesh_generate_face_groups,
mesh_plane_cut) plus the mesh_to_brep face-groups hint.

No live Fusion. We model the small slice of the mesh-feature API the tools touch — the
MeshGenerateFaceGroupsFeatures / MeshPlaneCutFeatures collections, their createInput/add, the enum
types, MeshBody / BRepBody fakes (named to match the Fusion type names the _inputs kind discrimination
reads), and just enough of the base-feature plumbing that run_in_base_feature() drives.

Pinned (the DoD):
  • mesh_generate_face_groups: createInput -> add succeeds, and ROUTES through run_in_base_feature —
    in PARAMETRIC it opens a base-feature scope (startEdit/finishEdit on the captured BaseFeature),
    in DIRECT it does NOT (no scope object touched, base_feature arg to inner_op is None).
  • mesh_plane_cut: each cut_type + each fill resolves the right enum onto the input; reports bodies.
  • MeshBodyRef rejects a BRep handle with the redirect message (both tools).
  • PlaneRef resolves an origin alias (xy) to the component's origin construction plane.
  • mesh_to_brep's prismatic error path now mentions mesh_generate_face_groups.
"""

import json

from conftest import load_tool

me = load_tool("mesh_edit")
mo = load_tool("mesh_ops")
inp = me._inputs


# ── fakes (named to match the Fusion type names the kind discrimination reads) ──────────────────

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
                 face_groups=0):
        self.name = name
        self.displayMesh = TriangleMesh(tri, nodes)
        self.isClosed = is_closed
        self.entityToken = token or f"MTOK::{name}"
        self.parentComponent = parent
        # faceGroups.count — observable proof that generate-face-groups applied (non-parametric path).
        self.faceGroups = _FaceGroups(face_groups)


class BRepBody:
    """Stands in for adsk.fusion.BRepBody — the WRONG kind for a mesh input."""
    def __init__(self, name="Body1", is_solid=True, token=None):
        self.name = name
        self.isSolid = is_solid
        self.entityToken = token or f"BTOK::{name}"


class ConstructionPlane:
    """Stands in for adsk.fusion.ConstructionPlane — a valid cut plane, passed through verbatim."""
    def __init__(self, name="Plane1"):
        self.name = name


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


# ── mesh-feature fakes (createInput -> input ; add -> feature with .bodies) ──────────────────────

class _FeatureResult:
    def __init__(self, name, bodies):
        self.name = name
        self.bodies = _Coll(bodies)


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


class _PlaneCutFeatures:
    def __init__(self, result_bodies=None, feat_name="PlaneCut1", raise_on_add=False,
                 none_feature=False, on_add=None):
        self._result_bodies = result_bodies if result_bodies is not None else []
        self._feat_name = feat_name
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self.last_input = None
        self.create_args = None
        self._on_add = on_add        # side effect to fire when the cut applies (e.g. grow body count)

    def createInput(self, mesh, cut_plane):
        self.create_args = (mesh, cut_plane)
        self.last_input = type("Inp", (), {})()
        return self.last_input

    def add(self, inp):
        if self.raise_on_add:
            raise RuntimeError("plane cut failed")
        if self._on_add is not None:
            self._on_add()
        if self.none_feature:
            return None
        return _FeatureResult(self._feat_name, self._result_bodies)


class _GrowingMeshBodies:
    """A meshBodies-style collection whose .count starts at `start` and jumps to `end` once the cut's
    add() has run — models split_body raising the body count (closed mesh) vs. leaving it unchanged
    (open mesh). The plane-cut fake calls .grew() in its add() to flip the count."""
    def __init__(self, start, end):
        self._start = start
        self._end = end
        self._added = False

    def grew(self):
        self._added = True

    @property
    def count(self):
        return self._end if self._added else self._start


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
    def __init__(self, comp, design_type=0, edit_object=None, all_comps=None):
        self.activeComponent = comp
        self.rootComponent = comp
        self.designType = design_type           # 0 direct, 1 parametric
        self.activeEditObject = edit_object
        self._all = all_comps if all_comps is not None else [comp]

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
    dts = adsk.fusion.DesignTypes
    dts.ParametricDesignType = 1
    dts.DirectDesignType = 0
    adsk.fusion.BaseFeature = _BaseFeature
    # face-groups method enum
    fg = adsk.fusion.MeshGenerateFaceGroupsMethodTypes
    fg.FastMeshGenerateFaceGroupsMethodType = "FAST"
    fg.AccurateMeshGenerateFaceGroupsMethodType = "ACCURATE"
    # plane-cut enums
    ct = adsk.fusion.MeshPlaneCutTypes
    ct.TrimMeshPlaneCutType = "TRIM"
    ct.SplitBodyMeshPlaneCutType = "SPLIT_BODY"
    ct.SplitFacesMeshPlaneCutType = "SPLIT_FACES"
    ft = adsk.fusion.MeshPlaneCutFillTypes
    ft.NoFillMeshPlaneCutFillType = "NOFILL"
    ft.MinimalMeshPlaneCutFillType = "MINIMAL"
    ft.UniformMeshPlaneCutFillType = "UNIFORM"
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
    # design_mode (imported by mesh_edit) reads via its own _inputs too — same _common, already patched
    return design


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── mesh_generate_face_groups ───────────────────────────────────────────────────────────────────

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
        out = _payload(me.mesh_generate_face_groups_handler(mesh="H", method="accurate"))
        assert out["generated"] is True
        assert out["method"] == "accurate"
        assert out["feature"] == "FaceGroups1"
        assert fg.add_called is True
        # the accurate enum was set on the input
        assert getattr(fg.last_input, "method", None) == "ACCURATE"
        # the convert-now-works note is present
        assert "prismatic" in out["note"].lower()

    def test_fast_method_resolves_enum(self):
        src, fg, _ = self._setup(parametric=False)
        out = _payload(me.mesh_generate_face_groups_handler(mesh="H", method="fast"))
        assert out["method"] == "fast"
        assert getattr(fg.last_input, "method", None) == "FAST"

    def test_parametric_routes_through_base_feature_scope(self):
        # PARAMETRIC -> run_in_base_feature opens the scope: the captured BaseFeature is started AND
        # finished (atomic), and the add lands inside it.
        bf = _BaseFeature()
        src, fg, _ = self._setup(parametric=True, base_feature=bf)
        out = _payload(me.mesh_generate_face_groups_handler(mesh="H"))
        assert out["generated"] is True
        assert bf.started is True and bf.finished is True   # scope opened AND closed (leak-proof)
        assert fg.add_called is True

    def test_direct_does_not_open_a_scope(self):
        # Even though a baseFeatures collection exists, DIRECT mode must NOT open/touch it.
        bf = _BaseFeature()
        src, fg, _ = self._setup(parametric=False, base_feature=bf)
        out = _payload(me.mesh_generate_face_groups_handler(mesh="H"))
        assert out["generated"] is True
        assert bf.started is False and bf.finished is False  # no scope used in direct

    def test_add_failure_surfaces_not_swallowed(self):
        self._setup(parametric=False, raise_on_add=True)
        res = me.mesh_generate_face_groups_handler(mesh="H")
        assert res["isError"] is True and "face groups failed" in res["message"]

    def test_none_feature_with_face_groups_is_success(self):
        # add() returns None in non-parametric mode but the face groups were created
        # (faceGroups.count > 0): success is judged by the side effect, not the feature return.
        self._setup(parametric=False, none_feature=True, face_groups=7)
        out = _payload(me.mesh_generate_face_groups_handler(mesh="H"))
        assert out["generated"] is True
        assert out["non_parametric"] is True
        assert out["feature"] is None
        assert out["face_group_count"] == 7

    def test_none_feature_in_parametric_scope_is_success(self):
        # PARAMETRIC: the scoped add returns None (the base-feature scope makes it non-parametric) and
        # the side effect is present -> SUCCESS, with the scope opened/closed around it.
        bf = _BaseFeature()
        self._setup(parametric=True, base_feature=bf, none_feature=True, face_groups=3)
        out = _payload(me.mesh_generate_face_groups_handler(mesh="H"))
        assert out["generated"] is True and out["non_parametric"] is True
        assert out["face_group_count"] == 3
        assert bf.started is True and bf.finished is True

    def test_brep_handle_rejected_with_redirect(self):
        _wire_adsk()
        brep = BRepBody("SolidBody", is_solid=True)
        comp = FakeComp("Comp", features=_Features())
        _install(me, FakeDesign(comp, design_type=0), handle_map={"H": brep})
        res = me.mesh_generate_face_groups_handler(mesh="H")
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
        res = me.mesh_generate_face_groups_handler(mesh="H")
        assert res["isError"] is True
        assert "meshGenerateFaceGroupsFeatures collection" in res["message"]

    def test_create_input_none_errors(self):
        src, fg, _ = self._setup(parametric=False)
        fg.createInput = lambda mesh: None
        res = me.mesh_generate_face_groups_handler(mesh="H")
        assert res["isError"] is True and "returned nothing" in res["message"]

    def test_create_input_raise_surfaces(self):
        src, fg, _ = self._setup(parametric=False)
        def _boom(mesh):
            raise RuntimeError("createInput blew up")
        fg.createInput = _boom
        res = me.mesh_generate_face_groups_handler(mesh="H")
        assert res["isError"] is True and "Could not create the face-groups input" in res["message"]


# ── mesh_plane_cut ──────────────────────────────────────────────────────────────────────────────

class TestPlaneCut:
    def _setup(self, result_bodies=None, raise_on_add=False, origin_plane=None, parametric=False,
               base_feature=None, none_feature=False, mesh_bodies=None):
        _wire_adsk()
        pc = _PlaneCutFeatures(result_bodies=result_bodies, raise_on_add=raise_on_add,
                               none_feature=none_feature)
        bf = base_feature
        feats = _Features(plane_cut=pc, base_features=_BaseFeatures(made=bf) if bf else None)
        # origin plane 'xy' -> attribute 'xYConstructionPlane'
        op = {"xYConstructionPlane": origin_plane} if origin_plane is not None else {}
        comp = FakeComp("Comp", features=feats, origin_planes=op, mesh_bodies=mesh_bodies)
        src = MeshBody("Scan")
        src.parentComponent = comp
        edit_obj = bf if parametric else None
        des = FakeDesign(comp, design_type=1 if parametric else 0, edit_object=edit_obj)
        _install(me, des, handle_map={"H": src})
        return src, pc, comp

    def test_trim_with_construction_plane_handle(self):
        plane = ConstructionPlane("CutPlane")
        result = MeshBody("ScanTrimmed")
        src, pc, _ = self._setup(result_bodies=[result])
        # pass the construction plane by a handle (entity token)
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="trim", fill="minimal"))
        assert out["cut"] is True
        assert out["cut_type"] == "trim"
        assert out["fill"] == "minimal"
        assert out["result_body_count"] == 1
        assert out["result_bodies"][0]["name"] == "ScanTrimmed"
        # the right enums were set
        assert getattr(pc.last_input, "cutType", None) == "TRIM"
        assert getattr(pc.last_input, "fillType", None) == "MINIMAL"
        # the ConstructionPlane was passed straight to createInput (not reduced to .geometry)
        assert pc.create_args[1] is plane

    def _install_plane_handle(self, plane, src, pc):
        # re-install with both the mesh handle 'H' and a plane handle 'P' resolvable
        des = FakeDesign(src.parentComponent, design_type=0)
        _install(me, des, handle_map={"H": src, "P": plane})

    def test_each_cut_type_resolves_enum(self):
        for ct_in, ct_enum in (("trim", "TRIM"), ("split_body", "SPLIT_BODY"),
                               ("split_faces", "SPLIT_FACES")):
            plane = ConstructionPlane("CP")
            src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
            self._install_plane_handle(plane, src, pc)
            out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type=ct_in))
            assert out["cut_type"] == ct_in
            assert getattr(pc.last_input, "cutType", None) == ct_enum

    def test_each_fill_resolves_enum(self):
        for fill_in, fill_enum in (("none", "NOFILL"), ("minimal", "MINIMAL"), ("uniform", "UNIFORM")):
            plane = ConstructionPlane("CP")
            src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
            self._install_plane_handle(plane, src, pc)
            out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", fill=fill_in))
            assert out["fill"] == fill_in
            assert getattr(pc.last_input, "fillType", None) == fill_enum

    def test_origin_alias_plane_resolves(self):
        # PlaneRef resolves the 'xy' origin alias to comp.xYConstructionPlane
        plane = ConstructionPlane("OriginXY")
        src, pc, comp = self._setup(result_bodies=[MeshBody("R")], origin_plane=plane)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="xy", cut_type="trim"))
        assert out["cut"] is True
        # the resolved origin plane flowed into createInput
        assert pc.create_args[1] is plane

    def test_split_body_reports_two_bodies(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("A"), MeshBody("B")])
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["result_body_count"] == 2

    def _setup_with_count(self, start, end, cut_type_grows=True, none_feature=False):
        """Wire a plane cut whose meshBodies count goes start -> end on add (models split outcome)."""
        _wire_adsk()
        coll = _GrowingMeshBodies(start, end)
        on_add = coll.grew if cut_type_grows else None
        pc = _PlaneCutFeatures(result_bodies=[MeshBody("R")], none_feature=none_feature, on_add=on_add)
        feats = _Features(plane_cut=pc)
        comp = FakeComp("Comp", features=feats, mesh_bodies=coll)
        src = MeshBody("Scan")
        src.parentComponent = comp
        des = FakeDesign(comp, design_type=0)
        plane = ConstructionPlane("CP")
        _install(me, des, handle_map={"H": src, "P": plane})
        return src, pc

    def test_split_body_became_split_true_when_count_increases(self):
        # Bug B: closed mesh — split_body raises the body count 1 -> 2 -> became_split True, no note.
        self._setup_with_count(1, 2, cut_type_grows=True)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["became_split"] is True
        assert out["mesh_bodies_before"] == 1 and out["mesh_body_count"] == 2
        assert "did not separate" not in out["note"]

    def test_split_body_became_split_false_when_count_unchanged(self):
        # Bug B: open (non-watertight) mesh — split_body applies but yields ONE body. became_split must
        # be False and the honest note must fire. cut:true (the cut DID apply) — NOT an error.
        self._setup_with_count(1, 1, cut_type_grows=False)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["cut"] is True
        assert out["became_split"] is False
        assert out["mesh_bodies_before"] == 1 and out["mesh_body_count"] == 1
        assert "did not separate" in out["note"]
        assert "watertight" in out["note"]

    def test_trim_has_no_became_split_signal(self):
        # trim never adds bodies by design — it must NOT carry a became_split flag (no false signal).
        self._setup_with_count(1, 1, cut_type_grows=False)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="trim"))
        assert "became_split" not in out
        assert "did not separate" not in out["note"]

    def test_split_faces_has_no_became_split_signal(self):
        # split_faces cuts the triangulation in place (one body) — no became_split gating either.
        self._setup_with_count(1, 1, cut_type_grows=False)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="split_faces"))
        assert "became_split" not in out
        assert "did not separate" not in out["note"]

    def test_flip_sets_is_flipped(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", flip=True))
        assert out["flipped"] is True
        assert getattr(pc.last_input, "isFlipped", None) is True

    def test_parametric_routes_through_base_feature_scope(self):
        plane = ConstructionPlane("CP")
        bf = _BaseFeature()
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], parametric=True, base_feature=bf)
        # re-install parametric design with both handles + the open scope visible
        des = FakeDesign(src.parentComponent, design_type=1, edit_object=bf)
        _install(me, des, handle_map={"H": src, "P": plane})
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        assert bf.started is True and bf.finished is True

    def test_add_failure_surfaces(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(raise_on_add=True)
        self._install_plane_handle(plane, src, pc)
        res = me.mesh_plane_cut_handler(mesh="H", plane="P")
        assert res["isError"] is True and "plane cut failed" in res["message"]

    def test_none_feature_is_success_via_mesh_body_set(self):
        # REGRESSION: add() returns None in non-parametric mode (split_body grew the mesh body set
        # from 1 -> 2). No exception = applied. Report SUCCESS via the observed mesh body count, not
        # the (None) feature object.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(none_feature=True, mesh_bodies=_Coll([MeshBody("A"), MeshBody("B")]))
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["cut"] is True
        assert out["non_parametric"] is True
        assert out["feature"] is None
        assert out["mesh_body_count"] == 2

    def test_none_feature_in_parametric_scope_is_success(self):
        # PARAMETRIC: the scoped add returns None (scope makes it non-parametric) -> SUCCESS, with the
        # base-feature scope opened/closed around the cut.
        plane = ConstructionPlane("CP")
        bf = _BaseFeature()
        src, pc, _ = self._setup(none_feature=True, parametric=True, base_feature=bf,
                                 mesh_bodies=_Coll([MeshBody("A")]))
        des = FakeDesign(src.parentComponent, design_type=1, edit_object=bf)
        _install(me, des, handle_map={"H": src, "P": plane})
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True and out["non_parametric"] is True
        assert bf.started is True and bf.finished is True

    def test_brep_handle_rejected_with_redirect(self):
        _wire_adsk()
        brep = BRepBody("SolidBody", is_solid=True)
        comp = FakeComp("Comp", features=_Features(plane_cut=_PlaneCutFeatures()))
        _install(me, FakeDesign(comp, design_type=0),
                 handle_map={"H": brep, "P": ConstructionPlane("CP")})
        res = me.mesh_plane_cut_handler(mesh="H", plane="P")
        assert res["isError"] is True
        assert "must be a MESH body" in res["message"]

    def test_missing_features_collection_errors(self):
        _wire_adsk()
        comp = FakeComp("Comp", features=_Features(plane_cut=None))
        src = MeshBody("Scan")
        src.parentComponent = comp
        des = FakeDesign(comp, design_type=0)
        _install(me, des, handle_map={"H": src, "P": ConstructionPlane("CP")})
        res = me.mesh_plane_cut_handler(mesh="H", plane="P")
        assert res["isError"] is True
        assert "meshPlaneCutFeatures collection" in res["message"]

    def test_planar_face_handle_reduced_to_its_geometry(self):
        # PlaneRef resolves a planar BRepFace; the cut wants its .geometry (a core.Plane), NOT the face.
        import adsk.core
        adsk.core.SurfaceTypes.PlaneSurfaceType = "PLANE_SURF"
        plane_geom = type("PlaneGeom", (), {"surfaceType": "PLANE_SURF"})()
        face = BRepFace(plane_geom)
        result = MeshBody("Trimmed")
        src, pc, _ = self._setup(result_bodies=[result])
        des = FakeDesign(src.parentComponent, design_type=0)
        _install(me, des, handle_map={"H": src, "P": face})
        out = _payload(me.mesh_plane_cut_handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        # the FACE was reduced to its .geometry before createInput
        assert pc.create_args[1] is plane_geom
        assert pc.create_args[1] is not face

    def test_create_input_none_errors(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
        self._install_plane_handle(plane, src, pc)
        pc.createInput = lambda mesh, cut_plane: None
        res = me.mesh_plane_cut_handler(mesh="H", plane="P")
        assert res["isError"] is True and "returned nothing" in res["message"]


# ── mesh_to_brep face-groups hint (the wired-in dead-end fix) ────────────────────────────────────

class TestMeshToBrepHint:
    def _setup_convert(self, raise_on_add=True):
        """A mesh_ops design whose convert add() RAISES, so the prismatic error path fires."""
        import adsk.fusion
        adsk.fusion.MeshBody = MeshBody
        adsk.fusion.BRepBody = BRepBody
        cm = adsk.fusion.MeshConvertMethodTypes
        cm.PrismaticMeshConvertMethodType = "PRISM"
        cm.FacetedMeshConvertMethodType = "FACET"
        cm.OrganicMeshConvertMethodType = "ORG"

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
        mo.app = type("A", (), {"activeProduct": des})()
        mo._common.app = mo.app
        adsk.fusion.Design.cast = lambda x: x if isinstance(x, _MODesign) else None
        mo._inputs._common.design = lambda: des
        mo._inputs._common.target_component = lambda d: comp
        return des

    def test_prismatic_convert_failure_mentions_face_groups_tool(self):
        self._setup_convert(raise_on_add=True)
        res = mo.mesh_to_brep_handler(mesh="H", method="prismatic")
        assert res["isError"] is True
        assert "mesh_generate_face_groups" in res["message"]

    def test_faceted_convert_failure_omits_the_hint(self):
        # the hint is prismatic-specific (face groups are a prismatic requirement)
        self._setup_convert(raise_on_add=True)
        res = mo.mesh_to_brep_handler(mesh="H", method="faceted")
        assert res["isError"] is True
        assert "mesh_generate_face_groups" not in res["message"]
