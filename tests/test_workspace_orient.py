"""Unit tests for ``workspace_orient.py`` — the cold-boot orientation call.

This tool's WHOLE POINT is progressive disclosure: one cheap read that situates the agent + a
budget-aware 'pointers' block steering to TARGETED refinement instead of whole-design dumps. So the
tests pin: the content/health rollup is assembled correctly from the object model; CAM is detected
WITHOUT a CAM product being active; the depth-1 digest is capped (not the full tree); and — the
load-bearing behaviour — the pointers flip to 'scope it' guidance once the design crosses the size
thresholds. Plus the guards (no document; a non-Design document).

No live Fusion — fakes model exactly the read surface the handler touches.
"""

import json

from conftest import load_tool

wo = load_tool("workspace_orient")


# ── fakes: just the read surface workspace_orient touches ───────────────────────────────────────

class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class FakeOcc:
    def __init__(self, name, comp=None, children=0, bodies=1, grounded=False, xref=False):
        self.name = name
        self.component = type("C", (), {"name": comp or name.split(":")[0]})()
        self.childOccurrences = _Coll([None] * children)
        self.bRepBodies = _Coll([None] * bodies)
        self.isGrounded = grounded
        self.isReferencedComponent = xref


class FakeJoint:
    def __init__(self, name, health=0):
        self.name = name
        self.healthState = health


class FakeTL:
    def __init__(self, health):
        self.healthState = health


class _Pt:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class _BBox:
    def __init__(self, mn, mx):
        self.minPoint = _Pt(*mn)
        self.maxPoint = _Pt(*mx)


class FakeRoot:
    def __init__(self, top_occs=(), all_count=None, joints=(), bodies=0, sketches=0, bbox=None):
        self.name = "Root"
        self.occurrences = _Coll(top_occs)
        self.allOccurrences = _Coll([None] * (all_count if all_count is not None else len(top_occs)))
        self.joints = _Coll(joints)
        self.bRepBodies = _Coll([None] * bodies)
        self.sketches = _Coll([None] * sketches)
        # bbox = ((minx,miny,minz),(maxx,maxy,maxz)) in cm (internal API units), or None = no geometry
        self.boundingBox = _BBox(*bbox) if bbox is not None else None


class _UnitsMgr:
    def __init__(self, units):
        self.defaultLengthUnits = units

    def convert(self, value, from_u, to_u):
        # the handler converts cm -> display units; mimic the common ones the tests use
        factor = {("cm", "mm"): 10.0, ("cm", "cm"): 1.0, ("cm", "in"): 1 / 2.54}.get((from_u, to_u), 1.0)
        return value * factor


class FakeDesign:
    def __init__(self, root, timeline=(), units="mm", design_type=1):
        self.rootComponent = root
        self.timeline = _Coll(timeline)
        self.unitsManager = _UnitsMgr(units)
        self.designType = design_type        # 1 = parametric, 0 = direct


class FakeSetup:
    def __init__(self, ops):
        self.allOperations = _Coll(ops)


class FakeOp:
    def __init__(self, has_toolpath):
        self.hasToolpath = has_toolpath


class FakeCAM:
    def __init__(self, setups):
        self.setups = _Coll(setups)


class FakeProducts:
    """document.products.itemByProductType(kind) -> the design or CAM product (or None)."""
    def __init__(self, design=None, cam=None):
        self._design = design
        self._cam = cam
    def itemByProductType(self, kind):
        if kind == "DesignProductType":
            return self._design
        if kind == "CAMProductType":
            return self._cam
        return None


class FakeRef:
    """A DocumentReference: .isOutOfDate + .dataFile.name (the external-component freshness signal)."""
    def __init__(self, name, out_of_date=False):
        self.isOutOfDate = out_of_date
        self.dataFile = type("DF", (), {"name": name})()


class FakeDataFile:
    """A saved doc's data-model identity: URN + version + web URL + parent folder/project/hub chain."""
    def __init__(self, urn="urn:adsk:lineage:abc", version=3, latest=3,
                 url="https://x/g/data", folder="Parts", folder_id="fld.1",
                 project="MCP Test Project", project_id="a.123", hub="Mechio"):
        self.id = urn
        self.versionNumber = version
        self.latestVersionNumber = latest
        self.fusionWebURL = url
        self.parentFolder = type("Fld", (), {"name": folder, "id": folder_id})()
        _hub = type("Hub", (), {"name": hub})()
        self.parentProject = type("Proj", (), {"name": project, "id": project_id,
                                               "parentHub": _hub})()


class FakeDoc:
    def __init__(self, name="Doc", design=None, cam=None, saved=True, modified=False, refs=(),
                 data_file=None):
        self.name = name
        self.isSaved = saved
        self.isModified = modified
        self.products = FakeProducts(design, cam)
        self.documentReferences = _Coll(refs)
        if data_file is not None:
            self.dataFile = data_file        # only saved docs have one (unsaved -> attr absent)


class _FakeCamera:
    def __init__(self, camera_type=0, eye=(10, 10, 10), target=(0, 0, 0)):
        self.cameraType = camera_type           # 0 ortho, 1 perspective
        self.eye = _Pt(*eye)
        self.target = _Pt(*target)


class _FakeViewport:
    def __init__(self, camera):
        self.camera = camera


class _FakeSelections:
    def __init__(self, entities):
        self._e = list(entities)
    @property
    def count(self):
        return len(self._e)
    def item(self, i):
        return type("Sel", (), {"entity": self._e[i]})()


def _install(active_product=None, doc=None, cam=None, design_for_cast=None,
             camera=None, selection=()):
    """Wire the module's app + adsk casts. active_product is what app.activeProduct returns (a design,
    a CAM product, or None); design_for_cast is what Design.cast resolves to (default: active_product
    if it's a FakeDesign). camera/selection feed the new view + selection echo."""
    cam_obj = camera if camera is not None else _FakeCamera()
    _ui = type("UI", (), {"activeWorkspace": type("W", (), {"name": "Design"})(),
                          "activeSelections": _FakeSelections(selection)})()

    class _App:
        version = "TEST.0"
        activeDocument = doc
        activeProduct = active_product
        userInterface = _ui
        activeViewport = _FakeViewport(cam_obj)
    wo.app = _App()
    wo._common.app = wo.app

    import adsk.fusion, adsk.cam
    dcast = design_for_cast if design_for_cast is not None else (
        active_product if isinstance(active_product, FakeDesign) else None)
    adsk.fusion.Design.cast = lambda x: dcast if (x is active_product or x is None) else (
        x if isinstance(x, FakeDesign) else None)
    adsk.cam.CAM.cast = lambda x: x if isinstance(x, FakeCAM) else None


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── guards ──────────────────────────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_active_document(self):
        _install(active_product=None, doc=None)
        res = wo.handler()
        assert res["isError"] is True and "No active document" in res["message"]

    def test_document_without_a_design(self):
        # a doc is open but no Design product (e.g. a drawing) -> has_design False, still reports doc/cam
        doc = FakeDoc(name="Drawing1", design=None, cam=None)
        _install(active_product=None, doc=doc, design_for_cast=None)
        out = _payload(wo.handler())
        assert out["has_design"] is False
        assert out["document"]["name"] == "Drawing1"
        assert out["has_cam"] is False
        assert "no Design product" in out["note"]


# ── the orientation report ───────────────────────────────────────────────────────────────────────

class TestOrientation:
    def _small_design(self, **kw):
        occs = [FakeOcc("Wheel:1", bodies=1, grounded=False),
                FakeOcc("Fork:1", bodies=1, grounded=True)]
        root = FakeRoot(top_occs=occs, all_count=2,
                        joints=[FakeJoint("Wheel_Spin")], bodies=0, sketches=3)
        return FakeDesign(root, timeline=[FakeTL(0), FakeTL(0)], **kw)

    def test_reports_document_and_design_identity(self):
        des = self._small_design()
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["has_design"] is True
        assert out["design"]["mode"] == "parametric"     # designType 1
        assert out["design"]["units"] == "mm"
        assert out["design"]["sketches"] == 3
        assert out["design"]["top_level_occurrences"] == 2

    def test_healthy_rollup(self):
        des = self._small_design()
        _install(active_product=des, doc=FakeDoc(design=des))
        h = _payload(wo.handler())["health"]
        assert h["is_healthy"] is True
        assert h["timeline_errors"] == 0 and h["broken_joints"] == []
        assert h["joint_count"] == 1
        assert h["grounded_occurrences"] == 1            # Fork is grounded

    def test_timeline_errors_make_it_unhealthy(self):
        occs = [FakeOcc("A:1")]
        root = FakeRoot(top_occs=occs, joints=[])
        des = FakeDesign(root, timeline=[FakeTL(0), FakeTL(2), FakeTL(1), FakeTL(3)])
        _install(active_product=des, doc=FakeDoc(design=des))
        h = _payload(wo.handler())["health"]
        assert h["timeline_errors"] == 1 and h["timeline_warnings"] == 1 and h["timeline_suppressed"] == 1
        assert h["is_healthy"] is False

    def test_broken_joint_surfaced_by_name(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")],
                        joints=[FakeJoint("Good", 0), FakeJoint("PistonSlide", 2)])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["health"]["broken_joints"] == ["PistonSlide"]
        assert out["health"]["is_healthy"] is False
        # the note must LEAD with the unhealthy verdict so a skimming agent can't miss it
        assert out["note"].startswith("⚠ UNHEALTHY")

    def test_healthy_note_says_so(self):
        des = self._small_design()
        _install(active_product=des, doc=FakeDoc(design=des))
        assert _payload(wo.handler())["note"].startswith("Healthy")

    def test_direct_mode_has_no_timeline(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[], design_type=0)   # direct
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["mode"] == "direct"
        assert out["health"]["timeline_features"] == 0

    def test_browser_digest_is_depth_one(self):
        occs = [FakeOcc("Asm:1", children=12, bodies=0, xref=True),
                FakeOcc("Plate:1", children=0, bodies=2, grounded=True)]
        root = FakeRoot(top_occs=occs)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        digest = _payload(wo.handler())["browser_digest"]
        assert len(digest) == 2
        asm = next(d for d in digest if d["name"] == "Asm:1")
        assert asm["children"] == 12 and asm["is_xref"] is True
        plate = next(d for d in digest if d["name"] == "Plate:1")
        assert plate["bodies"] == 2 and plate["grounded"] is True

    def test_digest_capped_for_wide_assemblies(self):
        occs = [FakeOcc(f"P{i}:1") for i in range(40)]
        root = FakeRoot(top_occs=occs, all_count=40)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert len(out["browser_digest"]) == wo._DIGEST_LIMIT      # capped, not all 40
        assert out["design"]["top_level_occurrences"] == 40        # but the true count is reported


# ── CAM detection (without switching to Manufacture) ─────────────────────────────────────────────

class TestCam:
    def test_no_cam(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, cam=None))
        out = _payload(wo.handler())
        assert out["has_cam"] is False and "cam" not in out

    def test_cam_present_with_ungenerated_ops(self):
        cam = FakeCAM([FakeSetup([FakeOp(True), FakeOp(False), FakeOp(False)])])
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, cam=cam))
        out = _payload(wo.handler())
        assert out["has_cam"] is True
        assert out["cam"]["setups"] == 1 and out["cam"]["total_operations"] == 3
        assert out["cam"]["ungenerated_operations"] == 2
        assert "cam" in out["pointers"] and "need generating" in out["pointers"]["cam"]


# ── external-reference (OOD) health — for ANY doc with xrefs, not just templates ─────────────────

class TestExternalReferences:
    def _design_with_refs(self, refs):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        doc = FakeDoc(design=des, refs=refs)
        _install(active_product=des, doc=doc)
        return des

    def test_no_references_is_clean(self):
        self._design_with_refs([])
        out = _payload(wo.handler())
        assert out["references"]["count"] == 0
        assert out["references"]["out_of_date"] == []
        assert out["health"]["is_healthy"] is True
        assert "fix_references" not in out["pointers"]

    def test_references_all_current_is_healthy(self):
        self._design_with_refs([FakeRef("PartA"), FakeRef("PartB")])
        out = _payload(wo.handler())
        assert out["references"]["count"] == 2
        assert out["references"]["out_of_date"] == []
        assert out["health"]["is_healthy"] is True
        assert "fix_references" not in out["pointers"]

    def test_out_of_date_reference_makes_design_unhealthy(self):
        self._design_with_refs([FakeRef("Fresh"), FakeRef("StalePart", out_of_date=True)])
        out = _payload(wo.handler())
        assert out["references"]["out_of_date"] == ["StalePart"]
        assert out["health"]["out_of_date_references"] == ["StalePart"]
        assert out["health"]["is_healthy"] is False              # OOD counts against health
        assert "fix_references" in out["pointers"]
        assert "doc_update_xref" in out["pointers"]["fix_references"]
        assert "StalePart" in out["pointers"]["fix_references"]
        assert out["note"].startswith("⚠ UNHEALTHY")
        assert "OUT-OF-DATE reference" in out["note"]

    def test_ood_reported_even_without_an_active_design(self):
        # a non-Design doc (e.g. a drawing) that still has stale xrefs must surface them
        doc = FakeDoc(name="Drawing1", design=None, cam=None,
                      refs=[FakeRef("StaleXref", out_of_date=True)])
        _install(active_product=None, doc=doc, design_for_cast=None)
        out = _payload(wo.handler())
        assert out["has_design"] is False
        assert out["references"]["out_of_date"] == ["StaleXref"]
        assert "out-of-date reference" in out["note"]


# ── POINTERS: the progressive-disclosure heart ───────────────────────────────────────────────────

class TestPointers:
    def test_small_design_points_to_whole_tree(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=3, bodies=5)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        p = _payload(wo.handler())["pointers"]
        assert "whole assembly in one call" in p["assembly_structure"]
        assert "find_geometry" in p["geometry"]

    def test_large_assembly_steers_to_scoped_tree(self):
        # > _BIG_OCCURRENCES occurrences -> the pointer must say to scope to a component, not dump all
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=wo._BIG_OCCURRENCES + 5)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert "scope to a component" in out["pointers"]["assembly_structure"]
        assert "LARGE" in out["note"]

    def test_many_bodies_steers_geometry_to_target(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=3, bodies=wo._BIG_BODIES + 1)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        p = _payload(wo.handler())["pointers"]
        assert "always scope by target" in p["geometry"]

    def test_broken_health_adds_fix_pointer(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], joints=[FakeJoint("J", 2)])
        des = FakeDesign(root, timeline=[FakeTL(2)])
        _install(active_product=des, doc=FakeDoc(design=des))
        p = _payload(wo.handler())["pointers"]
        assert "fix_health" in p and "design_recompute" in p["fix_health"]

    def test_kinematics_pointer_only_when_joints_or_grounding(self):
        # no joints, nothing grounded -> no kinematics pointer (don't suggest probing an empty thing)
        root = FakeRoot(top_occs=[FakeOcc("A:1", grounded=False)], joints=[])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        p = _payload(wo.handler())["pointers"]
        assert "kinematics" not in p


# ── data-model identity (where the doc lives: hub/project/folder + URN) ───────────────────────────

class TestDataModel:
    def test_saved_doc_reports_full_location_and_urn(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        df = FakeDataFile(urn="urn:adsk:lineage:xyz", version=4, latest=5,
                          folder="Rovers", project="MCP Test Project", project_id="a.999", hub="Mechio")
        _install(active_product=des, doc=FakeDoc(design=des, data_file=df))
        dm = _payload(wo.handler())["document"]["data_model"]
        assert dm["saved_to_cloud"] is True
        assert dm["document_id"] == "urn:adsk:lineage:xyz"
        assert dm["version_number"] == 4 and dm["latest_version_number"] == 5
        assert dm["hub"] == "Mechio"
        assert dm["project"] == "MCP Test Project" and dm["project_id"] == "a.999"
        assert dm["folder"] == "Rovers"

    def test_unsaved_doc_has_no_urn_and_note_warns(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, data_file=None))  # never saved
        out = _payload(wo.handler())
        dm = out["document"]["data_model"]
        assert dm["saved_to_cloud"] is False
        assert dm["document_id"] is None and dm["project"] is None and dm["hub"] is None
        assert "UNSAVED" in out["note"]

    def test_data_model_present_even_without_a_design(self):
        # a drawing (no Design) that IS saved still reports its data-model location
        df = FakeDataFile(project="Badass Pen", folder="Drawings")
        doc = FakeDoc(name="Sheet1", design=None, cam=None, data_file=df)
        _install(active_product=None, doc=doc, design_for_cast=None)
        dm = _payload(wo.handler())["document"]["data_model"]
        assert dm["saved_to_cloud"] is True
        assert dm["project"] == "Badass Pen" and dm["folder"] == "Drawings"


# ── overall bbox + camera view + selection echo ──────────────────────────────────────────────────

class TestBbox:
    def test_bbox_reported_in_display_units(self):
        # 0..5 cm box, units mm -> size 50 mm, center 25 mm (convert cm->mm = x10)
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1, bbox=((0, 0, 0), (5, 5, 5)))
        des = FakeDesign(root, timeline=[FakeTL(0)], units="mm")
        _install(active_product=des, doc=FakeDoc(design=des))
        bb = _payload(wo.handler())["design"]["overall_bbox"]
        assert bb["units"] == "mm"
        assert bb["size"] == {"x": 50.0, "y": 50.0, "z": 50.0}
        assert bb["center"] == {"x": 25.0, "y": 25.0, "z": 25.0}

    def test_bbox_none_when_no_geometry(self):
        root = FakeRoot(top_occs=[], all_count=0, bbox=None)   # empty/sketch-only design
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        assert _payload(wo.handler())["design"]["overall_bbox"] is None


class TestViewState:
    def test_orthographic_camera(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des),
                 camera=_FakeCamera(camera_type=0, eye=(10, 0, 0), target=(0, 0, 0)))
        v = _payload(wo.handler())["view"]
        assert v["projection"] == "orthographic"
        assert v["eye"] == {"x": 10.0, "y": 0.0, "z": 0.0}
        assert v["target"] == {"x": 0.0, "y": 0.0, "z": 0.0}

    def test_perspective_camera(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des), camera=_FakeCamera(camera_type=1))
        assert _payload(wo.handler())["view"]["projection"] == "perspective"


class TestSelectionEcho:
    def test_no_selection_is_empty(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des), selection=())
        out = _payload(wo.handler())
        assert out["selection"] == {"count": 0, "selected": []}
        assert "selection" not in out["pointers"]      # no pointer when nothing selected

    def test_selected_body_echoed_with_pointer(self):
        body = type("BRepBody", (), {"name": "Body1"})()
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des), selection=[body])
        out = _payload(wo.handler())
        assert out["selection"]["count"] == 1
        rec = out["selection"]["selected"][0]
        assert rec["kind"] == "body" and rec["name"] == "Body1"
        # a pointer to sys_get_selection (the deep read) appears when something is selected
        assert "selection" in out["pointers"] and "sys_get_selection" in out["pointers"]["selection"]

    def test_selected_face_reports_body_and_occurrence(self):
        face = type("BRepFace", (), {
            "body": type("B", (), {"name": "Plate"})(),
            "assemblyContext": type("O", (), {"fullPathName": "Sub:1+Plate:1"})(),
        })()
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des), selection=[face])
        rec = _payload(wo.handler())["selection"]["selected"][0]
        assert rec["kind"] == "face" and rec["body"] == "Plate"
        assert rec["occurrence"] == "Sub:1+Plate:1"
