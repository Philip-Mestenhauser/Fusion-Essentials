"""Unit tests for ``arrange.py`` — pack shapes within a sketch-profile boundary (Arrange feature).

The Arrange feature nests component occurrences inside a 2D envelope (a sketch profile / planar
face). Pinned here without a live Fusion: solver-type resolution (true_shape/rectangular), boundary
resolution (a named sketch -> its profile), shape resolution (occurrence names -> occurrences and
into ArrangeComponents.add), spacing -> cm, and the orchestration (createInput -> setProfileOrFace
Envelope -> add each component -> add feature). The actual solve is a live side-effect.
"""

import json

from conftest import load_tool

ar = load_tool("model_arrange")


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeProfiles:
    def __init__(self, n=1):
        self._n = n
    @property
    def count(self):
        return self._n
    def item(self, i):
        return ("profile", i)


class FakeSketch:
    def __init__(self, name, profile_count=1):
        self.name = name
        self.profiles = FakeProfiles(profile_count)


class FakeSketches:
    def __init__(self, sketches):
        self._s = list(sketches)
    @property
    def count(self):
        return len(self._s)
    def item(self, i):
        return self._s[i]
    def itemByName(self, name):
        for s in self._s:
            if s.name == name:
                return s
        return None


class FakeOcc:
    def __init__(self, name):
        self.name = name


class FakeArrangeComponents:
    def __init__(self):
        self.added = []
    def add(self, occ_or_face):
        self.added.append(occ_or_face)
        return ("ac", occ_or_face)


class FakeEnvelope:
    def __init__(self, profiles):
        self.profiles = list(profiles)
        self.objectSpacing = None


class FakeArrangeInput:
    def __init__(self, solver):
        self.solver = solver
        self.envelope = None          # the FakeEnvelope returned by setProfileOrFaceEnvelope
        self.arrangeComponents = FakeArrangeComponents()
    def setProfileOrFaceEnvelope(self, profiles_or_faces):
        self.envelope = FakeEnvelope(profiles_or_faces)
        return self.envelope


class FakeArrangeFeatures:
    def __init__(self):
        self.last_input = None
        self.added = False
    def createInput(self, solver):
        self.last_input = FakeArrangeInput(solver)
        return self.last_input
    def add(self, inp):
        self.added = True
        return type("F", (), {"name": "Arrange1"})()


class FakeRoot:
    def __init__(self, sketches, occurrences, af):
        self.sketches = FakeSketches(sketches)
        self.allOccurrences = list(occurrences)
        self.features = type("Feats", (), {"arrangeFeatures": af})()


class FakeDesign:
    def __init__(self, sketches, occurrences, af):
        self.rootComponent = FakeRoot(sketches, occurrences, af)


def _install(sketches=(), occ_names=()):
    af = FakeArrangeFeatures()
    design = FakeDesign(list(sketches), [FakeOcc(n) for n in occ_names], af)
    ar.app = type("A", (), {"activeProduct": design})()
    ar._common.app = ar.app
    import adsk.fusion, adsk.core
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    ST = adsk.fusion.ArrangeSolverTypes
    ST.Arrange2DTrueShapeSolverType = "TRUE"
    ST.Arrange2DRectangularSolverType = "RECT"
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: ("real", v))
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))
    return design, af


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── solver type ──────────────────────────────────────────────────────────────

class TestSolverType:
    def test_true_shape_default(self):
        _, af = _install([FakeSketch("Boundary")], ["A:1"])
        _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1"))
        assert af.last_input.solver == "TRUE"

    def test_rectangular(self):
        _, af = _install([FakeSketch("Boundary")], ["A:1"])
        _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1", solver="rectangular"))
        assert af.last_input.solver == "RECT"

    def test_unknown_solver_errors(self):
        _install([FakeSketch("Boundary")], ["A:1"])
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1", solver="hexagonal")
        assert res["isError"] is True and "solver" in res["message"].lower()

    def test_rect_alias_resolves_to_rectangular(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", solver="rect"))
        assert af.last_input.solver == "RECT"
        # payload's solver field is normalized off the resolved solver class name
        assert out["solver"] == "rectangular"

    def test_true_alias_normalizes_in_payload(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", solver="trueshape"))
        assert af.last_input.solver == "TRUE"
        assert out["solver"] == "true_shape"

    def test_solver_case_insensitive(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        _payload(ar.handler(boundary_sketch="B", shapes="A:1", solver="RECTANGULAR"))
        assert af.last_input.solver == "RECT"


# ── boundary resolution ──────────────────────────────────────────────────────

class TestBoundary:
    def test_named_sketch_profile_used_as_envelope(self):
        _, af = _install([FakeSketch("Boundary")], ["A:1"])
        _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1"))
        assert af.last_input.envelope.profiles == [("profile", 0)]

    def test_missing_boundary_errors(self):
        _install([FakeSketch("Other")], ["A:1"])
        res = ar.handler(boundary_sketch="Nope", shapes="A:1")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_boundary_with_no_profile_errors(self):
        _install([FakeSketch("Empty", profile_count=0)], ["A:1"])
        res = ar.handler(boundary_sketch="Empty", shapes="A:1")
        assert res["isError"] is True and "profile" in res["message"].lower()


# ── shapes ───────────────────────────────────────────────────────────────────

class TestShapes:
    def test_each_shape_added_as_component(self):
        _, af = _install([FakeSketch("B")], ["A:1", "B:1", "C:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1, B:1, C:1"))
        assert len(af.last_input.arrangeComponents.added) == 3
        assert out["arranged_count"] == 3

    def test_missing_shape_reported(self):
        _install([FakeSketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1, Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_no_shapes_errors(self):
        _install([FakeSketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="")
        assert res["isError"] is True and "shapes" in res["message"].lower()

    def test_feature_created(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert af.added is True
        assert out["arranged"] is True


# ── spacing ──────────────────────────────────────────────────────────────────

class TestSpacing:
    def test_spacing_scaled_to_cm(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=5, units="mm"))
        # objectSpacing set on the ENVELOPE input as a cm ValueInput (5mm -> 0.5cm)
        assert af.last_input.envelope.objectSpacing == ("real", 0.5)

    def test_spacing_inches_scaled_to_cm(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=2, units="in"))
        # 2in -> 5.08cm
        assert af.last_input.envelope.objectSpacing == ("real", 5.08)
        assert out["spacing"] == 2.0
        assert out["units"] == "in"

    def test_zero_spacing_not_set_and_reported_zero(self):
        _, af = _install([FakeSketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=0))
        # falsy spacing skips the setattr branch -> envelope keeps its default
        assert af.last_input.envelope.objectSpacing is None
        assert out["spacing"] == 0.0

    def test_unknown_units_errors(self):
        _install([FakeSketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", units="furlong")
        assert res["isError"] is True and "units" in res["message"].lower()
