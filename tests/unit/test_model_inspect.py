"""Tests for `model_inspect` — the measurement rich read (bbox default + include=['mass']; mesh routing).

Same fixture pattern as test_design_get/test_cam_get: stub the slice SEAMS + the TargetRef resolution,
assert the ROUTER's job — default = bbox, include=['mass'] adds mass, a MESH target routes to mesh stats,
the kind tag is surfaced, unknown include + target errors guard. The slice→handler delegation is proven
by live validation.
"""

import json
from types import SimpleNamespace

import pytest

from conftest import (load_tool, error_message, FakePoint, FakeBoundingBox3D, BRepBody,
                      _NamedCollection)

mi = load_tool("model_inspect")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _resolve_to(monkeypatch, kind):
    """Make TargetRef resolve to (a dummy entity, `kind`) and a design exist."""
    monkeypatch.setattr(mi._common, "design", lambda: object())
    monkeypatch.setattr(mi._TARGET, "resolve", lambda raw: ((object(), kind), None))


def _ok(payload):
    return {"isError": False, "content": [{"type": "text", "text": json.dumps(payload)}]}


@pytest.fixture
def stub_slices(monkeypatch):
    """Stub the inline measure CORES (_bbox / _physical_properties) + the mesh core (imported lazily
    from mesh_ops). The router's job — dispatch + compose — is what these tests pin; the cores' own
    numbers are covered by live validation."""
    import sys
    monkeypatch.setattr(mi, "_bbox", lambda design, ent, desc, frame, units: _ok({"x": 10, "y": 5, "z": 2}))
    monkeypatch.setattr(mi, "_physical_properties",
                        lambda design, ent, desc, units, accuracy, per_body: _ok({"mass_kg": 1.5}))
    stub = type("Mesh", (), {"mesh_measure_of_body": staticmethod(
        lambda mb, units: _ok({"triangle_count": 900, "is_closed": True}))})
    # `from . import mesh_ops` binds the PACKAGE ATTRIBUTE when the real module is already loaded
    # (another tool imports mesh_ops at top level), and falls back to sys.modules when it is not -
    # stub BOTH seams so the route is pinned regardless of what loaded first.
    monkeypatch.setitem(sys.modules, "mcpServer.tools.mesh_ops", stub)
    monkeypatch.setattr(sys.modules["mcpServer.tools"], "mesh_ops", stub, raising=False)


class TestDefaultAndDispatch:
    def test_solid_default_is_bbox(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        out = _payload(mi.handler(target="Body1"))
        assert out["x"] == 10 and out["kind"] == "body"
        assert "mass" not in out                         # mass is opt-in
        assert "include=" in out["note"]                 # advertises mass

    def test_design_default_is_bbox(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "design")
        out = _payload(mi.handler(target=""))
        assert out["kind"] == "design" and "x" in out

    def test_include_mass_adds_properties(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        out = _payload(mi.handler(target="Body1", include=["mass"]))
        assert out["mass"]["mass_kg"] == 1.5

    def test_mesh_target_routes_to_mesh_stats(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "mesh")
        out = _payload(mi.handler(target="Mesh1"))
        assert out["triangle_count"] == 900 and out["kind"] == "mesh"
        assert "x" not in out                            # NOT the bbox path
        assert "mesh" in out["note"].lower()             # breadcrumb explains the mesh path

    def test_mesh_is_not_a_valid_include(self, monkeypatch, stub_slices):
        # mesh stats are automatic for a mesh target, NOT a selectable slice — advertising it would lie.
        _resolve_to(monkeypatch, "body")
        res = mi.handler(target="Body1", include=["mesh"])
        assert res["isError"] and "mesh" in error_message(res).lower()


class TestGuards:
    def test_unresolvable_target_errors(self, monkeypatch):
        monkeypatch.setattr(mi._common, "design", lambda: object())
        monkeypatch.setattr(mi._TARGET, "resolve", lambda raw: (None, "no such target 'Ghost'"))
        res = mi.handler(target="Ghost")
        assert "ghost" in error_message(res).lower()

    def test_unknown_include_errors(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        res = mi.handler(target="Body1", include=["bogus"])
        assert "bogus" in error_message(res).lower() or "unknown" in error_message(res).lower()


class TestBodyAabb:
    """The default bbox spans BODIES only, read through the SHARED _geom.body_aabb (its
    boundingBox2/fallback behavior is pinned in test__geom.py) - so an orphaned datum/sketch does
    not inflate it and model_inspect + assembly_get agree on one occurrence's size."""

    def test_default_bbox_reads_via_geom_body_aabb(self, monkeypatch):
        from conftest import FakePoint, FakeBoundingBox3D
        monkeypatch.setattr(mi._common, "design", lambda: object())
        monkeypatch.setattr(mi._TARGET, "resolve", lambda raw: ((object(), "occurrence"), None))
        seen = []
        def fake_aabb(entity):
            seen.append(entity)
            return FakeBoundingBox3D(FakePoint(0, 0, 0), FakePoint(1, 2, 3))   # cm
        monkeypatch.setattr(mi._geom, "body_aabb", fake_aabb)
        out = _payload(mi.handler(target="Occ:1", units="mm"))
        assert len(seen) == 1                    # the box came from the bodies-only helper
        assert out["x"] == 10.0 and out["y"] == 20.0 and out["z"] == 30.0


class TestBodyLumpCount:
    """A body row discloses its DISCONNECTED-piece count. A multi-lump body is usually a shipped
    defect (a join that fused nothing) and no bbox or mass read can show it - the number has to come
    from the one read that can, _geom.lump_count."""

    def _resolve_body(self, monkeypatch, body, kind="body"):
        monkeypatch.setattr(mi._common, "design", lambda: object())
        monkeypatch.setattr(mi._TARGET, "resolve", lambda raw: ((body, kind), None))

    def test_a_body_target_publishes_its_lump_count(self, monkeypatch, stub_slices):
        from conftest import BRepBody
        body = BRepBody(name="Tensioner")
        body.lumps = type("L", (), {"count": 2})()
        self._resolve_body(monkeypatch, body)
        out = _payload(mi.handler(target="Tensioner"))
        assert out["lump_count"] == 2

    def test_a_single_piece_body_reads_one(self, monkeypatch, stub_slices):
        from conftest import BRepBody
        body = BRepBody(name="Bracket")
        body.lumps = type("L", (), {"count": 1})()
        self._resolve_body(monkeypatch, body)
        assert _payload(mi.handler(target="Bracket"))["lump_count"] == 1

    def test_an_unreadable_lump_count_is_published_as_unknown(self, monkeypatch, stub_slices):
        # None (not 0, not 1) - the caller can tell "one solid piece" from "nobody knows".
        from conftest import BRepBody
        self._resolve_body(monkeypatch, BRepBody(name="Plain"))
        assert _payload(mi.handler(target="Plain"))["lump_count"] is None

    def test_a_non_body_target_does_not_carry_a_lump_count(self, monkeypatch, stub_slices):
        # An occurrence/component/design spans many bodies, so a single lump count would describe
        # nothing - the key belongs to a BODY row only.
        _resolve_to(monkeypatch, "occurrence")
        assert "lump_count" not in _payload(mi.handler(target="Occ:1"))


class TestNormalizeInclude:
    def test_comma_string(self):
        assert mi._normalize_include("mass") == ["mass"]

    def test_none_empty(self):
        assert mi._normalize_include(None) == [] and mi._normalize_include("") == []


# ── _measurable_geometry: getOrientedBoundingBox needs B-Rep, a Component must fall back ───────────

class TestMeasurableGeometry:
    def test_brep_body_passes_through(self):
        b = BRepBody(name="Plate")
        geom, note = mi._measurable_geometry(b)
        assert geom is b and note == ""

    def test_entity_without_bodies_collection_passes_through(self):
        e = SimpleNamespace(name="not a component")     # no bRepBodies -> assumed already B-Rep
        geom, note = mi._measurable_geometry(e)
        assert geom is e and note == ""

    def test_component_with_no_bodies_yields_none(self):
        comp = SimpleNamespace(bRepBodies=_NamedCollection([]))
        geom, _ = mi._measurable_geometry(comp)
        assert geom is None

    def test_component_single_body_falls_back_to_it_and_names_it(self):
        body = SimpleNamespace(name="Core", boundingBox=None)
        comp = SimpleNamespace(bRepBodies=_NamedCollection([body]))
        geom, note = mi._measurable_geometry(comp)
        assert geom is body and "Core" in note

    def test_multi_body_component_measures_the_largest_by_aabb_volume(self):
        small = SimpleNamespace(name="Pin", boundingBox=FakeBoundingBox3D(
            FakePoint(0, 0, 0), FakePoint(1, 1, 1)))            # volume 1
        big = SimpleNamespace(name="Block", boundingBox=FakeBoundingBox3D(
            FakePoint(0, 0, 0), FakePoint(10, 2, 1)))           # volume 20
        comp = SimpleNamespace(bRepBodies=_NamedCollection([small, big]))
        geom, note = mi._measurable_geometry(comp)
        assert geom is big
        assert "largest of 2" in note and "Block" in note       # the fallback is flagged to the caller


# ── _joint_origin_axes: the JO frame -> bbox axes mapping ──────────────────────────────────────────

class TestJointOriginAxes:
    def test_axes_map_secondary_third_primary_to_xyz(self, monkeypatch):
        # X=secondaryAxisVector, Y=thirdAxisVector, Z=primaryAxisVector - swapping any of these
        # would silently measure the box in a rotated frame.
        jo = SimpleNamespace(secondaryAxisVector="SEC", thirdAxisVector="THIRD",
                             primaryAxisVector="PRIM", name="MachineFrame")
        monkeypatch.setattr(mi._joints, "find_joint_origins_by_name", lambda d, n: [(jo, "path")])
        assert mi._joint_origin_axes(None, "MachineFrame") == ("SEC", "THIRD", "PRIM", "MachineFrame")

    def test_no_match_returns_nones(self, monkeypatch):
        monkeypatch.setattr(mi._joints, "find_joint_origins_by_name", lambda d, n: [])
        assert mi._joint_origin_axes(None, "Ghost") == (None, None, None, None)


# ── the frame= path of _bbox: oriented box in a joint-origin frame ─────────────────────────────────

class TestBboxFramePath:
    def _axes(self):
        return (SimpleNamespace(x=1, y=0, z=0), SimpleNamespace(x=0, y=1, z=0),
                SimpleNamespace(x=0, y=0, z=1))

    def test_oriented_bbox_measured_with_the_frame_axes(self, monkeypatch):
        xv, yv, zv = self._axes()
        monkeypatch.setattr(mi, "_joint_origin_axes", lambda d, n: (xv, yv, zv, "PartFrame"))
        obb = SimpleNamespace(length=1.0, width=2.0, height=0.5, centerPoint=FakePoint(1, 2, 3))
        seen = {}
        def gobb(geom, x, y):
            seen["args"] = (geom, x, y)
            return obb
        monkeypatch.setattr(mi, "app",
                            SimpleNamespace(measureManager=SimpleNamespace(getOrientedBoundingBox=gobb)))
        body = BRepBody(name="Plate")
        out = _payload(mi._bbox(None, body, "body 'Plate'", "PartFrame", "mm"))
        assert seen["args"] == (body, xv, yv)               # measured with the frame's X/Y axes
        assert out["oriented"] is True and out["frame"] == "joint origin 'PartFrame' (part space)"
        # length=X, width=Y, height=Z, scaled cm -> mm
        assert (out["x"], out["y"], out["z"]) == (10.0, 20.0, 5.0)
        assert out["center"] == {"x": 10.0, "y": 20.0, "z": 30.0}
        assert out["frame_axes"]["z_axis"] == [0, 0, 1]

    def test_unknown_frame_errors_naming_it(self, monkeypatch):
        monkeypatch.setattr(mi, "_joint_origin_axes", lambda d, n: (None, None, None, None))
        res = mi._bbox(None, object(), "whole design", "Ghost", "mm")
        assert res["isError"] and "Ghost" in error_message(res)

    def test_frame_target_without_brep_body_errors(self, monkeypatch):
        # a Component with no bodies has nothing getOrientedBoundingBox accepts - refuse with a pointer.
        xv, yv, zv = self._axes()
        monkeypatch.setattr(mi, "_joint_origin_axes", lambda d, n: (xv, yv, zv, "F"))
        monkeypatch.setattr(mi, "app", SimpleNamespace(measureManager=object()))
        comp = SimpleNamespace(bRepBodies=_NamedCollection([]))
        res = mi._bbox(None, comp, "component 'Empty'", "F", "mm")
        assert res["isError"] and "no B-Rep body" in error_message(res)

    def test_oriented_measure_failure_is_an_error(self, monkeypatch):
        xv, yv, zv = self._axes()
        monkeypatch.setattr(mi, "_joint_origin_axes", lambda d, n: (xv, yv, zv, "F"))
        def boom(geom, x, y):
            raise RuntimeError("axes not perpendicular")
        monkeypatch.setattr(mi, "app",
                            SimpleNamespace(measureManager=SimpleNamespace(getOrientedBoundingBox=boom)))
        res = mi._bbox(None, BRepBody(name="Plate"), "body 'Plate'", "F", "mm")
        assert res["isError"] and "axes not perpendicular" in error_message(res)

    def test_unknown_units_errors(self):
        res = mi._bbox(None, object(), "x", "", "furlong")
        assert res["isError"] and "furlong" in error_message(res)


class TestWorldAlignedExtentsAreMeasurements:
    """The world-aligned branch publishes x/y/z/center under the SAME keys as the oriented branch,
    so it holds the SAME contract: an unreadable corner is null, never a confident 0. A 0 extent is
    an answer ("this plate is flat in Z") and a 0 centre is an answer ("it sits on the origin")."""

    class _BlindPoint:
        """A Point3D whose coordinate reads RAISE - a proxy that stopped answering."""
        def __getattr__(self, name):
            if name in ("x", "y", "z"):
                raise RuntimeError("point unavailable")
            raise AttributeError(name)

    def _entity(self, monkeypatch, box):
        monkeypatch.setattr(mi._geom, "body_aabb", lambda ent: box)
        return object()

    def test_readable_corners_give_the_extents_and_the_centre(self, monkeypatch):
        box = FakeBoundingBox3D(FakePoint(0, 0, 0), FakePoint(1, 2, 3))
        ent = self._entity(monkeypatch, box)
        out = _payload(mi._bbox(None, ent, "body 'Plate'", "", "mm"))
        assert (out["x"], out["y"], out["z"]) == (10.0, 20.0, 30.0)
        assert out["center"] == {"x": 5.0, "y": 10.0, "z": 15.0}
        assert out["oriented"] is False

    def test_an_unreadable_corner_reads_null_not_zero(self, monkeypatch):
        box = FakeBoundingBox3D(FakePoint(0, 0, 0), self._BlindPoint())
        ent = self._entity(monkeypatch, box)
        out = _payload(mi._bbox(None, ent, "body 'Plate'", "", "mm"))
        assert (out["x"], out["y"], out["z"]) == (None, None, None)
        assert out["center"] == {"x": None, "y": None, "z": None}

    def test_a_genuinely_flat_axis_still_reports_zero(self, monkeypatch):
        # the null must mean UNREADABLE and nothing else: a real zero extent is still a 0
        box = FakeBoundingBox3D(FakePoint(0, 0, 5), FakePoint(1, 2, 5))
        ent = self._entity(monkeypatch, box)
        out = _payload(mi._bbox(None, ent, "body 'Shim'", "", "mm"))
        assert out["z"] == 0.0 and out["center"]["z"] == 50.0


# ── _full_props / _physical_properties: unit scaling + guards + per-occurrence breakdown ───────────

def _make_pp(**over):
    """A PhysicalProperties fake with concrete cm-based values (the API reports cm)."""
    pp = SimpleNamespace(mass=2.0, volume=4.0, area=6.0, density=0.0078,
                         centerOfMass=SimpleNamespace(x=1.0, y=2.0, z=3.0), accuracy=None)
    pp.getXYZMomentsOfInertia = lambda: (True, 1.0, 2.0, 3.0, 0.4, 0.5, 0.6)
    pp.getPrincipalMomentsOfInertia = lambda: (True, 5.0, 6.0, 7.0)
    pp.getPrincipalAxes = lambda: (True, SimpleNamespace(x=1, y=0, z=0),
                                   SimpleNamespace(x=0, y=1, z=0), SimpleNamespace(x=0, y=0, z=1))
    pp.getRadiusOfGyration = lambda: (True, 0.5, 0.6, 0.7)
    pp.getRotationToPrincipal = lambda: (True, 0.1, 0.2, 0.3)
    for k, v in over.items():
        setattr(pp, k, v)
    return pp


_DEFAULT_PP = object()      # "build a default PhysicalProperties", distinct from an explicit None


def _occ_row(path, mass=1.0, children=(), pp=_DEFAULT_PP):
    """One occurrence as the per_body walk reads it: a full path, physical properties (pp=None for
    an unmeasurable one), and its own child occurrences - the collection that says whether its mass
    already aggregates anything."""
    props = (SimpleNamespace(mass=mass, centerOfMass=SimpleNamespace(x=0.0, y=0.0, z=0.0))
             if pp is _DEFAULT_PP else pp)
    return SimpleNamespace(name=path.split("+")[-1], fullPathName=path,
                           getPhysicalProperties=lambda acc: props,
                           childOccurrences=_NamedCollection(list(children)))


class TestFullProps:
    def test_mm_scaling_per_quantity(self):
        # k = cm-per-mm = 0.1: lengths x10, areas x100, volumes x1000, inertia x100; mass (kg) and
        # rotation angles never rescale. A single wrong exponent here misreports every mass read.
        out = mi._full_props(_make_pp(), mi._common.scale("mm"))
        assert out["mass_kg"] == 2.0
        assert out["volume"] == 4000.0 and out["area"] == 600.0
        assert out["center_of_mass"] == [10.0, 20.0, 30.0]
        assert out["inertia_world"]["Ixx"] == 100.0 and out["inertia_world"]["Ixz"] == 60.0
        assert out["principal_moments"]["i1"] == 500.0
        assert out["radius_of_gyration"]["kx"] == 5.0
        assert out["rotation_to_principal_rad"]["rx"] == 0.1
        assert out["principal_axes"]["z"] == [0, 0, 1]

    def test_failed_sub_reads_omit_their_blocks(self):
        # each get*() returns (retVal, ...) - a False retVal means the read failed, so its block is
        # omitted rather than reporting zeros as if measured.
        pp = _make_pp()
        pp.getXYZMomentsOfInertia = lambda: (False, 0, 0, 0, 0, 0, 0)
        pp.getPrincipalMomentsOfInertia = lambda: None
        out = mi._full_props(pp, 0.1)
        assert "inertia_world" not in out and "principal_moments" not in out


class TestPhysicalProperties:
    def test_unknown_units_errors(self):
        res = mi._physical_properties(None, object(), "x", "parsec", "medium", False)
        assert res["isError"] and "parsec" in error_message(res)

    def test_unknown_accuracy_errors(self):
        res = mi._physical_properties(None, object(), "x", "mm", "extreme", False)
        assert res["isError"] and "extreme" in error_message(res)

    def test_no_measurable_solid_errors_naming_the_target(self):
        e = SimpleNamespace(getPhysicalProperties=lambda acc: None)
        res = mi._physical_properties(None, e, "body 'Shell'", "mm", "medium", False)
        assert res["isError"] and "Shell" in error_message(res)

    def test_reports_mass_and_the_accuracy_actually_used(self):
        # accuracy_used is read BACK off the result (the API may compute at a different accuracy
        # than requested) - echoing the request instead would hide that.
        pp = _make_pp(accuracy=mi._ACCURACY["high"])
        e = SimpleNamespace(getPhysicalProperties=lambda acc: pp)
        out = _payload(mi._physical_properties(None, e, "body 'Plate'", "mm", "medium", False))
        assert out["mass_kg"] == 2.0 and out["accuracy"] == "medium"
        assert out["accuracy_used"] == "high"
        assert "per_occurrence" not in out                  # opt-in via per_body

    def test_per_body_breakdown_skips_unmeasurable_occurrences(self):
        opp = SimpleNamespace(mass=1.25, centerOfMass=SimpleNamespace(x=0.1, y=0.0, z=0.0))
        o1 = _occ_row("A:1", pp=opp)
        o2 = _occ_row("B:1", pp=None)                      # surface-only: no physical properties
        e = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                            allOccurrences=_NamedCollection([o1, o2]))
        out = _payload(mi._physical_properties(None, e, "whole design", "mm", "medium", True))
        assert out["per_occurrence_count"] == 1
        # a LEAF row keeps the plain shape - no aggregates_children key to reason about
        assert out["per_occurrence"] == [{"occurrence": "A:1", "mass_kg": 1.25,
                                          "center_of_mass": [1.0, 0.0, 0.0]}]


class TestPerOccurrenceReachesEveryDepth:
    """per_body must name EVERY occurrence in the target's subtree. The direct-children collection
    stops one level down, so a body owned by a nested sub-component gets no row of its own and is
    silently folded into its parent's mass - a breakdown that is short by exactly the deep parts."""

    def _design_with_grandchild(self):
        grand = _occ_row("Frame:1+Motor:1", mass=0.5)
        child = _occ_row("Frame:1", mass=2.0, children=[grand])
        # The root exposes BOTH collections, as live: 'occurrences' is the direct children only,
        # 'allOccurrences' the flattened subtree. Walking the former misses the grandchild.
        return SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                               occurrences=_NamedCollection([child]),
                               allOccurrences=_NamedCollection([child, grand]))

    def _rows(self, entity, target="whole design"):
        out = _payload(mi._physical_properties(None, entity, target, "mm", "medium", True))
        return out, out["per_occurrence"]

    def test_a_grandchild_occurrence_gets_its_own_row(self):
        out, rows = self._rows(self._design_with_grandchild())
        assert [r["occurrence"] for r in rows] == ["Frame:1", "Frame:1+Motor:1"]
        assert out["per_occurrence_count"] == 2
        assert rows[1]["mass_kg"] == 0.5

    def test_rows_are_keyed_by_full_path_not_bare_name(self):
        # two 'Motor:1' under different parents are different parts; the bare name collapses them
        a = _occ_row("Left:1+Motor:1", mass=0.5)
        b = _occ_row("Right:1+Motor:1", mass=0.5)
        e = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                            allOccurrences=_NamedCollection([a, b]))
        _, rows = self._rows(e)
        assert [r["occurrence"] for r in rows] == ["Left:1+Motor:1", "Right:1+Motor:1"]

    def test_a_row_with_children_says_it_aggregates_them(self):
        # the parent's mass ALREADY contains the grandchild's row, so summing the rows double-counts
        _, rows = self._rows(self._design_with_grandchild())
        assert rows[0]["aggregates_children"] is True
        assert "aggregates_children" not in rows[1]

    def test_an_unreadable_child_count_publishes_null_not_leaf(self):
        # null says "unknown", which is the only honest answer; a missing key would claim leaf and
        # invite the caller to sum a row that may already include others.
        deaf = SimpleNamespace(name="Frame:1", fullPathName="Frame:1",
                               getPhysicalProperties=lambda acc: SimpleNamespace(
                                   mass=1.0, centerOfMass=SimpleNamespace(x=0.0, y=0.0, z=0.0)),
                               childOccurrences=SimpleNamespace())     # no readable count
        e = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                            allOccurrences=_NamedCollection([deaf]))
        _, rows = self._rows(e)
        assert rows[0]["aggregates_children"] is None

    def test_an_occurrence_target_walks_its_own_subtree(self):
        # an Occurrence carries no flattened allOccurrences - only childOccurrences - so a
        # sub-assembly target still breaks down to its deepest parts.
        grand = _occ_row("Frame:1+Motor:1+Shaft:1", mass=0.25)
        child = _occ_row("Frame:1+Motor:1", mass=0.5, children=[grand])
        occ = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                              childOccurrences=_NamedCollection([child]))
        _, rows = self._rows(occ, "occurrence 'Frame:1'")
        assert [r["occurrence"] for r in rows] == ["Frame:1+Motor:1", "Frame:1+Motor:1+Shaft:1"]

    def test_a_target_with_no_occurrences_reports_an_empty_breakdown(self):
        e = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp())   # a lone body
        out, rows = self._rows(e, "body 'Plate'")
        assert rows == [] and out["per_occurrence_count"] == 0
        assert out["per_occurrence_truncated"] is False

    def test_the_row_list_is_capped_and_says_so(self, monkeypatch):
        monkeypatch.setattr(mi, "_MAX_PER_OCCURRENCE_ROWS", 3)
        many = [_occ_row(f"P{i}:1") for i in range(5)]
        e = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                            allOccurrences=_NamedCollection(many))
        out, rows = self._rows(e)
        assert len(rows) == 3 and out["per_occurrence_count"] == 3
        assert out["per_occurrence_truncated"] is True
        assert "per_occurrence_truncated" in out["note"]

    def test_a_list_inside_the_cap_is_not_flagged_truncated(self, monkeypatch):
        monkeypatch.setattr(mi, "_MAX_PER_OCCURRENCE_ROWS", 3)
        e = SimpleNamespace(getPhysicalProperties=lambda acc: _make_pp(),
                            allOccurrences=_NamedCollection([_occ_row(f"P{i}:1") for i in range(3)]))
        out, rows = self._rows(e)
        assert len(rows) == 3 and out["per_occurrence_truncated"] is False

    def test_the_note_discloses_that_parent_rows_aggregate(self):
        out, _ = self._rows(self._design_with_grandchild())
        assert "aggregates_children" in out["note"] and "double-count" in out["note"]

    def test_without_per_body_no_breakdown_and_no_extra_note(self):
        out = _payload(mi._physical_properties(None, self._design_with_grandchild(),
                                               "whole design", "mm", "medium", False))
        assert "per_occurrence" not in out and "per_occurrence_truncated" not in out
        assert "aggregates_children" not in out["note"]


class TestSubtreeOccurrences:
    def test_a_deep_occurrence_chain_is_walked_to_the_bottom(self):
        deep = _occ_row("A:1+B:1+C:1")
        mid = _occ_row("A:1+B:1", children=[deep])
        top = _occ_row("A:1", children=[mid])
        entity = SimpleNamespace(childOccurrences=_NamedCollection([top]))
        assert [o.fullPathName for o in mi._subtree_occurrences(entity, 10)] == [
            "A:1", "A:1+B:1", "A:1+B:1+C:1"]

    def test_the_walk_stops_one_past_the_limit(self):
        # one extra item is what lets the caller flag truncation without counting a total it
        # never walked; an unbounded walk would enumerate a whole assembly to publish 200 rows.
        chain = [_occ_row(f"A{i}:1") for i in range(10)]
        entity = SimpleNamespace(childOccurrences=_NamedCollection(chain))
        assert len(mi._subtree_occurrences(entity, 4)) == 5

    def test_an_entity_with_neither_collection_walks_nothing(self):
        assert mi._subtree_occurrences(SimpleNamespace(), 10) == []


class TestRouterErrorPropagation:
    def test_mass_slice_error_fails_the_read(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        monkeypatch.setattr(mi, "_physical_properties",
                            lambda *a: mi.error("no measurable solid"))
        res = mi.handler(target="Body1", include=["mass"])
        assert res["isError"] and "no measurable solid" in error_message(res)

    def test_bbox_error_fails_the_read(self, monkeypatch, stub_slices):
        _resolve_to(monkeypatch, "body")
        monkeypatch.setattr(mi, "_bbox", lambda *a: mi.error("no bounding box"))
        res = mi.handler(target="Body1")
        assert res["isError"] and "no bounding box" in error_message(res)


class TestVecHelpers:
    def test_none_vectors_stay_none(self):
        assert mi._vec(None) is None and mi._vecxyz(None) is None

    def test_vec_scales_components(self):
        assert mi._vec(SimpleNamespace(x=1.0, y=2.0, z=3.0), 10.0) == [10.0, 20.0, 30.0]
