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
    def __iter__(self):
        # Live adsk collections are iterable (allComponents is walked with list()); model that so a
        # design-wide walk over this collection behaves like the real API.
        return iter(self._i)


UNAVAILABLE = ("3 : The occurrence's referenced component is unavailable (broken or missing "
               "external reference).")


class BrokenOcc:
    """An occurrence whose referenced component will not load. Only `name` reads; component,
    fullPathName and childOccurrences all RAISE, isReferencedComponent reads FALSE (a live xref
    reads True) and isValid reads True - so occ.component raising is the only signal."""
    def __init__(self, name="45740"):
        self.name = name
        self.isReferencedComponent = False
        self.isValid = True

    @property
    def component(self):
        raise RuntimeError(UNAVAILABLE)

    @property
    def fullPathName(self):
        raise RuntimeError("2 : InternalValidationError : path.valid()")

    @property
    def childOccurrences(self):
        raise RuntimeError("2 : InternalValidationError : path.valid()")


class FakeOcc:
    def __init__(self, name, comp=None, children=0, bodies=1, grounded=False, xref=False,
                 broken_children=()):
        self.name = name
        # component.occurrences is the COMPONENT-LOCAL superset - the only collection an unresolved
        # child appears in; childOccurrences (the assembly-context one) drops it.
        self.component = type("C", (), {"name": comp or name.split(":")[0],
                                        "occurrences": _Coll(list(broken_children)
                                                             + [None] * children)})()
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


class _RaisingWalk:
    """root.allOccurrences on a design holding an unresolved reference: the PROPERTY ACCESS itself
    raises, so the census has to be rebuilt from component.occurrences."""
    @property
    def count(self):
        raise RuntimeError("2 : InternalValidationError : occ")

    def item(self, i):
        raise RuntimeError("2 : InternalValidationError : occ")

    def __iter__(self):
        raise RuntimeError("2 : InternalValidationError : occ")


class FakeRoot:
    def __init__(self, top_occs=(), all_count=None, joints=(), bodies=0, sketches=0, bbox=None,
                 walk_raises=False):
        self.name = "Root"
        self.occurrences = _Coll(top_occs)
        self.allOccurrences = (_RaisingWalk() if walk_raises else
                               _Coll([None] * (all_count if all_count is not None else len(top_occs))))
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


class FakeSubComp:
    """A sub-component carrying its OWN sketches/bodies collections - like the live Component. It has
    NO allComponents attribute (that collection is a Design property), matching the API so a design-wide
    count that mistakenly read it off a component would raise, not silently degrade."""
    def __init__(self, name, bodies=0, sketches=0):
        self.name = name
        self.bRepBodies = _Coll([None] * bodies)
        self.sketches = _Coll([None] * sketches)


class FakeDesign:
    def __init__(self, root, timeline=(), units="mm", design_type=1, parameters=0, sub_components=(),
                 marker=None):
        self.rootComponent = root
        self.timeline = _Coll(timeline)
        if marker is not None:                 # a rolled-back marker (< count) means features after it are reverted
            self.timeline.markerPosition = marker
        self.unitsManager = _UnitsMgr(units)
        self.designType = design_type        # 1 = parametric, 0 = direct
        self.userParameters = type("UP", (), {"count": parameters})()
        # allComponents lives on the DESIGN and is a counted collection (root included), as in the live
        # API - _common.all_components reads it here, never off a Component.
        self._all_components = [root] + list(sub_components)

    @property
    def allComponents(self):
        return _Coll(self._all_components)


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
                 project="MCP Test Project", project_id="a.123", hub="Test Hub"):
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

    def test_reports_parameters_count_and_pointer(self):
        # parameters were invisible in the front-door orient - a design WITH them must report the count
        # AND a param_get breadcrumb (the same inbound crumb design_get now gives).
        des = self._small_design(parameters=18)
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["parameters"] == 18
        assert "param_get" in out["pointers"]["parameters"]

    def test_no_param_pointer_when_zero(self):
        des = self._small_design(parameters=0)
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["parameters"] == 0            # count still reported (like bodies/sketches)
        assert "parameters" not in out["pointers"]         # but no pointer when there's nothing to point at

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
        # healthState 2 = error (a real compute failure) -> flagged as failed-to-compute.
        root = FakeRoot(top_occs=[FakeOcc("A:1")],
                        joints=[FakeJoint("Good", 0), FakeJoint("PistonSlide", 2)])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["health"]["broken_joints"] == ["PistonSlide"]
        assert out["health"]["is_healthy"] is False
        # the note leads with the facts (not an "unhealthy" verdict) so a skimming agent sees what
        # was found without being told a deliberate config is broken.
        assert out["note"].startswith("Attention")
        assert "failed to compute" in out["note"]

    def test_suppressed_joint_is_not_broken(self):
        # healthState 3 = SUPPRESSED (author-parked alternate, e.g. a fixture template's reversed jaw).
        # It must NOT count as broken and must NOT drop is_healthy. (Live: 'Jaw ... REVERSED' hs=3.)
        root = FakeRoot(top_occs=[FakeOcc("A:1")],
                        joints=[FakeJoint("Active", 0), FakeJoint("Parked REVERSED", 3)])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["health"]["broken_joints"] == []
        assert out["health"]["is_healthy"] is True
        assert out["note"].startswith("No compute errors")

    def test_healthy_note_says_so(self):
        des = self._small_design()
        _install(active_product=des, doc=FakeDoc(design=des))
        assert _payload(wo.handler())["note"].startswith("No compute errors")

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


# ── timeline markers (null health) + rolled-back marker + warnings surfaced distinctly ────────────

class TestTimelineHonesty:
    def _des(self, timeline, marker=None):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], joints=[])
        return FakeDesign(root, timeline=timeline, marker=marker)

    def test_null_health_marker_counted_distinctly(self):
        # a Snapshot reads NULL health (neither healthy nor error) - counted as a marker, not silently
        # folded into an implied 'healthy'. Flip the elif off and timeline_markers goes to 0 (red).
        des = self._des([FakeTL(0), FakeTL(None), FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        h = _payload(wo.handler())["health"]
        assert h["timeline_markers"] == 1
        assert h["timeline_errors"] == 0 and h["timeline_warnings"] == 0
        assert h["is_healthy"] is True                  # a marker alone is not unhealthy

    def test_rolled_back_marker_is_unhealthy_and_surfaced(self):
        des = self._des([FakeTL(0), FakeTL(0), FakeTL(0)], marker=1)   # marker at 1 of 3 = rolled back
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["health"]["timeline_rolled_back"] is True
        assert out["health"]["is_healthy"] is False
        assert "rolled back" in out["note"]
        assert "fix_health" in out["pointers"] and "rolled-back" in out["pointers"]["fix_health"]

    def test_marker_at_end_is_not_rolled_back(self):
        des = self._des([FakeTL(0), FakeTL(0)], marker=2)             # marker at the end
        _install(active_product=des, doc=FakeDoc(design=des))
        assert _payload(wo.handler())["health"]["timeline_rolled_back"] is False

    def test_warning_surfaced_distinctly_even_when_otherwise_healthy(self):
        # errors 0 -> is_healthy stays True, but a timeline WARNING must be STATED in the note, never
        # folded into a clean 'no problems'.
        des = self._des([FakeTL(0), FakeTL(1)])                       # one warning, no error
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["health"]["timeline_warnings"] == 1
        assert out["health"]["is_healthy"] is True
        assert "WARNING" in out["note"]


# ── design-wide counts: sketches + bodies span sub-components, not just root (F25) ────────────────

class TestDesignWideCounts:
    """The sketch/body counts in the design summary must be DESIGN-WIDE (every component), not
    root/active-scoped. Live regression: a gimbal doc whose 3 sketches all live in sub-components,
    with root active, reported 'sketches: 0' - misleading a cold agent about whether geometry exists.
    """

    def test_sketches_in_sub_components_are_counted_with_empty_root(self):
        # root has ZERO sketches; the geometry lives in three sub-components. A root-only count reports
        # 0; the design-wide walk must report the true 3 (this is the exact F25 shape).
        root = FakeRoot(top_occs=[FakeOcc("Frame:1"), FakeOcc("OuterRing:1"), FakeOcc("InnerRing:1")],
                        all_count=3, sketches=0, bodies=0)
        subs = [FakeSubComp("Frame", sketches=1), FakeSubComp("OuterRing", sketches=1),
                FakeSubComp("InnerRing", sketches=1)]
        des = FakeDesign(root, timeline=[FakeTL(0)], sub_components=subs)
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["sketches"] == 3      # NOT 0 - would be 0 under a root-only count

    def test_bodies_summed_across_root_and_sub_components(self):
        # root holds 1 body; two sub-components hold 2 and 3 - total 6 across the design.
        root = FakeRoot(top_occs=[FakeOcc("A:1"), FakeOcc("B:1")], all_count=2, bodies=1, sketches=2)
        subs = [FakeSubComp("A", bodies=2, sketches=1), FakeSubComp("B", bodies=3, sketches=0)]
        des = FakeDesign(root, timeline=[FakeTL(0)], sub_components=subs)
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["bodies"] == 6        # 1 + 2 + 3, NOT the root-only 1
        assert out["design"]["sketches"] == 3      # 2 + 1 + 0, NOT the root-only 2

    def test_single_component_design_matches_root(self):
        # No sub-components: design-wide == root-only, so the common single-part case is unchanged.
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1, bodies=4, sketches=5)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["bodies"] == 4 and out["design"]["sketches"] == 5


# ── CAM detection (without switching to Manufacture) ─────────────────────────────────────────────

class TestCam:
    def test_no_cam(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, cam=None))
        out = _payload(wo.handler())
        assert out["has_cam"] is False and "cam" not in out

    def test_cam_present_with_ungenerated_ops(self, monkeypatch):
        cam = FakeCAM([FakeSetup([FakeOp(True), FakeOp(False), FakeOp(False)])])
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, cam=cam))
        monkeypatch.setattr(wo._cam_common, "get_cam", lambda: (cam, None))
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
        # the key names its noun: referenced DOCUMENTS, which is a different count from
        # doc_get(xref_tree).reference_link_count (reference LINKS).
        assert out["references"]["referenced_documents"] == 0
        assert out["references"]["out_of_date"] == []
        assert out["health"]["is_healthy"] is True
        assert "fix_references" not in out["pointers"]

    def test_references_all_current_is_healthy(self):
        self._design_with_refs([FakeRef("PartA"), FakeRef("PartB")])
        out = _payload(wo.handler())
        assert out["references"]["referenced_documents"] == 2
        assert out["references"]["out_of_date"] == []
        assert out["health"]["is_healthy"] is True
        assert "fix_references" not in out["pointers"]

    def test_out_of_date_reference_is_flagged_for_attention(self):
        self._design_with_refs([FakeRef("Fresh"), FakeRef("StalePart", out_of_date=True)])
        out = _payload(wo.handler())
        assert out["references"]["out_of_date"] == ["StalePart"]
        assert out["health"]["out_of_date_references"] == ["StalePart"]
        assert out["health"]["is_healthy"] is False              # OOD still counts against health
        assert "fix_references" in out["pointers"]
        assert "doc_update_xref" in out["pointers"]["fix_references"]
        assert "StalePart" in out["pointers"]["fix_references"]
        # the note states the facts and flags that they may be intentional, rather than a bare verdict
        assert out["note"].startswith("Attention")
        assert "out-of-date reference" in out["note"]
        assert "intentional" in out["note"]                      # tells the agent to confirm, not assume

    def test_a_healthy_design_publishes_no_unresolved_marker(self):
        # the 0-broken boundary: the list is empty, is_healthy stays true, and the note keeps its
        # clean verdict - no unresolved wording appears on a document that has none.
        self._design_with_refs([FakeRef("PartA")])
        out = _payload(wo.handler())
        assert out["health"]["unresolved_references"] == []
        assert out["health"]["is_healthy"] is True
        assert "UNRESOLVED" not in out["note"]
        assert "unresolved_references" not in out["pointers"]
        assert out["design"]["occurrences_walk"] == "allOccurrences"

    def test_one_unresolved_reference_makes_the_document_unhealthy_and_is_NAMED(self):
        # the specimen: is_healthy read TRUE beside a note declaring the document clean while an
        # occurrence's referenced component would not load.
        container = FakeOcc("Op1 Workholding Container:1", broken_children=[BrokenOcc("45740")])
        root = FakeRoot(top_occs=[container], walk_raises=True)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        h = out["health"]
        assert h["is_healthy"] is False
        assert [u["name"] for u in h["unresolved_references"]] == ["45740"]
        assert h["unresolved_references"][0]["parent_path"] == "Op1 Workholding Container:1"
        assert UNAVAILABLE in h["unresolved_references"][0]["detail"]
        assert "45740" in out["note"] and "UNRESOLVED" in out["note"]
        assert "No compute errors" not in out["note"]
        assert "45740" in out["pointers"]["unresolved_references"]

    def test_the_note_never_promises_a_source_file_or_hub(self):
        # the API exposes NO path from a broken occurrence to its document/project/hub, so the note
        # must not send the agent after one.
        container = FakeOcc("Op1 Workholding Container:1", broken_children=[BrokenOcc("45740")])
        des = FakeDesign(FakeRoot(top_occs=[container], walk_raises=True), timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        note = _payload(wo.handler())["note"]
        assert "doc_update_xref" not in note        # cannot refresh a reference with no DocumentReference
        assert "switch" not in note.lower()          # no hub advice is buildable
        assert "browser tree" in note

    def test_a_raising_walk_reports_the_recursed_marker_and_an_honest_total(self):
        # the blast radius: total_occurrences read 0 beside top_level_occurrences 5.
        container = FakeOcc("Op1:1", broken_children=[BrokenOcc("45740")])
        des = FakeDesign(FakeRoot(top_occs=[container, FakeOcc("Stock:1")], walk_raises=True),
                         timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["occurrences_walk"] == "recursed"
        assert out["design"]["total_occurrences"] == 3      # 2 readable + the unresolved one
        assert "recursed" in out["note"]

    def test_an_unreadable_census_publishes_null_not_zero(self):
        # neither walk enumerated: the count is UNKNOWN. A 0 here is a read failure dressed as a fact.
        root = FakeRoot(walk_raises=True)
        root.occurrences = _RaisingWalk()
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["design"]["total_occurrences"] is None
        assert out["design"]["occurrences_walk"] == "unreadable"
        assert "unknown rather than zero" in out["note"]

    def test_a_broken_TOP_LEVEL_occurrence_gets_a_digest_row_instead_of_a_blank_one(self):
        # a row built from swallowed reads would show it as an ordinary empty component - which is
        # exactly how it stayed invisible.
        des = FakeDesign(FakeRoot(top_occs=[BrokenOcc("45740"), FakeOcc("Stock:1")],
                                  walk_raises=True), timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        rows = {r["name"]: r for r in _payload(wo.handler())["browser_digest"]}
        assert rows["45740"]["unresolved"] is True
        assert "bodies" not in rows["45740"]           # nothing readable is claimed about it
        assert rows["Stock:1"].get("unresolved") is None

    def test_a_container_row_counts_its_unresolved_descendants(self):
        container = FakeOcc("Op1:1", children=2, broken_children=[BrokenOcc("45740")])
        des = FakeDesign(FakeRoot(top_occs=[container], walk_raises=True), timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des))
        row = _payload(wo.handler())["browser_digest"][0]
        assert row["children"] == 2                  # childOccurrences, which DROPS the broken one
        assert row["unresolved_descendants"] == 1     # so the subtree count is published beside it

    def test_the_digest_names_its_own_depth_rather_than_overstating_is_xref(self):
        # is_xref reads false on every row of a document whose references sit deeper; the note says
        # which depth the flag describes instead of leaving the agent to conclude "no references".
        self._design_with_refs([FakeRef("PartA")])
        note = _payload(wo.handler())["note"]
        assert "browser_digest is DEPTH-1" in note
        assert "referenced_documents" in note and "reference_link_count" in note

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
                          folder="Rovers", project="MCP Test Project", project_id="a.999", hub="Test Hub")
        _install(active_product=des, doc=FakeDoc(design=des, data_file=df))
        dm = _payload(wo.handler())["document"]["data_model"]
        assert dm["saved_to_cloud"] is True
        assert dm["document_id"] == "urn:adsk:lineage:xyz"
        assert dm["version_number"] == 4 and dm["latest_version_number"] == 5
        assert dm["hub"] == "Test Hub"
        assert dm["project"] == "MCP Test Project" and dm["project_id"] == "a.999"
        assert dm["folder"] == "Rovers"

    def test_version_numbers_say_which_handle_they_were_read_off(self):
        # Both numbers come off the ONE DataFile handle the open document HOLDS, and that handle
        # keeps its pre-save values - so after a save they can disagree with each other (v3 beside
        # latest 2 was observed). The note states that, and names the post-save read to trust
        # instead - it must NOT promise the numbers merely lag by a few seconds.
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, data_file=FakeDataFile(version=3, latest=2)))
        dm = _payload(wo.handler())["document"]["data_model"]
        note = dm["version_lag_note"]
        assert dm["version_number"] == 3 and dm["latest_version_number"] == 2
        assert "HOLDS" in note and "pre-save" in note
        assert "version_confirmed" in note                 # the read to trust instead
        assert "few seconds" not in note

    def test_unsaved_doc_has_no_version_note_to_warn_about(self):
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1)
        des = FakeDesign(root, timeline=[FakeTL(0)])
        _install(active_product=des, doc=FakeDoc(design=des, data_file=None))
        dm = _payload(wo.handler())["document"]["data_model"]
        assert "version_lag_note" not in dm

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

    def test_a_failed_conversion_reports_null_not_raw_centimetres(self):
        # The payload labels these numbers with the design's display unit. Falling back to the
        # UNCONVERTED cm value publishes 5 as "50 mm" - a wrong measurement wearing the right label.
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1, bbox=((0, 0, 0), (5, 5, 5)))
        des = FakeDesign(root, timeline=[FakeTL(0)], units="mm")

        def _boom(value, from_u, to_u):
            raise RuntimeError("units manager unavailable")
        des.unitsManager.convert = _boom
        _install(active_product=des, doc=FakeDoc(design=des))
        bb = _payload(wo.handler())["design"]["overall_bbox"]
        assert bb["units"] == "mm"
        assert bb["size"] == {"x": None, "y": None, "z": None}
        assert bb["center"] == {"x": None, "y": None, "z": None}
        assert bb["unreadable_values"] is True
        assert "null" in bb["note"]

    def test_an_unreadable_coordinate_reports_null_not_zero(self):
        # An unreadable max.z defaulted to 0.0 published a Z size of "0 mm" - a design that is flat,
        # which is an ANSWER, not a missing read. Only that axis goes null; x/y still report.
        class _NoZ:
            x = 5.0
            y = 5.0
            @property
            def z(self):
                raise RuntimeError("unreadable")
        root = FakeRoot(top_occs=[FakeOcc("A:1")], all_count=1, bbox=((0, 0, 0), (5, 5, 5)))
        root.boundingBox.maxPoint = _NoZ()
        des = FakeDesign(root, timeline=[FakeTL(0)], units="mm")
        _install(active_product=des, doc=FakeDoc(design=des))
        bb = _payload(wo.handler())["design"]["overall_bbox"]
        assert bb["size"] == {"x": 50.0, "y": 50.0, "z": None}
        assert bb["center"]["z"] is None and bb["center"]["x"] == 25.0
        assert bb["unreadable_values"] is True


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

    def test_an_unreadable_eye_component_reports_a_null_point_not_a_zero_axis(self):
        # (10, 0.0, 0) with the 0.0 standing in for an unreadable Y is a DIFFERENT world position
        # from the one the camera holds - null says the point is unknown, which is the truth.
        class _BadPt:
            x = 10.0
            z = 4.0
            @property
            def y(self):
                raise RuntimeError("unreadable")
        root = FakeRoot(top_occs=[FakeOcc("A:1")])
        des = FakeDesign(root, timeline=[FakeTL(0)])
        camera = _FakeCamera(camera_type=0, eye=(10, 0, 0), target=(1, 2, 3))
        camera.eye = _BadPt()
        _install(active_product=des, doc=FakeDoc(design=des), camera=camera)
        v = _payload(wo.handler())["view"]
        assert v["eye"] is None
        assert v["target"] == {"x": 1.0, "y": 2.0, "z": 3.0}     # the readable point still reports


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


class TestRelationHealthInFirstCall:
    def _design_with_constraint(self, health):
        occs = [FakeOcc("A:1")]
        root = FakeRoot(top_occs=occs, joints=[])
        con = type("C", (), {"name": "Constraint 1", "healthState": health})()
        root.assemblyConstraints = _Coll([con])
        return FakeDesign(root, timeline=[FakeTL(0)])

    def test_failed_constraint_drops_the_first_call_health(self):
        # Measured: a failed assembly constraint left this read healthy while only a deeper
        # include=['relations'] slice named it - the orientation read folds relation health in.
        des = self._design_with_constraint(health=2)
        _install(active_product=des, doc=FakeDoc(design=des))
        h = _payload(wo.handler())["health"]
        assert h["is_healthy"] is False
        assert h["broken_relations"] == ["Constraint 1"]

    def test_healthy_constraint_leaves_the_rollup_alone(self):
        des = self._design_with_constraint(health=0)
        _install(active_product=des, doc=FakeDoc(design=des))
        h = _payload(wo.handler())["health"]
        assert h["is_healthy"] is True and h["broken_relations"] == []

    def test_a_broken_relation_alone_contradicts_neither_the_verdict_nor_the_pointers(self):
        # is_healthy counts broken_relations, so the verdict sentence and the pointers must too:
        # otherwise the one fault in the design reads "No compute errors ..." beside is_healthy
        # false, with nothing naming the tool that repairs it.
        des = self._design_with_constraint(health=2)
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert out["health"]["is_healthy"] is False
        assert "No compute errors" not in out["note"]
        assert "1 assembly relation(s) failed to compute" in out["note"]
        assert "Constraint 1" in out["pointers"]["fix_relations"]
        assert out["pointers"]["fix_relations"].startswith("assembly_get(")

    def test_a_healthy_design_keeps_the_clean_verdict_and_no_relation_pointer(self):
        des = self._design_with_constraint(health=0)
        _install(active_product=des, doc=FakeDoc(design=des))
        out = _payload(wo.handler())
        assert "No compute errors" in out["note"]
        assert "fix_relations" not in out["pointers"]
