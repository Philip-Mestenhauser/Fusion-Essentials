"""Unit tests for mesh_plane_cut.py - the plane pre-flight, the cut types and the rollback."""

import json
from conftest import FakeBoundingBox3D, FakePoint, FakeVector3D, load_tool
from conftest import Plane as PlaneGeom
import adsk.fusion  # noqa: E402

me = load_tool("mesh_plane_cut")


_CUT = adsk.fusion.MeshPlaneCutTypes


_FILL = adsk.fusion.MeshPlaneCutFillTypes


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


class _PlaneCutFeatures:
    def __init__(self, result_bodies=None, feat_name="PlaneCut1", raise_on_add=False,
                 none_feature=False, on_add=None, tri_after=600, deletable=True,
                 restore_on_delete=False, delete_raises=False, timeline_drops=True,
                 blind_after=False, area_after=None, volume_after=None):
        self._result_bodies = result_bodies if result_bodies is not None else []
        self._feat_name = feat_name
        self.raise_on_add = raise_on_add
        self.none_feature = none_feature
        self.last_input = None
        self.create_args = None
        self._on_add = on_add        # side effect to fire when the cut applies (e.g. grow body count)
        # The triangle count the cut leaves on the mesh it re-triangulates - the effect the tool reads
        # back. None models a cut that touches no triangle; 0 models the whole mesh being consumed.
        self.tri_after = tri_after
        # The area/volume the cut leaves on the same bodies. None leaves them where they were - a cut
        # that moved no geometry at all.
        self.area_after = area_after
        self.volume_after = volume_after
        self.cut_mesh = None         # the in-place target, wired by the setups once the mesh exists
        self.timeline = None         # design.timeline, wired by the setups; deleteMe shrinks it
        self.deletable = deletable
        self.restore_on_delete = restore_on_delete
        self.delete_raises = delete_raises
        self.timeline_drops = timeline_drops
        self.blind_after = blind_after
        self.last_feature = None
        self._add_called = False

    def createInput(self, mesh, cut_plane):
        self.create_args = (mesh, cut_plane)
        self.last_input = type("Inp", (), {})()
        return self.last_input

    @property
    def add_called(self):
        """Did the MUTATION run? A pre-flight refusal must leave this False."""
        return self._add_called

    def _cut_bodies(self):
        return [m for m in [self.cut_mesh] + list(self._result_bodies) if m is not None]

    def add(self, inp):
        self._add_called = True
        if self.raise_on_add:
            raise RuntimeError("plane cut failed")
        if self._on_add is not None:
            self._on_add()
        before = self.cut_mesh.displayMesh.triangleCount if self.cut_mesh is not None else None
        if self.tri_after is not None:
            for m in self._cut_bodies():
                m.displayMesh.triangleCount = self.tri_after
        for attr, value in (("area", self.area_after), ("volume", self.volume_after)):
            if value is not None:
                for m in self._cut_bodies():
                    setattr(m, attr, value)
        if self.blind_after:
            for m in self._cut_bodies():
                m.displayMesh = None          # the after-count can no longer be read at all
        if self.none_feature:
            return None

        def on_delete():
            if self.timeline is not None and self.timeline_drops:
                self.timeline.count -= 1
            if self.restore_on_delete and self.cut_mesh is not None:
                self.cut_mesh.displayMesh = TriangleMesh(before, 502)

        self.last_feature = _FeatureResult(self._feat_name, self._result_bodies,
                                           deletable=self.deletable, raises=self.delete_raises,
                                           on_delete=on_delete)
        return self.last_feature


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


class TestPlaneCut:

    def _setup(self, result_bodies=None, raise_on_add=False, origin_plane=None, parametric=False,
               base_feature=None, none_feature=False, mesh_bodies=None, tri_before=1000,
               tri_after=600, deletable=True, restore_on_delete=False, delete_raises=False,
               timeline_drops=True, blind_after=False, bbox=None, area=150.0, volume=125.0,
               area_after=None, volume_after=None):
        _wire_adsk()
        pc = _PlaneCutFeatures(result_bodies=result_bodies, raise_on_add=raise_on_add,
                               none_feature=none_feature, tri_after=tri_after, deletable=deletable,
                               restore_on_delete=restore_on_delete, delete_raises=delete_raises,
                               timeline_drops=timeline_drops, blind_after=blind_after,
                               area_after=area_after, volume_after=volume_after)
        bf = base_feature
        feats = _Features(plane_cut=pc, base_features=_BaseFeatures(made=bf) if bf else None)
        # origin plane 'xy' -> attribute 'xYConstructionPlane'
        op = {"xYConstructionPlane": origin_plane} if origin_plane is not None else {}
        comp = FakeComp("Comp", features=feats, origin_planes=op, mesh_bodies=mesh_bodies)
        src = MeshBody("Scan", tri=tri_before, bbox=bbox, area=area, volume=volume)
        src.parentComponent = comp
        pc.cut_mesh = src               # the body whose triangle count the cut re-writes
        edit_obj = bf if parametric else None
        des = FakeDesign(comp, design_type=1 if parametric else 0, edit_object=edit_obj)
        pc.timeline = des.timeline      # what deleteMe shrinks, and the rollback's proof read
        _install(me, des, handle_map={"H": src})
        return src, pc, comp

    def _install_plane_handle(self, plane, src, pc):
        # re-install with both the mesh handle 'H' and a plane handle 'P' resolvable
        des = FakeDesign(src.parentComponent, design_type=0)
        pc.timeline = des.timeline      # the handler reads the rollback proof off THIS design
        _install(me, des, handle_map={"H": src, "P": plane})

    def _setup_with_count(self, start, end, cut_type_grows=True, none_feature=False,
                          is_closed=True, tri_after=600):
        """Wire a plane cut whose meshBodies count goes start -> end on add (models split outcome)."""
        _wire_adsk()
        coll = _GrowingMeshBodies(start, end)
        on_add = coll.grew if cut_type_grows else None
        pc = _PlaneCutFeatures(result_bodies=[MeshBody("R")], none_feature=none_feature, on_add=on_add,
                               tri_after=tri_after)
        feats = _Features(plane_cut=pc)
        comp = FakeComp("Comp", features=feats, mesh_bodies=coll)
        src = MeshBody("Scan", is_closed=is_closed)
        src.parentComponent = comp
        pc.cut_mesh = src
        des = FakeDesign(comp, design_type=0)
        pc.timeline = des.timeline
        plane = ConstructionPlane("CP")
        _install(me, des, handle_map={"H": src, "P": plane})
        return src, pc

    # A 20 mm cube mesh sits in 0..2 cm on every axis; the guard works in Fusion's internal cm.
    _CUBE = staticmethod(lambda: FakeBoundingBox3D(FakePoint(0, 0, 0), FakePoint(2, 2, 2)))

    def _setup_with_box(self, z_cm, cut_plane_name="FarPlane", normal=(0, 0, 1), bbox=None,
                        geometry=True, **kw):
        """A cube-mesh design plus a construction plane at z=z_cm whose geometry the guard can read
        (geometry=False models a plane whose geometry is unreadable)."""
        box = self._CUBE() if bbox is None else bbox
        src, pc, comp = self._setup(result_bodies=[MeshBody("R")], bbox=box, **kw)
        geom = PlaneGeom(FakeVector3D(*normal), FakePoint(0, 0, z_cm)) if geometry else None
        plane = ConstructionPlane(cut_plane_name, geometry=geom, component=comp)
        self._install_plane_handle(plane, src, pc)
        return src, pc, plane

    def test_trim_with_construction_plane_handle(self):
        plane = ConstructionPlane("CutPlane")
        result = MeshBody("ScanTrimmed")
        src, pc, _ = self._setup(result_bodies=[result])
        # pass the construction plane by a handle (entity token)
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim", fill="minimal"))
        assert out["cut"] is True
        assert out["cut_type"] == "trim"
        assert out["fill"] == "minimal"
        assert out["result_body_count"] == 1
        assert out["result_bodies"][0]["name"] == "ScanTrimmed"
        # the right enums were set
        assert getattr(pc.last_input, "meshPlaneCutType", None) == _CUT.TrimMeshPlaneCutType
        assert getattr(pc.last_input, "meshPlaneCutFillType", None) == _FILL.MinimalMeshPlaneCutFillType
        # the ConstructionPlane was passed straight to createInput (not reduced to .geometry)
        assert pc.create_args[1] is plane

    def test_each_cut_type_resolves_enum(self):
        for ct_in, ct_enum in (("trim", _CUT.TrimMeshPlaneCutType),
                               ("split_body", _CUT.SplitBodyMeshPlaneCutType),
                               ("split_faces", _CUT.SplitFacesMeshPlaneCutType)):
            plane = ConstructionPlane("CP")
            src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
            self._install_plane_handle(plane, src, pc)
            out = _payload(me.handler(mesh="H", plane="P", cut_type=ct_in))
            assert out["cut_type"] == ct_in
            assert getattr(pc.last_input, "meshPlaneCutType", None) == ct_enum

    def test_each_fill_resolves_enum(self):
        for fill_in, fill_enum in (("none", _FILL.NoFillMeshPlaneCutFillType),
                                   ("minimal", _FILL.MinimalMeshPlaneCutFillType),
                                   ("uniform", _FILL.UniformMeshPlaneCutFillType)):
            plane = ConstructionPlane("CP")
            src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
            self._install_plane_handle(plane, src, pc)
            out = _payload(me.handler(mesh="H", plane="P", fill=fill_in))
            assert out["fill"] == fill_in
            assert getattr(pc.last_input, "meshPlaneCutFillType", None) == fill_enum

    def test_origin_alias_plane_resolves(self):
        # PlaneRef resolves the 'xy' origin alias to comp.xYConstructionPlane
        plane = ConstructionPlane("OriginXY")
        src, pc, comp = self._setup(result_bodies=[MeshBody("R")], origin_plane=plane)
        out = _payload(me.handler(mesh="H", plane="xy", cut_type="trim"))
        assert out["cut"] is True
        # the resolved origin plane flowed into createInput
        assert pc.create_args[1] is plane

    def test_split_body_reports_two_bodies(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("A"), MeshBody("B")])
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["result_body_count"] == 2

    def test_split_body_became_split_true_when_count_increases(self):
        # Closed mesh: split_body raises the body count 1 -> 2 -> became_split True, no note.
        self._setup_with_count(1, 2, cut_type_grows=True)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["became_split"] is True
        assert out["mesh_bodies_before"] == 1 and out["mesh_body_count"] == 2
        assert "did not separate" not in out["note"]

    def test_split_body_became_split_false_when_count_unchanged(self):
        # OPEN mesh: split_body applies but yields ONE body. became_split must be False and the note
        # may name the open mesh, because is_closed was READ. cut:true (the cut DID apply).
        self._setup_with_count(1, 1, cut_type_grows=False, is_closed=False)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["cut"] is True
        assert out["became_split"] is False
        assert out["mesh_bodies_before"] == 1 and out["mesh_body_count"] == 1
        assert out["mesh_is_closed"] is False
        assert "did not separate" in out["note"]
        assert "is_closed=false" in out["note"]

    def test_split_body_no_separation_on_a_CLOSED_mesh_does_not_blame_watertightness(self):
        # The mesh reads is_closed=true, so the open-mesh explanation is ruled OUT rather than
        # asserted - the tool may only name a cause it actually observed.
        self._setup_with_count(1, 1, cut_type_grows=False, is_closed=True)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["became_split"] is False
        assert out["mesh_is_closed"] is True
        assert "did not separate" in out["note"]
        assert "does not apply" in out["note"]

    def test_trim_has_no_became_split_signal(self):
        # trim never adds bodies by design — it must NOT carry a became_split flag (no false signal).
        self._setup_with_count(1, 1, cut_type_grows=False)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert "became_split" not in out
        assert "did not separate" not in out["note"]

    def test_split_faces_has_no_became_split_signal(self):
        # split_faces cuts the triangulation in place (one body) — no became_split gating either.
        self._setup_with_count(1, 1, cut_type_grows=False)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_faces"))
        assert "became_split" not in out
        assert "did not separate" not in out["note"]

    def test_a_plane_clear_of_the_bounding_box_refuses_before_any_mutation(self):
        # The destructive case, made impossible: the plane sits 18 cm past the cube's near corner, so
        # every box corner is on one side. The feature must NEVER be created - no add, no createInput.
        src, pc, _ = self._setup_with_box(z_cm=20)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        msg = res["message"]
        assert "Refusing before any cut" in msg
        assert "'FarPlane' does not reach mesh 'Scan'" in msg
        assert "18.0 cm clear" in msg                  # the nearest corner's distance to the plane
        assert "DESTROYS the whole mesh" in msg        # the trim-specific consequence
        assert pc.add_called is False                  # THE point: no mutation happened
        assert pc.create_args is None                  # not even the input was built

    def test_the_pre_flight_refusal_words_the_consequence_per_cut_type(self):
        for ct, phrase in (("split_faces", "would split no facet"),
                           ("split_body", "would leave one body")):
            src, pc, _ = self._setup_with_box(z_cm=20)
            res = me.handler(mesh="H", plane="P", cut_type=ct)
            assert res["isError"] is True, ct
            assert phrase in res["message"], ct
            assert "DESTROYS" not in res["message"], ct
            assert pc.add_called is False, ct

    def test_a_plane_through_the_bounding_box_proceeds_to_the_cut(self):
        # The guard may only refuse a plane that MISSES: one crossing the box runs the cut normally.
        src, pc, _ = self._setup_with_box(z_cm=1)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        assert pc.add_called is True
        assert out["triangles_after"] == 600

    def test_a_plane_tangent_to_the_bounding_box_refuses_as_touching(self):
        # Tangency destroys: a plane exactly on the cube's top face took a trim from 20 triangles to 0.
        # If no box corner is strictly on one side, no facet is either, so the discarded side holds
        # everything or nothing - refusing tangency cannot false-refuse a real cut. The wording says
        # TOUCHES rather than quoting a 0.0 cm gap, which would read as a measured clearance.
        src, pc, _ = self._setup_with_box(z_cm=2)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        msg = res["message"]
        assert "only TOUCHES mesh 'Scan'" in msg
        assert "0.0 cm clear" not in msg
        assert "cm clear at the nearest" not in msg
        assert pc.add_called is False               # the mesh is never put at risk

    def test_a_plane_flush_with_the_bottom_of_the_mesh_also_refuses(self):
        # The mirror case, and the realistic one: a mesh sitting on z=0 cut by plane='xy'. The box is
        # entirely on the POSITIVE side with its nearest corners on the plane - flip=true on this is
        # what destroys such a mesh.
        src, pc, _ = self._setup_with_box(z_cm=0)
        res = me.handler(mesh="H", plane="P", cut_type="trim", flip=True)
        assert res["isError"] is True
        assert "only TOUCHES mesh 'Scan'" in res["message"]
        assert pc.add_called is False

    def test_unreadable_plane_geometry_skips_the_guard_and_the_post_gate_still_fires(self):
        # A guard may never refuse on an input it did not read. With no readable plane geometry the
        # call proceeds - and the post-cut triangle gate is what catches the annihilation.
        src, pc, _ = self._setup_with_box(z_cm=20, geometry=False, tri_after=0)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "Refusing before any cut" not in res["message"]
        assert "ANNIHILATED" in res["message"]         # defense in depth caught it instead
        assert pc.add_called is True

    def test_a_degenerate_plane_normal_skips_the_guard_instead_of_raising(self):
        # A readable origin with a ZERO-length normal is the one case where the plane's numbers exist
        # but no side can be computed: the guard must decline (and never index a null direction).
        src, pc, _ = self._setup_with_box(z_cm=20, normal=(0, 0, 0), tri_after=0)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "Refusing before any cut" not in res["message"]
        assert "ANNIHILATED" in res["message"]
        assert pc.add_called is True

    def test_an_unreadable_bounding_box_skips_the_guard(self):
        src, pc, _ = self._setup_with_box(z_cm=20, tri_after=0)
        src.boundingBox = None                          # the box read fails, the plane's does not
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "Refusing before any cut" not in res["message"]
        assert "ANNIHILATED" in res["message"]
        assert pc.add_called is True

    def test_a_mesh_in_another_coordinate_frame_skips_the_guard(self):
        # An occurrence proxy's box is root-space while a construction plane's geometry is component
        # LOCAL, so the two numbers are not comparable - the guard must decline to answer.
        src, pc, _ = self._setup_with_box(z_cm=20, tri_after=0)
        src.assemblyContext = object()                  # a proxy: root-space box, local-space plane
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "Refusing before any cut" not in res["message"]
        assert pc.add_called is True

    def test_a_plane_proxy_skips_the_guard(self):
        # A PROXY plane passes the same-component test (a proxy's .component is the underlying
        # component) while its geometry is expressed in ROOT space - so the proxy check is the only
        # thing stopping a root-space origin from being compared against a component-local box.
        src, pc, plane = self._setup_with_box(z_cm=20, tri_after=0)
        plane.assemblyContext = object()                # placed through an occurrence: root-space
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "Refusing before any cut" not in res["message"]
        assert "ANNIHILATED" in res["message"]          # the post-gate caught it instead
        assert pc.add_called is True

    def test_a_plane_in_another_component_skips_the_guard(self):
        src, pc, plane = self._setup_with_box(z_cm=20, tri_after=0)
        plane.component = FakeComp("Elsewhere")         # not the mesh's own component
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "Refusing before any cut" not in res["message"]
        assert pc.add_called is True

    def test_trim_refuses_when_the_triangle_count_is_unchanged(self):
        # A plane that does not pass through the mesh cuts no triangle off it. The count is the only
        # signal that says so, and an unchanged count must be an ERROR, not cut:true.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=None)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        msg = res["message"]
        assert "changed nothing" in msg
        assert "1000 triangles" in msg          # the count that did not move
        assert "'CP'" in msg                    # the plane that cut nothing, named
        assert "Scan" in msg                    # the mesh, named

    def test_the_unchanged_count_refusal_quotes_the_second_signal_that_agrees(self):
        # The count alone does not carry the refusal: it stands because the mesh's own area/volume
        # ALSO did not move, and the message states both readings it decided on.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(tri_after=None)
        self._install_plane_handle(plane, src, pc)
        msg = me.handler(mesh="H", plane="P", cut_type="trim")["message"]
        assert "changed nothing" in msg
        assert "area and volume did not move either" in msg
        assert "area 150.0 -> 150.0 cm2" in msg
        assert "volume 125.0 -> 125.0 cm3" in msg

    def test_an_unchanged_count_with_MOVED_geometry_is_a_landed_cut(self):
        # A trim whose fill adds exactly as many triangles as the cut removed leaves the count flat
        # while real geometry went away. Refusing that would roll back a cut that landed, so the
        # second signal decides: cut:true, with both readings published.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(tri_after=None, area_after=90.0, volume_after=62.5)
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        assert out["triangles_before"] == 1000 and out["triangles_after"] == 1000
        assert out["area_before_cm2"] == 150.0 and out["area_after_cm2"] == 90.0
        assert out["volume_before_cm3"] == 125.0 and out["volume_after_cm3"] == 62.5
        assert "MOVED" in out["note"]

    def test_an_unreadable_second_signal_leaves_the_count_as_the_only_evidence(self):
        # A build whose MeshBody.area/.volume cannot be read gives no counter-evidence, so the
        # unchanged count still refuses - and the message says the second check was unreadable
        # rather than claiming the geometry was measured flat.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(tri_after=None, area=None, volume=None)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "could not be read as a second check" in res["message"]
        assert "area unreadable; volume unreadable" in res["message"]

    def test_split_faces_is_gated_by_the_same_unchanged_count(self):
        # split_faces re-triangulates in place exactly as trim does, so it shares the gate - gating
        # only 'trim' would leave the same silent no-op reachable through the other in-place type.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=None)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="split_faces")
        assert res["isError"] is True
        assert "changed nothing" in res["message"]

    def test_split_faces_unchanged_message_describes_splitting_not_removal(self):
        # split_faces ADDS triangles where the plane crosses the facets; it removes none. Trim's
        # "cut no triangles off it" would misdescribe the mechanism on this shared path.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=None)
        self._install_plane_handle(plane, src, pc)
        msg = me.handler(mesh="H", plane="P", cut_type="split_faces")["message"]
        assert "ADDS triangles" in msg
        assert "does not cross the mesh" in msg
        assert "cut no triangles off it" not in msg

    def test_trim_refuses_when_the_whole_mesh_is_annihilated(self):
        # A trim whose kept side is the EMPTY one removes every triangle: the body survives as a
        # 0-triangle husk. That is destruction, never a cut - it must be an error naming flip.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim", flip=False)
        assert res["isError"] is True
        msg = res["message"]
        assert "ANNIHILATED" in msg
        assert "all 1000 triangles" in msg      # how much was removed
        assert "0 triangles" in msg             # what is left
        assert "flip=true" in msg               # the remedy for the wrong kept side
        assert "Scan" in msg

    def test_split_faces_annihilation_never_blames_a_kept_side_or_flip(self):
        # A split_faces that reaches zero is not a side-keeping outcome: the operation only ADDS
        # triangles, so trim's flip/kept-side remedy would send the caller after a wrong cause.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="split_faces")
        assert res["isError"] is True
        msg = res["message"]
        assert "ANNIHILATED" in msg
        assert "never removes triangles" in msg
        assert "flip=" not in msg
        assert "ONE side" not in msg

    def test_annihilation_under_flip_true_points_back_at_flip_false(self):
        # The remedy names the OTHER side of the plane, so the suggested flip inverts with the request.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim", flip=True)
        assert res["isError"] is True
        assert "flip=false" in res["message"]
        assert "flip=true" not in res["message"]

    def test_trim_publishes_triangle_counts_before_and_after(self):
        # The honest success: a real trim moved the count, and BOTH numbers are published so the
        # caller can see WHICH side survived by magnitude.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=250)
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        assert out["triangles_before"] == 1000
        assert out["triangles_after"] == 250
        assert out["result_body_count"] == 1        # the existing fields survive
        assert "UNVERIFIED" not in out["note"]

    def test_refusal_proves_the_rollback_with_the_timeline_and_reports_the_mesh_it_reads(self):
        # The rollback is attempted through the feature handle, and its PROOF is the timeline count
        # dropping - reported beside a plain read of what the mesh holds now (still 0 triangles here,
        # which the message states rather than dressing up as a restoration).
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert pc.last_feature.delete_called is True
        assert "rolled back - the timeline re-reads 2 item(s), down from 3" in res["message"]
        assert "the mesh now reads 0 triangles" in res["message"]

    def test_the_unchanged_refusal_does_not_claim_a_rollback_the_timeline_denies(self):
        # The vacuous-proof case: on the UNCHANGED-count branch the mesh's triangle count equals its
        # pre-cut value whether the rollback took or not, so a count-based proof would claim success
        # for a deleteMe() that returned true and removed nothing. The timeline count is what decides.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=None, timeline_drops=False)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        msg = res["message"]
        assert "REMAINS in the model" in msg
        assert "still re-reads 3 item(s)" in msg
        assert "rolled back" not in msg
        assert "design_delete_feature" in msg          # the remedy for what is still there

    def test_refusal_reports_a_verified_rollback_when_the_mesh_comes_back(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0, restore_on_delete=True)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "rolled back" in res["message"]
        assert "the mesh now reads 1000 triangles" in res["message"]

    def test_refusal_reports_a_declined_rollback_as_still_in_the_model(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0, deletable=False,
                                 timeline_drops=False)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "REMAINS in the model" in res["message"]
        assert "deleteMe returned False" in res["message"]

    def test_a_raising_deleteme_is_reported_as_a_raise_not_a_decline(self):
        # deleteMe() throwing and deleteMe() returning false are different answers: swallowing the
        # exception into "declined" hides the API's own explanation of why the rollback failed.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0, delete_raises=True)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        msg = res["message"]
        assert "deleteMe raised: deleteMe blew up" in msg
        assert "returned False" not in msg
        assert "rolled back - the timeline" not in msg

    def test_an_unreadable_timeline_leaves_the_rollback_unconfirmed(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], tri_after=0)
        des = FakeDesign(src.parentComponent, design_type=0)
        des.timeline = None                    # no timeline to count - the proof cannot be taken
        pc.timeline = None
        _install(me, des, handle_map={"H": src, "P": plane})
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "could not be re-read to confirm it" in res["message"]
        assert "rolled back" not in res["message"]

    def test_refusal_without_a_feature_claims_no_rollback_at_all(self):
        # add() returned nothing, so there is no handle to delete - the error must say the cut is
        # still in the model instead of claiming a rollback it never performed.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(none_feature=True, tri_after=0,
                                 mesh_bodies=_Coll([MeshBody("A")]))
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P", cut_type="trim")
        assert res["isError"] is True
        assert "no feature handle to roll back" in res["message"]
        assert "was rolled back" not in res["message"]

    def test_an_emptied_mesh_is_never_offered_design_delete_feature_as_recovery(self):
        # Removing the timeline entry does NOT bring mesh data back, so the disposition for an emptied
        # mesh must point at the document's history instead of a tool call that cannot restore it.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(none_feature=True, tri_after=0,
                                 mesh_bodies=_Coll([MeshBody("A")]))
        self._install_plane_handle(plane, src, pc)
        msg = me.handler(mesh="H", plane="P", cut_type="trim")["message"]
        assert "design_delete_feature on the wrapping base feature returned deleted:true" in msg
        assert "doc_restore_version" in msg
        assert "remove it with design_delete_feature" not in msg     # the remedy that does not work

    def test_the_unchanged_refusal_still_offers_the_timeline_remedy(self):
        # The mesh is intact there, so the inert timeline entry IS the thing to remove - the
        # no-recovery wording belongs only to a mesh that reads 0 triangles.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(none_feature=True, tri_after=None,
                                 mesh_bodies=_Coll([MeshBody("A")]))
        self._install_plane_handle(plane, src, pc)
        msg = me.handler(mesh="H", plane="P", cut_type="trim")["message"]
        assert "the mesh reads 1000 triangles" in msg
        assert "doc_restore_version" not in msg

    def test_a_zero_before_count_skips_the_gate_and_reports_it_unverified(self):
        # The gate needs a NONZERO before-count to mean anything: on a mesh already reading 0
        # triangles, "unchanged" and "annihilated" are the same observation and neither can be
        # asserted. The call must not claim the mesh was annihilated - it must say UNVERIFIED.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R", tri=0)], tri_before=0, tri_after=0)
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["triangles_before"] == 0 and out["triangles_after"] == 0
        assert "UNVERIFIED" in out["note"]
        assert "before=0, after=0" in out["note"]
        assert pc.last_feature.delete_called is False    # nothing was rolled back on a skipped gate

    def test_an_unreadable_after_count_reports_it_unverified(self):
        # The other half of the same guard: with no readable after-count the cut's effect is unknown,
        # so the payload publishes null and says so instead of gating on a missing number.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], blind_after=True)
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["triangles_before"] == 1000
        assert out["triangles_after"] is None
        assert "UNVERIFIED" in out["note"]
        assert "after=None" in out["note"]

    def test_split_body_is_not_gated_by_the_triangle_count(self):
        # split_body's effect is a NEW body, not a re-triangulation: an unchanged triangle count on
        # the first piece is normal there, so the count gate must not fire and must not be published.
        self._setup_with_count(1, 2, cut_type_grows=True, tri_after=None)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["became_split"] is True
        assert "triangles_before" not in out and "triangles_after" not in out

    def test_result_body_count_is_null_when_no_feature_came_back(self):
        # result_body_count counts the FEATURE's bodies; with no feature it is UNKNOWN, and a zero
        # there reads as "the cut produced nothing" - the misdirection that hid an annihilated mesh.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(none_feature=True, mesh_bodies=_Coll([MeshBody("A")]))
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["result_body_count"] is None
        assert out["result_bodies"] == []
        assert "'result_body_count' is null" in out["note"]

    def test_flip_sets_is_flipped(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", flip=True))
        assert out["flipped"] is True
        assert getattr(pc.last_input, "isFlip", None) is True

    def test_parametric_routes_through_base_feature_scope(self):
        plane = ConstructionPlane("CP")
        bf = _BaseFeature()
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], parametric=True, base_feature=bf)
        # re-install parametric design with both handles + the open scope visible
        des = FakeDesign(src.parentComponent, design_type=1, edit_object=bf)
        _install(me, des, handle_map={"H": src, "P": plane})
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        assert bf.started is True and bf.finished is True

    def test_add_failure_surfaces(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(raise_on_add=True)
        self._install_plane_handle(plane, src, pc)
        res = me.handler(mesh="H", plane="P")
        assert res["isError"] is True and "plane cut failed" in res["message"]

    def test_none_feature_is_success_via_mesh_body_set(self):
        # REGRESSION: add() returns None in a DIRECT design (split_body grew the mesh body set
        # from 1 -> 2). No exception = applied. Report SUCCESS via the observed mesh body count, not
        # the (None) feature object.
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(none_feature=True, mesh_bodies=_Coll([MeshBody("A"), MeshBody("B")]))
        self._install_plane_handle(plane, src, pc)
        out = _payload(me.handler(mesh="H", plane="P", cut_type="split_body"))
        assert out["cut"] is True
        assert out["design_mode"] == "direct"
        assert out["base_feature"] is None           # direct opens no scope
        assert out["feature"] is None
        assert out["mesh_body_count"] == 2
        assert me._common.DIRECT_FEATURE_NOTE in out["note"]

    def test_none_feature_in_parametric_scope_is_success(self):
        # PARAMETRIC: the scoped add returns None (the scope suppresses the feature) -> SUCCESS, with
        # the base-feature scope opened/closed around the cut. The reported mode is the DESIGN's own.
        plane = ConstructionPlane("CP")
        bf = _BaseFeature()
        src, pc, _ = self._setup(none_feature=True, parametric=True, base_feature=bf,
                                 mesh_bodies=_Coll([MeshBody("A")]))
        des = FakeDesign(src.parentComponent, design_type=1, edit_object=bf)
        _install(me, des, handle_map={"H": src, "P": plane})
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True and out["feature"] is None
        assert out["design_mode"] == "parametric"
        assert out["base_feature"] == "BaseFeature1"   # where the cut actually landed
        assert "BaseFeature1" in out["note"]
        assert "direct" not in out["note"].lower()
        assert bf.started is True and bf.finished is True

    def test_mode_is_read_before_the_scope_opens(self):
        # designType reads DIRECT while a base-feature edit scope is open; a mode read taken after
        # the cut would report 'direct' for a parametric design.
        plane = ConstructionPlane("CP")
        bf = _BaseFeature()
        src, pc, _ = self._setup(none_feature=True, parametric=True, base_feature=bf,
                                 mesh_bodies=_Coll([MeshBody("A")]))
        des = FakeDesign(src.parentComponent, design_type=1, edit_object=bf)
        _install(me, des, handle_map={"H": src, "P": plane})
        real_start = bf.startEdit

        def start_and_flip():
            des.designType = 0        # what the platform reports while the scope is open
            return real_start()

        bf.startEdit = start_and_flip
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["design_mode"] == "parametric"

    def test_a_returned_feature_is_still_named_in_parametric(self):
        # the feature path is unaffected: a real feature is named beside the parametric mode.
        plane = ConstructionPlane("CP")
        bf = _BaseFeature()
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")], parametric=True, base_feature=bf)
        des = FakeDesign(src.parentComponent, design_type=1, edit_object=bf)
        _install(me, des, handle_map={"H": src, "P": plane})
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["feature"] == "PlaneCut1" and out["design_mode"] == "parametric"
        assert "'feature' is null" not in out["note"]

    def test_brep_handle_rejected_with_redirect(self):
        _wire_adsk()
        brep = BRepBody("SolidBody", is_solid=True)
        comp = FakeComp("Comp", features=_Features(plane_cut=_PlaneCutFeatures()))
        _install(me, FakeDesign(comp, design_type=0),
                 handle_map={"H": brep, "P": ConstructionPlane("CP")})
        res = me.handler(mesh="H", plane="P")
        assert res["isError"] is True
        assert "must be a MESH body" in res["message"]

    def test_missing_features_collection_errors(self):
        _wire_adsk()
        comp = FakeComp("Comp", features=_Features(plane_cut=None))
        src = MeshBody("Scan")
        src.parentComponent = comp
        des = FakeDesign(comp, design_type=0)
        _install(me, des, handle_map={"H": src, "P": ConstructionPlane("CP")})
        res = me.handler(mesh="H", plane="P")
        assert res["isError"] is True
        assert "meshPlaneCutFeatures collection" in res["message"]

    def test_planar_face_handle_reduced_to_its_geometry(self):
        # PlaneRef resolves a planar BRepFace; the cut wants its .geometry (a core.Plane), NOT the face.
        import adsk.core
        plane_geom = type("PlaneGeom", (), {"surfaceType": adsk.core.SurfaceTypes.PlaneSurfaceType})()
        face = BRepFace(plane_geom)
        result = MeshBody("Trimmed")
        src, pc, _ = self._setup(result_bodies=[result])
        des = FakeDesign(src.parentComponent, design_type=0)
        _install(me, des, handle_map={"H": src, "P": face})
        out = _payload(me.handler(mesh="H", plane="P", cut_type="trim"))
        assert out["cut"] is True
        # the FACE was reduced to its .geometry before createInput
        assert pc.create_args[1] is plane_geom
        assert pc.create_args[1] is not face

    def test_create_input_none_errors(self):
        plane = ConstructionPlane("CP")
        src, pc, _ = self._setup(result_bodies=[MeshBody("R")])
        self._install_plane_handle(plane, src, pc)
        pc.createInput = lambda mesh, cut_plane: None
        res = me.handler(mesh="H", plane="P")
        assert res["isError"] is True and "returned nothing" in res["message"]
