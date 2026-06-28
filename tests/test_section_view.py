"""Unit tests for ``view_section.py`` — the Section Analysis cutaway tool.

The branches that matter and could silently misbehave: action validation, plane
aliasing (top/front/right -> xy/xz/yz), the ``through``-occurrence path that
converts a bbox center into a section distance along the chosen plane's normal
(mm->cm, per-plane coordinate pick), ``flip``/``show_hatch`` propagation into the
SectionAnalysisInput, and list/clear. The createInput call is captured on a fake
``sectionAnalyses`` so we can assert the distance/flags handed to Fusion without
a live session. ``auto_view`` camera aiming is a viewport side-effect (no pure
logic) — left to live testing; we keep auto_view=false here so handler() returns
without touching a camera.
"""

import json

from conftest import load_tool

sv = load_tool("section_view")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakePoint:
    def __init__(self, x, y, z):
        self.x, self.y, self.z = x, y, z


class FakeBBox:
    def __init__(self, minp, maxp):
        self.minPoint = FakePoint(*minp)
        self.maxPoint = FakePoint(*maxp)


class FakeOcc:
    def __init__(self, name, bbox=None, full_path=None):
        self.name = name
        self.fullPathName = full_path or name
        self.boundingBox = bbox


class FakeSectionInput:
    def __init__(self, entity, distance_cm):
        self.entity = entity
        self.distance_cm = distance_cm
        self.flip = False
        self.isHatchShown = True


class FakeSection:
    def __init__(self, name):
        self.name = name
        self.isLightBulbOn = True
        self._deleted = False

    def deleteMe(self):
        self._deleted = True
        return True


class FakeSectionAnalyses:
    def __init__(self, existing=()):
        self._items = list(existing)
        self.last_input = None
        self.add_calls = 0

    @property
    def count(self):
        return len(self._items)

    def item(self, i):
        return self._items[i]

    def createInput(self, entity, distance_cm):
        self.last_input = FakeSectionInput(entity, distance_cm)
        return self.last_input

    def add(self, inp):
        self.add_calls += 1
        sec = FakeSection(f"Section{self.add_calls}")
        self._items.append(sec)
        return sec


class FakeRoot:
    """Exposes the origin construction planes by attribute name + occurrences."""
    def __init__(self, occurrences=()):
        self.xYConstructionPlane = "PLANE_XY"
        self.xZConstructionPlane = "PLANE_XZ"
        self.yZConstructionPlane = "PLANE_YZ"
        self.allOccurrences = list(occurrences)


class FakeAnalyses:
    def __init__(self, sections):
        self.sectionAnalyses = sections


class FakeDesign:
    def __init__(self, root, sections):
        self.rootComponent = root
        self.analyses = FakeAnalyses(sections)


class FakeViewport:
    def refresh(self):
        pass


class FakeApp:
    def __init__(self, design):
        self._design = design
        self.activeProduct = design
        self.activeViewport = FakeViewport()


def _install(occurrences=(), existing_sections=()):
    sections = FakeSectionAnalyses(existing_sections)
    root = FakeRoot(occurrences)
    design = FakeDesign(root, sections)
    sv.app = FakeApp(design)
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    # the bare-plane path uses PlaneRef, which resolves via _common.design()/target_component()
    # (the app-reference seam) — point them at the fake root so an origin alias resolves.
    sv._inputs._common.design = lambda: design
    sv._inputs._common.target_component = lambda d: root
    return sections


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── guards / validation ─────────────────────────────────────────────────────

class TestGuards:
    def test_unknown_action(self):
        _install()
        res = sv.handler(action="slice")
        assert res["isError"] is True and "Unknown action" in res["message"]

    def test_no_design(self):
        sv.app = FakeApp(None)
        import adsk.fusion
        adsk.fusion.Design.cast = lambda x: None
        res = sv.handler(action="cut", plane="xy")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_cut_requires_plane_or_through(self):
        _install()
        res = sv.handler(action="cut")
        assert res["isError"] is True
        assert "Provide 'plane'" in res["message"]

    def test_through_unknown_occurrence(self):
        _install(occurrences=[FakeOcc("Vise")])
        res = sv.handler(action="cut", through="Nonexistent")
        assert res["isError"] is True
        assert "No occurrence matched" in res["message"]


# ── plain plane cut ─────────────────────────────────────────────────────────

class TestPlaneCut:
    def test_xy_plane_uses_xy_construction_plane_at_zero(self):
        sections = _install()
        out = _payload(sv.handler(action="cut", plane="xy", auto_view=False))
        assert out["action"] == "cut"
        assert sections.last_input.entity == "PLANE_XY"
        assert sections.last_input.distance_cm == 0.0

    def test_alias_front_maps_to_xz(self):
        sections = _install()
        _payload(sv.handler(action="cut", plane="front", auto_view=False))
        assert sections.last_input.entity == "PLANE_XZ"

    def test_offset_mm_converted_to_cm(self):
        sections = _install()
        _payload(sv.handler(action="cut", plane="xy", offset=10.0, auto_view=False))
        assert sections.last_input.distance_cm == 1.0   # 10 mm -> 1 cm

    def test_flip_and_hatch_propagate(self):
        sections = _install()
        out = _payload(sv.handler(action="cut", plane="yz", flip=True,
                                  show_hatch=False, auto_view=False))
        assert sections.last_input.flip is True
        assert sections.last_input.isHatchShown is False
        assert out["flipped"] is True


# ── through-occurrence center math ──────────────────────────────────────────

class TestThroughCenter:
    def test_xy_uses_z_center(self):
        # bbox z spans 2..4 cm -> center cz = 3 cm; xy normal is Z.
        occ = FakeOcc("Part", bbox=FakeBBox((0, 0, 2), (6, 8, 4)))
        sections = _install(occurrences=[occ])
        _payload(sv.handler(action="cut", through="Part", plane="xy", auto_view=False))
        assert sections.last_input.distance_cm == 3.0

    def test_front_uses_y_center(self):
        # y spans 1..5 -> cy = 3; front/xz normal is Y.
        occ = FakeOcc("Part", bbox=FakeBBox((0, 1, 0), (6, 5, 4)))
        sections = _install(occurrences=[occ])
        _payload(sv.handler(action="cut", through="Part", plane="front", auto_view=False))
        assert sections.last_input.distance_cm == 3.0

    def test_through_adds_explicit_offset_on_top_of_center(self):
        # cy = 3 cm, plus 20 mm (=2 cm) offset -> 5 cm.
        occ = FakeOcc("Part", bbox=FakeBBox((0, 1, 0), (6, 5, 4)))
        sections = _install(occurrences=[occ])
        _payload(sv.handler(action="cut", through="Part", plane="front",
                            offset=20.0, auto_view=False))
        assert sections.last_input.distance_cm == 5.0

    def test_through_defaults_to_xz_when_no_plane(self):
        occ = FakeOcc("Part", bbox=FakeBBox((0, 1, 0), (6, 5, 4)))
        sections = _install(occurrences=[occ])
        out = _payload(sv.handler(action="cut", through="Part", auto_view=False))
        assert sections.last_input.entity == "PLANE_XZ"
        assert "xz plane" in out["where"]

    def test_through_substring_match(self):
        occ = FakeOcc("Carrier Body:1", bbox=FakeBBox((0, 0, 0), (2, 2, 2)))
        sections = _install(occurrences=[occ])
        out = _payload(sv.handler(action="cut", through="carrier", plane="xy", auto_view=False))
        assert "Carrier Body:1" in out["where"]


# ── list / clear ─────────────────────────────────────────────────────────────

class TestListClear:
    def test_list_reports_sections(self):
        _install(existing_sections=[FakeSection("Section1"), FakeSection("Section2")])
        out = _payload(sv.handler(action="list"))
        assert out["count"] == 2
        assert {s["name"] for s in out["sections"]} == {"Section1", "Section2"}

    def test_clear_removes_all(self):
        s1, s2 = FakeSection("Section1"), FakeSection("Section2")
        _install(existing_sections=[s1, s2])
        out = _payload(sv.handler(action="clear"))
        assert out["removed_count"] == 2
        assert s1._deleted and s2._deleted
