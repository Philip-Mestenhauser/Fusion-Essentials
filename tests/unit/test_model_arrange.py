"""Unit tests for ``model_arrange.py`` — pack shapes into an envelope (Arrange feature).

The Arrange feature nests component occurrences into a 2D envelope (a sketch profile / planar face,
or a plane sized in 'units') or a 3D box envelope. Pinned here without a live Fusion: solver-type
resolution (true_shape/rectangular/3d), envelope resolution and the one-form-per-call refusals,
shape resolution (occurrence names -> ArrangeComponents.add), the sizes and settings written onto
the envelope/definition inputs in cm, and the read-backs the payload publishes (result envelopes,
arrangeStatistics). The actual solve is a live side-effect.
"""

import json
from types import SimpleNamespace

import adsk.core
import adsk.fusion
import pytest

from conftest import (
    BRepBody, BRepFace, MakeComp, _NamedCollection, assert_no_active_design, install, load_tool,
    make_design, make_occurrence, make_sketch, payload as _payload,
)

ar = load_tool("model_arrange")

# Measured solver enum (seeded from live_api_facts) - fakes and assertions speak these.
_TRUE = adsk.fusion.ArrangeSolverTypes.Arrange2DTrueShapeSolverType
_RECT = adsk.fusion.ArrangeSolverTypes.Arrange2DRectangularSolverType
_3D = adsk.fusion.ArrangeSolverTypes.Arrange3DSolverType


# ── fakes ───────────────────────────────────────────────────────────────────

def _sketch(name, tag="profile", profile_count=1, compute_deferred=False):
    """A boundary sketch whose profiles say WHICH sketch they came from."""
    return make_sketch(name=name, is_compute_deferred=compute_deferred,
                       profiles=[(tag, i) for i in range(profile_count)])


def _vec(x=0.0, y=0.0, z=0.0):
    return SimpleNamespace(x=x, y=y, z=z)


def _rv(value):
    """What the patched ValueInput.createByReal hands back - the tool reads .realValue off it."""
    return SimpleNamespace(realValue=value)


def _stats_json(arranged, unarranged):
    """arrangeStatistics in the measured 3D shape: {name, statistics: {label: {value}}}, cm units."""
    return json.dumps({"name": "Arrange1", "statistics": {
        "Components Arranged": {"localizedName": "Components Arranged", "value": arranged},
        "Components Unarranged": {"localizedName": "Components Unarranged", "value": unarranged},
        "Components Volume": {"localizedName": "Components Volume", "value": 16},
    }})


def _both_stats_json(total_arranged, unarranged, *per_envelope):
    """Both measured shapes at once: the top-level TOTAL map plus its per-envelope breakdown."""
    data = json.loads(_sheet_stats_json(*per_envelope))
    data["name"] = "Arrange1"
    data["statistics"] = {
        "Components Arranged": {"localizedName": "Components Arranged", "value": total_arranged},
        "Components Unarranged": {"localizedName": "Components Unarranged", "value": unarranged},
        "Envelopes Used": {"localizedName": "Envelopes Used", "value": len(per_envelope)},
    }
    return json.dumps(data)


def _sheet_stats_json(*per_envelope):
    """arrangeStatistics in the measured 2D PLANE shape: one statistics object per envelope, and no
    'Components Unarranged' among them."""
    return json.dumps({"envelopes": [
        {"name": f"Envelope{i}", "statistics": {
            "Actual Efficiency": {"localizedName": "Actual Efficiency", "value": 0.0467},
            "Components Arranged": {"localizedName": "Components Arranged", "value": arranged},
            "Envelope Area": {"localizedName": "Envelope Area", "value": 600},
            "Envelopes Quantity": {"localizedName": "Envelopes Quantity", "value": 1},
        }} for i, arranged in enumerate(per_envelope, start=1)]})


def _occ(path, ground_to_parent=None):
    return make_occurrence(path=path, component=SimpleNamespace(name=path.split(":")[0]),
                           transform2=SimpleNamespace(translation=_vec()),
                           ground_to_parent=ground_to_parent)


class FakeArrangeComponents:
    def __init__(self):
        self.added = []
    def add(self, occ_or_face):
        self.added.append(occ_or_face)
        return ("ac", occ_or_face)


class FakeArrangeDefinition:
    """ArrangeFeatureInput.definition - no live shape dump for this type, so it is a local double.
    Its four members start on the platform's own measured defaults."""
    def __init__(self):
        self.isCreateCopies = True
        self.globalRotation = adsk.fusion.ArrangeRotationTypes.AllRotationsArrangeRotationType
        self.globalQuantity = _rv(1.0)
        self.isPartInPartAllowed = True


class FakeEnvelope:
    """The envelope input a set*Envelope call returns - a local double; no live shape dump. The
    PROFILE form carries every member but the two origin offsets."""
    def __init__(self, kind, profiles=(), plane=None, sizes=()):
        self.kind = kind
        self.profiles = list(profiles)
        self.plane = plane
        self.sizes = tuple(sizes)
        self.objectSpacing = None
        self.frameWidth = None
        self.placementClearance = None
        self.isPartialArrangeAllowed = False
        if kind == "3d":
            self.ceilingClearance = None
        if kind != "profile":
            self.originXOffset = None
            self.originYOffset = None


class FakeResultEnvelope:
    """One ArrangeFeature.resultEnvelopes row - a local double; no live shape dump. Its box is the
    envelope's own SIZE, and it comes in the three measured shapes: a BoundingBox2D of Point2Ds
    (reading .z raises) for a plane envelope, a BoundingBox3D for a 3D one, and NO box at all for a
    profile envelope."""
    _BOXES = {"plane": (SimpleNamespace(x=0.0, y=0.0), SimpleNamespace(x=30.0, y=20.0)),
              "3d": (_vec(), _vec(20.0, 20.0, 10.0))}

    def __init__(self, name, occurrence_count, kind="3d"):
        self.name = name
        self.occurrences = _NamedCollection([None] * occurrence_count)
        box = self._BOXES.get(kind)
        if box is not None:
            self.boundingBox = SimpleNamespace(minPoint=box[0], maxPoint=box[1])


class FakeArrangeFeature:
    """The feature add() hands back - a local double; no live shape dump. An empty
    `statistics` models the '' the API returns when the statistics are unavailable."""
    def __init__(self, statistics="", envelopes=(), kind="3d"):
        self.name = "Arrange1"
        self.arrangeStatistics = statistics
        self.resultEnvelopes = _NamedCollection(
            [FakeResultEnvelope(n, c, kind) for n, c in envelopes])
        self.deleted = 0
    def deleteMe(self):
        self.deleted += 1
        return True


class FakeArrangeInput:
    def __init__(self, solver):
        self.solver = solver
        self.envelope = None          # the FakeEnvelope the envelope setter returned
        self.definition = FakeArrangeDefinition()
        self.arrangeComponents = FakeArrangeComponents()
    def setProfileOrFaceEnvelope(self, profiles_or_faces):
        self.envelope = FakeEnvelope("profile", profiles=profiles_or_faces)
        return self.envelope
    def setPlaneEnvelope(self, plane, length, width):
        self.envelope = FakeEnvelope("plane", plane=plane, sizes=(length, width))
        return self.envelope
    def set3DEnvelope(self, plane, length, width, height):
        self.envelope = FakeEnvelope("3d", plane=plane, sizes=(length, width, height))
        return self.envelope


class FakeArrangeFeatures:
    """add() imitates the measured solver behavior: the named occurrences stay where they are and
    envelope COPIES land as new occurrences - the effect read the handler verifies against.
    `unarranged` leaves that many of the added components out, as the statistics report it."""
    def __init__(self):
        self.last_input = None
        self.added = False
        self.design = None
        self.unarranged = 0
        self.statistics = None        # None = built from the input; "" = the empty read
        self.envelope_rows = None     # None = one envelope holding everything placed
    def createInput(self, solver):
        self.last_input = FakeArrangeInput(solver)
        return self.last_input
    def add(self, inp):
        self.added = True
        placed = len(inp.arrangeComponents.added) - self.unarranged
        if self.design is not None:
            for shape in inp.arrangeComponents.added[:placed]:
                nm = getattr(shape, "name", "X")
                self.design.rootComponent.allOccurrences.append(
                    _occ(f"Arrange1:1+Envelope1(Qty: 1):1+{nm}"))
        stats = _stats_json(placed, self.unarranged) if self.statistics is None else self.statistics
        rows = [("Envelope1", placed)] if self.envelope_rows is None else self.envelope_rows
        kind = inp.envelope.kind if inp.envelope is not None else "3d"
        return FakeArrangeFeature(statistics=stats, envelopes=rows, kind=kind)


def _component(name, sketches, occurrences=(), af=None):
    """A component holding `sketches`, its three origin planes, and the arrangeFeatures collection
    when it is the root."""
    comp = MakeComp(name=name, sketches=list(sketches), occurrences=list(occurrences),
                    origin_planes=(SimpleNamespace(name="XY"), SimpleNamespace(name="XZ"),
                                   SimpleNamespace(name="YZ")))
    if af is not None:
        comp.features = SimpleNamespace(arrangeFeatures=af)
    return comp


def _wire(design, af):
    """Point the tool at `design` (both seams) and model the ValueInput factories it calls."""
    af.design = design
    install(ar, design)
    adsk.core.ValueInput.createByReal = staticmethod(_rv)
    adsk.core.ValueInput.createByString = staticmethod(lambda s: ("str", s))
    return design, af


def _install(sketches=(), occ_names=(), tokens=None):
    af = FakeArrangeFeatures()
    root = _component("Root", sketches, [_occ(n) for n in occ_names], af)
    return _wire(make_design(comp=root, tokens=tokens), af)


@pytest.fixture(autouse=True)
def _fusion_types(monkeypatch):
    """The adsk.fusion type identity the envelope-plane guard branches on - a Mock is not a type."""
    monkeypatch.setattr(adsk.fusion, "BRepFace", BRepFace, raising=False)


def _envelope_input(envelope_cls):
    """A createInput building its profile envelope from `envelope_cls` - the seam a test models an
    envelope property that refuses the write, or the read back, with."""
    class _Input(FakeArrangeInput):
        def setProfileOrFaceEnvelope(self, profiles_or_faces):
            self.envelope = envelope_cls("profile", profiles=profiles_or_faces)
            return self.envelope
    return lambda solver: _Input(solver)


# ── solver type ──────────────────────────────────────────────────────────────

class TestSolverType:
    def test_true_shape_default(self):
        _, af = _install([_sketch("Boundary")], ["A:1"])
        _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1"))
        assert af.last_input.solver == _TRUE

    def test_rectangular(self):
        _, af = _install([_sketch("Boundary")], ["A:1"])
        _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1", solver="rectangular"))
        assert af.last_input.solver == _RECT

    def test_unknown_solver_errors(self):
        _install([_sketch("Boundary")], ["A:1"])
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1", solver="hexagonal")
        assert res["isError"] is True and "solver" in res["message"].lower()

    def test_rect_alias_resolves_to_rectangular(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", solver="rect"))
        assert af.last_input.solver == _RECT
        # payload's solver field is normalized off the resolved solver class name
        assert out["solver"] == "rectangular"

    def test_true_alias_normalizes_in_payload(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", solver="trueshape"))
        assert af.last_input.solver == _TRUE
        assert out["solver"] == "true_shape"

    def test_solver_case_insensitive(self):
        _, af = _install([_sketch("B")], ["A:1"])
        _payload(ar.handler(boundary_sketch="B", shapes="A:1", solver="RECTANGULAR"))
        assert af.last_input.solver == _RECT


# ── boundary resolution ──────────────────────────────────────────────────────

class TestBoundary:
    def test_named_sketch_profile_used_as_envelope(self):
        _, af = _install([_sketch("Boundary")], ["A:1"])
        _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1"))
        assert af.last_input.envelope.profiles == [("profile", 0)]

    def test_a_deferred_boundary_sketch_is_refused(self):
        # profiles.item(0) is a blind index off the sketch's own collection, so a deferred sketch
        # would hand the envelope whichever region was first before the deferral.
        _install([_sketch("Boundary", profile_count=2, compute_deferred=True)], ["A:1"])
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1")
        assert res["isError"] is True
        assert "isComputeDeferred=true" in res["message"] and "'Boundary'" in res["message"]
        assert "'boundary_sketch'" in res["message"]

    def test_missing_boundary_errors(self):
        _install([_sketch("Other")], ["A:1"])
        res = ar.handler(boundary_sketch="Nope", shapes="A:1")
        assert res["isError"] is True and "Nope" in res["message"]

    def test_a_shared_boundary_sketch_name_is_refused_with_its_owners(self, monkeypatch):
        # Two components can each hold a "Boundary". The refusal names them and no arrange feature
        # is built; calling it "No sketch named 'Boundary'" says the opposite of what the walk read.
        _design, af = _install([_sketch("Boundary")], ["A:1"])
        refusal = "2 sketches are named 'Boundary' ('Boundary' in Root, 'Boundary' in Frame)"
        monkeypatch.setattr(ar._common, "find_sketch",
                            lambda design, name, remedy=None: (None, refusal))
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1")
        assert res["isError"] is True
        assert res["message"] == refusal and "No sketch named" not in res["message"]
        assert af.last_input is None and af.added is False     # nothing was created

    def test_boundary_with_no_profile_errors(self):
        _install([_sketch("Empty", profile_count=0)], ["A:1"])
        res = ar.handler(boundary_sketch="Empty", shapes="A:1")
        assert res["isError"] is True and "profile" in res["message"].lower()


# ── the 'boundary_component' SCOPE ───────────────────────────────────────────
# Fusion numbers sketches per component from 1, so two components each holding a "Boundary" is the
# norm. The design-wide walk REFUSES that name and points at this input: a rename is no remedy for
# a component that arrived inside a referenced document. The scope is spelled for the BOUNDARY
# because that is the only sketch this tool resolves by name.

@pytest.fixture
def multi():
    """Root plus further named components, each with its OWN sketches. allComponents lives on the
    DESIGN, which is the collection the shared by-name walk asks."""
    def _do(pairs, occ_names=("A:1",), extra_components=()):
        af = FakeArrangeFeatures()
        occs = [_occ(n) for n in occ_names]
        comps = [_component(pairs[0][0], pairs[0][1], occs, af)]
        comps += [_component(n, s) for n, s in pairs[1:]]
        design = make_design(comp=comps[0], all_components=comps + list(extra_components))
        return _wire(design, af)
    return _do


class TestBoundaryComponentScope:
    def _shared(self, multi):
        """ONE name across TWO components, with different profile counts (Alpha 2, Beta 1) so the
        envelope that answered is readable rather than assumed from a shared name."""
        alpha_sk = _sketch("Boundary", tag="alpha", profile_count=2)
        beta_sk = _sketch("Boundary", tag="beta", profile_count=1)
        design, af = multi([("Alpha", [alpha_sk]), ("Beta", [beta_sk])])
        return design, af, alpha_sk, beta_sk

    def test_the_unscoped_shared_name_refuses_and_names_the_scope_input(self, multi):
        _design, af, _a, _b = self._shared(multi)
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1")
        assert res["isError"] is True
        assert "2 sketches are named 'Boundary'" in res["message"]
        assert "'boundary_component'" in res["message"] and "Rename one" not in res["message"]
        assert af.last_input is None and af.added is False

    def test_the_scope_uses_THAT_components_boundary_profile(self, multi):
        _design, af, alpha_sk, beta_sk = self._shared(multi)
        _payload(ar.handler(boundary_sketch="Boundary", boundary_component="Beta", shapes="A:1"))
        assert af.last_input.envelope.profiles == [("beta", 0)]

    def test_the_sibling_component_is_reachable_by_the_same_call(self, multi):
        _design, af, alpha_sk, _b = self._shared(multi)
        _payload(ar.handler(boundary_sketch="Boundary", boundary_component="Alpha", shapes="A:1"))
        assert af.last_input.envelope.profiles == [("alpha", 0)]

    def test_an_unknown_component_is_refused_before_the_arrange(self, multi):
        _design, af, _a, _b = self._shared(multi)
        res = ar.handler(boundary_sketch="Boundary", boundary_component="Gamma", shapes="A:1")
        assert res["isError"] is True and "No component named 'Gamma'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_a_wrong_component_is_refused_even_when_the_name_is_UNIQUE(self, multi):
        # The scope is VALIDATED: a dropped one nests against Alpha's envelope on a call that
        # named Beta, and nothing tells the caller which boundary was used.
        _design, af = multi([("Alpha", [_sketch("OnlyOne")]), ("Beta", [])])
        res = ar.handler(boundary_sketch="OnlyOne", boundary_component="Beta", shapes="A:1")
        assert res["isError"] is True and "'Beta'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_the_scoped_MISS_names_boundary_component_never_the_bare_component(self, multi):
        # This tool declares a STRICT schema and carries NO 'component' input, so a refusal naming
        # 'component' hands the caller a retry its own schema rejects.
        _design, af = multi([("Alpha", [_sketch("Boundary")]), ("Beta", [_sketch("Other")])])
        res = ar.handler(boundary_sketch="Boundary", boundary_component="Beta", shapes="A:1")
        assert res["isError"] is True
        assert "holds no sketch named 'Boundary'" in res["message"]
        assert "'boundary_component'" in res["message"]
        assert "'component'" not in res["message"]
        assert af.last_input is None and af.added is False

    def test_an_AMBIGUOUS_boundary_component_names_boundary_component(self, multi):
        # The occurrence-path remedy names the input this tool actually accepts.
        a = _component("Frame", [_sketch("Boundary")])
        b = _component("Frame", [_sketch("Boundary")])
        design, af = multi([("Root", [])], extra_components=(a, b))
        design.rootComponent.allOccurrences += [
            make_occurrence(path="P2-Gimbal:1+Frame:1", component=a),
            make_occurrence(path="P3-Gimbal:1+Frame:1", component=b)]
        res = ar.handler(boundary_sketch="Boundary", boundary_component="Frame", shapes="A:1")
        assert res["isError"] is True
        assert "2 components match 'Frame'" in res["message"]
        assert "'boundary_component' also takes an occurrence fullPathName" in res["message"]
        assert "'component'" not in res["message"]
        assert af.last_input is None and af.added is False


# ── shapes ───────────────────────────────────────────────────────────────────

class TestShapes:
    def test_each_shape_added_as_component(self):
        _, af = _install([_sketch("B")], ["A:1", "B:1", "C:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1, B:1, C:1"))
        assert len(af.last_input.arrangeComponents.added) == 3
        assert out["arranged_count"] == 3

    def test_missing_shape_reported(self):
        _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1, Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_no_shapes_errors(self):
        _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="")
        assert res["isError"] is True and "shapes" in res["message"].lower()

    def test_feature_created(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert af.added is True
        assert out["arranged"] is True


# ── spacing ──────────────────────────────────────────────────────────────────

class TestSpacing:
    def test_spacing_scaled_to_cm(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=5, units="mm"))
        # objectSpacing set on the ENVELOPE input as a cm ValueInput (5mm -> 0.5cm)
        assert af.last_input.envelope.objectSpacing == _rv(0.5)
        # and PUBLISHED from the read-back off that input, in the caller's units
        assert out["settings"]["spacing"] == 5.0

    def test_spacing_inches_scaled_to_cm(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=2, units="in"))
        # 2in -> 5.08cm
        assert af.last_input.envelope.objectSpacing == _rv(5.08)
        assert out["spacing"] == 2.0
        assert out["units"] == "in"
        assert out["settings"]["spacing"] == 2.0

    def test_a_spacing_past_six_cm_decimals_is_accepted_at_the_read_back_rounding(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=0.015625,
                                  units="in"))
        assert out["settings"]["spacing"] == 0.015625

    def test_a_spacing_that_does_not_read_back_is_an_error(self):
        # A dropped set is silent and leaves the platform default in place, so an ok whose
        # 'settings' cannot show the number would report a spacing that never landed.
        _, af = _install([_sketch("B")], ["A:1"])

        class _WriteOnlyEnvelope(FakeEnvelope):
            """An envelope whose objectSpacing cannot be read back after the write."""
            @property
            def objectSpacing(self):
                raise AttributeError("objectSpacing is write-only on this API version")
            @objectSpacing.setter
            def objectSpacing(self, v):
                self._written = v

        af.createInput = _envelope_input(_WriteOnlyEnvelope)
        res = ar.handler(boundary_sketch="B", shapes="A:1", spacing=5)
        assert res["isError"] is True
        assert "'spacing' did not take" in res["message"]
        assert "reads back nothing, not 5.0 mm" in res["message"]

    def test_a_spacing_the_platform_keeps_at_its_default_is_an_error(self):
        # The harder case: the write is ACCEPTED and the property answers its own default, which is
        # what a silently dropped set looks like from here.
        _, af = _install([_sketch("B")], ["A:1"])

        class _StubbornEnvelope(FakeEnvelope):
            """An envelope whose objectSpacing keeps its default whatever is written."""
            @property
            def objectSpacing(self):
                return _rv(0.3)
            @objectSpacing.setter
            def objectSpacing(self, v):
                self._written = v

        af.createInput = _envelope_input(_StubbornEnvelope)
        res = ar.handler(boundary_sketch="B", shapes="A:1", spacing=5)
        assert res["isError"] is True
        assert "reads back 3.0 mm, not 5.0 mm" in res["message"]

    def test_zero_spacing_not_set_and_reported_zero(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", spacing=0))
        # falsy spacing skips the setattr branch -> envelope keeps its default
        assert af.last_input.envelope.objectSpacing is None
        assert out["spacing"] == 0.0

    def test_unknown_units_errors(self):
        _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", units="furlong")
        assert res["isError"] is True and "units" in res["message"].lower()

    def test_spacing_setter_raise_surfaces_as_error(self):
        # An objectSpacing setter failure must propagate out of the handler (as Arrange failed: ...)
        # - swallowing it would report the spacing as applied when it wasn't.
        class _ReadOnlyEnvelope(FakeEnvelope):
            """An envelope whose objectSpacing refuses the tool's write (its own build still sets
            the default, as a live input arrives carrying one)."""
            def __init__(self, *a, **kw):
                self._refuse = False
                super().__init__(*a, **kw)
                self._refuse = True
            @property
            def objectSpacing(self):
                return self._spacing
            @objectSpacing.setter
            def objectSpacing(self, v):
                if self._refuse:
                    raise AttributeError("objectSpacing is read-only on this API version")
                self._spacing = v

        _, af = _install([_sketch("B")], ["A:1"])
        af.createInput = _envelope_input(_ReadOnlyEnvelope)
        res = ar.handler(boundary_sketch="B", shapes="A:1", spacing=5)
        assert res["isError"] is True
        assert "Arrange failed: objectSpacing is read-only" in res["message"]


# ── the envelope FORMS: exactly one per call ─────────────────────────────────

class TestEnvelopeForms:
    def test_both_envelope_forms_at_once_are_refused(self):
        # Two envelopes is not a merge: one of them would be silently dropped.
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", envelope_plane="xy",
                         envelope_length=300, envelope_width=200)
        assert res["isError"] is True and "exactly ONE envelope" in res["message"]
        assert "boundary_sketch='B'" in res["message"]
        assert "envelope_plane='xy'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_no_envelope_at_all_is_refused(self):
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(shapes="A:1")
        assert res["isError"] is True and "exactly ONE envelope" in res["message"]
        assert af.last_input is None and af.added is False

    def test_the_3d_solver_refuses_a_boundary_sketch(self):
        # The 3D solver takes a 3D envelope; a sketch profile cannot be one.
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", solver="3d")
        assert res["isError"] is True
        assert "solver='3d'" in res["message"] and "'envelope_plane'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_a_height_without_the_3d_solver_is_refused(self):
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", envelope_height=50)
        assert res["isError"] is True
        assert "envelope_height" in res["message"] and "solver='3d'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_a_plane_envelope_missing_a_side_is_refused(self):
        _, af = _install([], ["A:1"])
        res = ar.handler(shapes="A:1", envelope_plane="xy", envelope_length=300)
        assert res["isError"] is True
        assert "envelope_width" in res["message"] and "envelope_length" not in res["message"]
        assert af.last_input is None

    def test_the_3d_solver_needs_a_height(self):
        _, af = _install([], ["A:1"])
        res = ar.handler(shapes="A:1", solver="3d", envelope_plane="xy",
                         envelope_length=200, envelope_width=200)
        assert res["isError"] is True and "envelope_height" in res["message"]
        assert af.last_input is None

    def test_a_planar_face_handle_is_refused_as_the_envelope_plane(self):
        # PlaneRef resolves a planar FACE too; set3DEnvelope/setPlaneEnvelope take a construction
        # plane, so the face is refused by name rather than handed over.
        face = BRepFace(SimpleNamespace(surfaceType=adsk.core.SurfaceTypes.PlaneSurfaceType))
        _, af = _install([], ["A:1"], tokens={"FACE": face})
        res = ar.handler(shapes="A:1", envelope_plane="FACE", envelope_length=300,
                         envelope_width=200)
        assert res["isError"] is True
        assert "planar FACE" in res["message"] and "'envelope_plane'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_boundary_component_with_a_plane_envelope_is_refused(self):
        # It scopes the boundary SKETCH; accepted silently here it would say nothing about the call.
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(shapes="A:1", envelope_plane="xy", envelope_length=300,
                         envelope_width=200, boundary_component="Root")
        assert res["isError"] is True and "'boundary_component'" in res["message"]
        assert af.last_input is None


# ── the plane and 3D envelopes ───────────────────────────────────────────────

class TestPlaneEnvelope:
    def test_the_2d_plane_envelope_is_sized_in_cm(self):
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                                  envelope_length=300, envelope_width=200))
        assert af.last_input.solver == _RECT
        assert af.last_input.envelope.sizes == (_rv(30.0), _rv(20.0))
        assert af.last_input.envelope.plane is not None
        assert out["envelope_plane"] == "XY" and out["boundary_sketch"] is None

    def test_the_3d_envelope_carries_the_height(self):
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100))
        assert af.last_input.solver == _3D
        assert af.last_input.envelope.sizes == (_rv(20.0), _rv(20.0), _rv(10.0))
        assert out["solver"] == "3d"

    def test_the_3d_clearances_are_written_in_cm_and_read_back(self):
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100,
                                  frame_width=5, placement_clearance=2, ceiling_clearance=10))
        env = af.last_input.envelope
        assert (env.frameWidth, env.placementClearance, env.ceilingClearance) == (
            _rv(0.5), _rv(0.2), _rv(1.0))
        assert out["settings"]["frame_width"] == 5.0
        assert out["settings"]["placement_clearance"] == 2.0
        assert out["settings"]["ceiling_clearance"] == 10.0

    def test_partial_is_set_on_the_envelope_input(self):
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100,
                                  partial=True))
        assert af.last_input.envelope.isPartialArrangeAllowed is True
        assert out["settings"]["partial"] is True

    def test_the_2d_plane_envelope_takes_the_frame_and_clearance_knobs_too(self):
        # frameWidth, placementClearance and isPartialArrangeAllowed are members of the 2D PLANE
        # envelope input as well as the 3D one; only ceilingClearance is 3D-only.
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                                  envelope_length=300, envelope_width=200, frame_width=5,
                                  placement_clearance=2, partial=True))
        env = af.last_input.envelope
        assert (env.frameWidth, env.placementClearance) == (_rv(0.5), _rv(0.2))
        assert env.isPartialArrangeAllowed is True
        assert out["settings"]["partial"] is True

    def test_a_ceiling_clearance_stays_3d_only(self):
        _, af = _install([], ["A:1"])
        res = ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                         envelope_length=300, envelope_width=200, ceiling_clearance=10)
        assert res["isError"] is True
        assert "ceiling_clearance" in res["message"] and "solver='3d'" in res["message"]
        assert af.last_input is None

    def test_the_profile_envelope_takes_the_frame_and_clearance_knobs_too(self):
        # frameWidth, placementClearance and isPartialArrangeAllowed are members of the PROFILE
        # envelope input as well; only the two origin offsets are absent there.
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", frame_width=5,
                                  placement_clearance=2, partial=True))
        env = af.last_input.envelope
        assert (env.frameWidth, env.placementClearance) == (_rv(0.5), _rv(0.2))
        assert env.isPartialArrangeAllowed is True
        assert out["settings"]["frame_width"] == 5.0

    def test_an_envelope_origin_is_refused_with_a_profile_envelope(self):
        # The profile envelope input carries no originXOffset/originYOffset at all.
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", envelope_origin=[600, 400])
        assert res["isError"] is True
        assert "'envelope_origin'" in res["message"] and "no origin offsets" in res["message"]
        assert af.last_input is None and af.added is False

    def test_the_envelope_origin_offsets_are_written_in_cm_and_read_back(self):
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                                  envelope_length=300, envelope_width=200,
                                  envelope_origin=[600, 400]))
        env = af.last_input.envelope
        assert (env.originXOffset, env.originYOffset) == (_rv(60.0), _rv(40.0))
        assert out["settings"]["envelope_origin"] == [600.0, 400.0]

    def test_a_malformed_envelope_origin_is_refused(self):
        _, af = _install([], ["A:1"])
        res = ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                         envelope_length=300, envelope_width=200, envelope_origin=[600])
        assert res["isError"] is True and "'envelope_origin'" in res["message"]
        assert af.last_input is None


# ── the arrangement settings on the definition ───────────────────────────────

class TestSettings:
    def test_move_originals_clears_create_copies(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", move_originals=True))
        assert af.last_input.definition.isCreateCopies is False
        assert out["settings"]["create_copies"] is False

    def test_copies_are_left_alone_by_default_and_the_read_is_published(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert af.last_input.definition.isCreateCopies is True
        assert out["settings"]["create_copies"] is True

    def test_rotation_maps_to_its_measured_member(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", rotation="none"))
        assert (af.last_input.definition.globalRotation
                is adsk.fusion.ArrangeRotationTypes.NoneArrangeRotationType)
        assert out["settings"]["rotation"] == "none"

    def test_rotation_with_the_3d_solver_is_refused(self):
        _, af = _install([], ["A:1"])
        res = ar.handler(shapes="A:1", solver="3d", envelope_plane="xy", envelope_length=200,
                         envelope_width=200, envelope_height=100, rotation="all")
        assert res["isError"] is True
        assert "rotation" in res["message"] and "solver='3d'" in res["message"]
        assert af.last_input is None and af.added is False

    def test_quantity_is_written_as_a_whole_number(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", quantity=3))
        assert af.last_input.definition.globalQuantity == _rv(3.0)
        assert out["settings"]["quantity"] == 3.0

    def test_one_is_the_smallest_quantity_the_guard_admits(self):
        # The boundary of the whole-number guard: 1 is a quantity, 0 means the input was omitted.
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", quantity=1))
        assert af.last_input.definition.globalQuantity == _rv(1.0)
        assert out["settings"]["quantity"] == 1.0

    def test_a_fractional_quantity_is_refused(self):
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", quantity=1.5)
        assert res["isError"] is True and "whole number" in res["message"]
        assert af.last_input is None

    def test_part_in_part_outside_true_shape_is_refused(self):
        _, af = _install([_sketch("B")], ["A:1"])
        res = ar.handler(boundary_sketch="B", shapes="A:1", solver="rectangular",
                         part_in_part=True)
        assert res["isError"] is True
        assert "'part_in_part'" in res["message"] and "true_shape" in res["message"]
        assert af.last_input is None and af.added is False

    def test_part_in_part_false_is_written_rather_than_ignored(self):
        # The platform default is TRUE, so false is the value that has to reach the definition; an
        # omitted input leaves the default alone and writes nothing.
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1", part_in_part=False))
        assert af.last_input.definition.isPartInPartAllowed is False
        assert out["settings"]["part_in_part"] is False

    def test_an_omitted_part_in_part_leaves_the_platform_default(self):
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert af.last_input.definition.isPartInPartAllowed is True    # untouched
        assert "part_in_part" not in out["settings"]

    def test_part_in_part_false_with_the_3d_solver_is_refused(self):
        _, af = _install([], ["A:1"])
        res = ar.handler(shapes="A:1", solver="3d", envelope_plane="xy", envelope_length=200,
                         envelope_width=200, envelope_height=100, part_in_part=False)
        assert res["isError"] is True and "part_in_part" in res["message"]
        assert af.last_input is None


# ── honesty: failed/absent mutation must surface as isError, never a false ok ─
# (the paths test_model_mirror.py / test_model_shell.py treat as mandatory)

_SPHERE_ST = adsk.core.SurfaceTypes.SphereSurfaceType
_PLANE_ST = adsk.core.SurfaceTypes.PlaneSurfaceType


def _shape_with(path, *surface_types):
    """One arranged occurrence whose body carries a face of each surface type - what the 2D
    pre-flight walks each shape for."""
    leaf = path.split("+")[-1].split(":")[0]
    body = BRepBody(name=leaf + "Body",
                    faces=[BRepFace(SimpleNamespace(surfaceType=st)) for st in surface_types])
    return make_occurrence(path=path, component=SimpleNamespace(name=leaf),
                           transform2=SimpleNamespace(translation=_vec()), bodies=[body])


def _install_shapes(*occurrences):
    """A root holding `occurrences` plus one boundary sketch - the world a 2D nest is called in."""
    af = FakeArrangeFeatures()
    root = _component("Root", [_sketch("Boundary")], occurrences, af)
    return _wire(make_design(comp=root), af)


class TestPlanarFacePreflight:
    """MEASURED on one SPHERE among eight tray parts: both 2D solvers failed the whole arrange with
    ARRANGE_ERROR_MISSING_FACE, naming no shape and leaving no feature in the timeline, the seven
    others nested under both, and the 3D solver packed the sphere. So both 2D gates name what the
    walk READ - no face of these reads a plane - and leave the whole-feature claim to the platform
    sentence; a surface body or a torus is unmeasured and neither wording speaks for it."""

    @pytest.mark.parametrize("solver", ["true_shape", "rectangular"])
    def test_a_shape_with_no_planar_face_is_refused_before_the_add(self, solver):
        _, af = _install_shapes(_shape_with("Ball:1", _SPHERE_ST),
                                _shape_with("Pad:1", _PLANE_ST, _SPHERE_ST))
        res = ar.handler(boundary_sketch="Boundary", shapes="Ball:1,Pad:1", solver=solver)
        assert res["isError"] is True
        assert "Ball:1" in res["message"] and "Pad:1" not in res["message"]
        assert f"solver='{solver}'" in res["message"] and "solver='3d'" in res["message"]
        assert af.added is False                        # refused BEFORE the feature was created

    def test_the_3d_solver_packs_the_same_shape(self):
        _, af = _install_shapes(_shape_with("Ball:1", _SPHERE_ST))
        out = _payload(ar.handler(shapes="Ball:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100))
        assert out["arranged"] is True and af.added is True

    def test_only_the_faceless_shapes_are_named(self):
        _, af = _install_shapes(
            _shape_with("Ball:1", _SPHERE_ST),
            _shape_with("Pad:1", _PLANE_ST),
            _shape_with("Blob:1", _SPHERE_ST, adsk.core.SurfaceTypes.TorusSurfaceType))
        res = ar.handler(boundary_sketch="Boundary", shapes="Ball:1,Pad:1,Blob:1")
        assert res["isError"] is True
        assert "Ball:1" in res["message"] and "Blob:1" in res["message"]
        assert "Pad:1" not in res["message"] and af.added is False

    def test_a_sub_assembly_shape_is_judged_by_the_bodies_one_level_down(self):
        parent = make_occurrence(path="Sub:1", component=SimpleNamespace(name="Sub"),
                                 transform2=SimpleNamespace(translation=_vec()),
                                 children=[_shape_with("Sub:1+Ball:1", _SPHERE_ST)])
        _, af = _install_shapes(parent)
        res = ar.handler(boundary_sketch="Boundary", shapes="Sub:1")
        assert res["isError"] is True and "Sub:1" in res["message"] and af.added is False

    def test_a_shape_exposing_no_body_refuses_nothing(self):
        # Nothing was read to judge it by, and refusing on that would block a nest the platform
        # takes - the platform's own error still covers the case.
        _, af = _install_shapes(_occ("Sub:1"))
        out = _payload(ar.handler(boundary_sketch="Boundary", shapes="Sub:1"))
        assert out["arranged"] is True and af.added is True

    def test_a_face_whose_geometry_will_not_read_is_no_verdict(self):
        # A surface type that did not answer is not a non-planar face: collapsing the two would
        # refuse a nest on a read that never happened.
        _, af = _install_shapes(_shape_with("Ball:1", None))
        out = _payload(ar.handler(boundary_sketch="Boundary", shapes="Ball:1"))
        assert out["arranged"] is True and af.added is True

    def test_an_occurrence_whose_children_will_not_read_is_no_verdict(self):
        # The walk could not see the whole shape, so the bodies it DID read are not the whole
        # answer - the platform's own refusal covers what this walk could not reach.
        body = BRepBody(name="SubBody",
                        faces=[BRepFace(SimpleNamespace(surfaceType=_SPHERE_ST))])
        occ = make_occurrence(path="Sub:1", component=SimpleNamespace(name="Sub"),
                              transform2=SimpleNamespace(translation=_vec()), bodies=[body],
                              raises_on={"childOccurrences": "children unreadable"})
        _, af = _install_shapes(occ)
        out = _payload(ar.handler(boundary_sketch="Boundary", shapes="Sub:1"))
        assert out["arranged"] is True and af.added is True

    def test_the_platform_missing_face_error_is_worded_like_the_pre_flight(self):
        # A body the walk could not read still meets the platform's refusal, which names no shape -
        # and by then the feature is gone from the timeline, so that path says so.
        _, af = _install_shapes(_occ("Sub:1"))

        def _boom(inp):
            raise RuntimeError("3 : Arrange1 / Compute Failed // ARRANGE_ERROR_MISSING_FACE - "
                               "Missing planar face: cannot arrange these items.")

        af.add = _boom
        res = ar.handler(boundary_sketch="Boundary", shapes="Sub:1")
        assert res["isError"] is True and "ARRANGE_ERROR_MISSING_FACE" in res["message"]
        assert "gone from the timeline" in res["message"] and "solver='3d'" in res["message"]


class TestGroundedPreflight:
    """MEASURED on a scratch tray (Tray:1 holding P1:1, P2:1): move_originals=true failed the WHOLE
    arrange with the platform's '3 : Pinned component cannot be arranged' when one shape read
    isGroundToParent True. A positive read is the only thing this gate refuses on."""

    def test_a_grounded_shape_with_move_originals_is_refused_before_the_add(self):
        _, af = _install_shapes(_occ("Pinned:1", ground_to_parent=True), _occ("Free:1"))
        res = ar.handler(boundary_sketch="Boundary", shapes="Pinned:1,Free:1", move_originals=True)
        assert res["isError"] is True
        assert "Pinned:1" in res["message"] and "Free:1" not in res["message"]
        assert "assembly_ground(ground_to_parent=false)" in res["message"]
        assert af.added is False and af.last_input is None      # nothing created

    def test_the_same_shapes_with_the_flag_false_arrange(self):
        _, af = _install_shapes(_occ("Pinned:1", ground_to_parent=False), _occ("Free:1"))
        out = _payload(ar.handler(boundary_sketch="Boundary", shapes="Pinned:1,Free:1",
                                  move_originals=True))
        assert out["arranged"] is True and af.added is True

    def test_move_originals_false_is_not_refused_by_this_gate(self):
        # The copy path was not measured against a grounded shape - the gate leaves it alone.
        _, af = _install_shapes(_occ("Pinned:1", ground_to_parent=True))
        out = _payload(ar.handler(boundary_sketch="Boundary", shapes="Pinned:1"))
        assert out["arranged"] is True and af.added is True

    def test_the_platform_pinned_raise_is_mapped_to_the_shapes_the_preflight_would_have_named(self):
        # move_originals=false never runs the preflight, so this except-block mapping is the FIRST
        # read of the flag - it re-derives the same name the gate would have refused on.
        _, af = _install_shapes(_occ("Pinned:1", ground_to_parent=True))

        def _boom(inp):
            raise RuntimeError("3 : Arrange1 / Compute Failed // ARRANGE_ITEM_GROUNDED - Pinned "
                               "component cannot be arranged.")

        af.add = _boom
        res = ar.handler(boundary_sketch="Boundary", shapes="Pinned:1")
        assert res["isError"] is True
        assert "Pinned:1" in res["message"]
        assert "assembly_ground(ground_to_parent=false)" in res["message"]
        # "gone from the timeline" is NOT claimed here: unlike the missing-face platform text, this
        # raise names a feature (Arrange1) that may still be sitting in the timeline - unmeasured.

    def test_an_unnamed_platform_pinned_raise_without_the_code_omits_it(self):
        # The measured CREATE-path text carries no ARRANGE_ITEM_GROUNDED token - naming it anyway
        # would assert a platform code nothing here saw. No shape reads True either, so the
        # sentence still avoids the bare platform string via the remedy, not a fabricated code.
        _, af = _install_shapes(_occ("Ghost:1"))

        def _boom(inp):
            raise RuntimeError("3 : Pinned component cannot be arranged.")

        af.add = _boom
        res = ar.handler(boundary_sketch="Boundary", shapes="Ghost:1")
        assert res["isError"] is True
        assert "ARRANGE_ITEM_GROUNDED" not in res["message"]
        assert "assembly_ground(ground_to_parent=false)" in res["message"]

    def test_an_unnamed_platform_pinned_raise_with_the_code_names_it(self):
        # The other side: when the raise DOES carry the code and still names no shape, the code is
        # the one fact this branch actually observed, so it is named.
        _, af = _install_shapes(_occ("Ghost:1"))

        def _boom(inp):
            raise RuntimeError("3 : Arrange1 / Compute Failed // ARRANGE_ITEM_GROUNDED - Pinned "
                               "component cannot be arranged.")

        af.add = _boom
        res = ar.handler(boundary_sketch="Boundary", shapes="Ghost:1")
        assert res["isError"] is True
        assert "ARRANGE_ITEM_GROUNDED" in res["message"]
        assert "assembly_ground(ground_to_parent=false)" in res["message"]


class TestActiveComponentPreflight:
    """MEASURED on BOTH solvers: an arrange whose shapes include the ACTIVE component failed with a
    bare '3 :' (an empty platform message), and the identical call landed once root was active. The
    refusal rests on a comparison that ANSWERED - the component where new geometry lands."""

    def _install_active(self, active_shape):
        """A root holding one shape and a boundary, with the shape's component (or root) ACTIVE."""
        af = FakeArrangeFeatures()
        occ = _occ("A:1")
        root = _component("Root", [_sketch("Boundary")], [occ], af)
        design = make_design(comp=root)
        design.activeComponent = occ.component if active_shape else root
        _wire(design, af)
        return af, design, occ

    def test_a_shape_whose_component_is_active_is_refused_before_the_add(self):
        af, _design, _occ_a = self._install_active(True)
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1")
        assert res["isError"] is True
        assert "A:1" in res["message"] and "ACTIVE edit target" in res["message"]
        assert "design_activate_component('root')" in res["message"]
        assert af.added is False and af.last_input is None      # nothing created

    def test_the_same_call_with_root_active_arranges(self):
        af, _design, _occ_a = self._install_active(False)
        out = _payload(ar.handler(boundary_sketch="Boundary", shapes="A:1"))
        assert out["arranged"] is True and af.added is True

    def test_a_bare_platform_failure_names_the_active_shape(self):
        # The pre-flight is TRI-STATE: it refuses on a proven True alone, so a comparison that could
        # not be made lets the call through. When the platform then fails with a message carrying no
        # cause at all, the same read - answering by then - is what names one.
        af, design, occ = self._install_active(False)

        def _boom(inp):
            design.activeComponent = occ.component
            raise RuntimeError("3 :")

        af.add = _boom
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1")
        assert res["isError"] is True
        assert "A:1" in res["message"] and "ACTIVE edit target" in res["message"]
        assert "Platform: 3 :" in res["message"]

    def test_a_bare_platform_failure_with_no_active_shape_stays_the_plain_report(self):
        af, _design, _occ_a = self._install_active(False)

        def _boom(inp):
            raise RuntimeError("3 :")

        af.add = _boom
        res = ar.handler(boundary_sketch="Boundary", shapes="A:1")
        assert res["isError"] is True
        assert res["message"] == "Arrange failed: 3 :"


class TestHonesty:
    def test_add_returning_none_is_error(self):
        _, af = _install([_sketch("B")], ["A:1"])
        af.add = lambda inp: None
        res = ar.handler(boundary_sketch="B", shapes="A:1")
        assert res["isError"] is True and "no feature" in res["message"].lower()

    def test_add_raising_surfaces_as_error(self):
        _, af = _install([_sketch("B")], ["A:1"])

        def _boom(inp):
            raise RuntimeError("solver crashed")

        af.add = _boom
        res = ar.handler(boundary_sketch="B", shapes="A:1")
        assert res["isError"] is True
        assert "Arrange failed" in res["message"] and "solver crashed" in res["message"]

    def test_no_active_design(self):
        _install([_sketch("B")], ["A:1"])
        assert_no_active_design(ar, ar.handler, boundary_sketch="B", shapes="A:1")

    def test_copies_without_movement_are_disclosed(self):
        # The solver leaves the inputs unmoved and mints envelope copies - the payload must say so
        # instead of implying the named occurrences were packed.
        _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert out["moved"] == []
        assert out["new_occurrence_count"] == 1
        assert "Arrange1:1+Envelope1(Qty: 1):1+A:1" in out["new_occurrences"]
        assert "new_occurrences holds the copies" in out["note"] and "moved reads empty" in out["note"]

    def test_nothing_happened_is_an_error_and_rolls_back(self):
        # No input moved AND no occurrence appeared = the arrange did nothing; success would be a lie.
        _, af = _install([_sketch("B")], ["A:1"])
        deleted = []
        def _inert_add(inp):
            af.added = True
            return type("F", (), {"name": "Arrange1",
                                  "deleteMe": lambda self: deleted.append(True) or True})()
        af.add = _inert_add
        res = ar.handler(boundary_sketch="B", shapes="A:1")
        assert res["isError"] is True and "NOTHING happened" in res["message"]
        assert deleted == [True]

    def test_components_left_out_are_an_error_not_a_quiet_success(self):
        # The solver places what fits and reports the rest in its statistics; a plain ok here reads
        # as "all four nested" while a part sits outside the envelope.
        _, af = _install([_sketch("B")], ["A:1", "B:1", "C:1"])
        af.unarranged = 1
        res = ar.handler(boundary_sketch="B", shapes="A:1, B:1, C:1")
        assert res["isError"] is True
        assert "1 component(s) UNPLACED" in res["message"]
        assert "arranged 2, unarranged 1" in res["message"]
        assert "design_delete_feature" in res["message"]
        # partial is accepted on every envelope form, so the remedy is consumable on this call too
        assert "partial=true" in res["message"]

    def test_partial_accepts_what_did_not_fit(self):
        _, af = _install([], ["A:1", "B:1", "C:1"])
        af.unarranged = 1
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100,
                                  partial=True))
        assert out["components_arranged"] == 2 and out["components_unarranged"] == 1

    def test_the_statistics_map_is_published_with_the_counts(self):
        _, af = _install([_sketch("B")], ["A:1", "B:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1, B:1"))
        assert out["components_arranged"] == 2 and out["components_unarranged"] == 0
        # the 3D shape's one map, keyed by the name the JSON carries; mm: 16 cm3 * 1000
        assert out["statistics"]["Arrange1"]["Components Volume"] == 16000
        assert "volumes in mm^3" in out["note"]
        assert "areas are in" not in out["note"]     # this map carries no 'Area' key at all

    def test_a_volume_statistic_converts_to_units_cubed_while_a_count_does_not(self):
        # mm: cm3 -> mm3 is factor 10 CUBED (x1000) - the boundary an area-shaped (x100) mistake
        # would miss; 'Components Arranged' is a bare count and must pass through unconverted.
        _, af = _install([_sketch("B")], ["A:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert out["statistics"]["Arrange1"]["Components Volume"] == 16000
        assert out["statistics"]["Arrange1"]["Components Arranged"] == 1

    def test_the_plane_envelope_statistics_shape_is_read_per_envelope(self):
        # The 2D plane envelope answers {"envelopes": [{name, statistics}]} rather than one
        # top-level map, and reports no 'Components Unarranged' at all.
        _, af = _install([], ["A:1", "B:1", "C:1"])
        af.statistics = _sheet_stats_json(6)
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="rectangular",
                                  envelope_plane="xy", envelope_length=300, envelope_width=200,
                                  quantity=2))
        assert out["components_arranged"] == 6
        assert out["components_unarranged"] is None
        assert out["statistics"]["Envelope1"]["Envelope Area"] == 60000    # mm: 600 cm2 * 100
        assert "components_unarranged reads null, not zero" in out["note"]

    def test_an_area_statistic_converts_to_units_squared_while_a_count_does_not(self):
        # mm: cm2 -> mm2 is factor 10 SQUARED (x100), the boundary a linear-length mistake would
        # miss; 'Envelopes Quantity' is a bare count and must pass through unconverted.
        _, af = _install([], ["A:1"])
        af.statistics = _sheet_stats_json(1)
        out = _payload(ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                                  envelope_length=300, envelope_width=200))
        assert out["statistics"]["Envelope1"]["Envelope Area"] == 60000
        assert out["statistics"]["Envelope1"]["Envelopes Quantity"] == 1

    def test_the_per_envelope_counts_are_summed(self):
        _, af = _install([], ["A:1", "B:1", "C:1"])
        af.statistics = _sheet_stats_json(4, 2)
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="rectangular",
                                  envelope_plane="xy", envelope_length=300, envelope_width=200))
        assert out["components_arranged"] == 6
        assert sorted(out["statistics"]) == ["Envelope1", "Envelope2"]

    def test_both_statistics_levels_at_once_are_not_double_counted(self):
        # Measured: one arrangeStatistics carries the TOTAL map and the per-envelope breakdown of
        # the same nest. Summing across the two levels reports twice the components there are.
        _, af = _install([], ["A:1", "B:1", "C:1"])
        af.statistics = _both_stats_json(3, 0, 3)
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="rectangular",
                                  envelope_plane="xy", envelope_length=300, envelope_width=200))
        assert out["components_arranged"] == 3
        assert out["components_unarranged"] == 0
        # both maps are still published, keyed by the names they came under
        assert sorted(out["statistics"]) == ["Arrange1", "Envelope1"]

    def test_a_partial_arrange_names_the_shortfall_in_its_note(self):
        _, af = _install([], ["A:1", "B:1", "C:1"])
        af.unarranged = 1
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100,
                                  partial=True))
        assert out["components_unarranged"] == 1
        assert "1 component(s) did not fit" in out["note"]

    def test_an_unreported_unarranged_count_is_not_an_error(self):
        # The error fires on a READ value only: a statistics object carrying no unarranged key says
        # nothing about what was left out, and a defaulted 0 would make that silence an answer.
        _, af = _install([], ["A:1", "B:1", "C:1"])
        af.unarranged = 1
        af.statistics = _sheet_stats_json(2)
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="rectangular",
                                  envelope_plane="xy", envelope_length=300, envelope_width=200))
        assert out["components_arranged"] == 2 and out["components_unarranged"] is None

    def test_empty_statistics_publish_null_and_say_so(self):
        # arrangeStatistics returns '' on failure - the counts are UNKNOWN then, never a stand-in 0.
        _, af = _install([_sketch("B")], ["A:1"])
        af.statistics = ""
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert out["statistics"] is None
        assert out["components_arranged"] is None and out["components_unarranged"] is None
        assert "arrangeStatistics did not read" in out["note"]

    def test_the_result_envelopes_are_published_with_their_occupancy(self):
        # A PROFILE result envelope carries no bounding box at all, so its row has no 'extent' and
        # the note says which fact the absence rests on.
        _, af = _install([_sketch("B")], ["A:1", "B:1", "C:1"])
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1, B:1, C:1"))
        assert out["envelopes"] == 1
        row = out["result_envelopes"][0]
        assert row["name"] == "Envelope1" and row["occurrence_count"] == 3
        assert "extent" not in row
        assert "boxless" in out["note"] and "no 'extent'" in out["note"]

    def test_a_plane_envelope_publishes_a_two_axis_extent(self):
        # Its box is a BoundingBox2D of Point2Ds - reading .z raises - so the row carries x and y
        # only, and the numbers are the envelope's own SIZE in the caller's units.
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="rectangular", envelope_plane="xy",
                                  envelope_length=300, envelope_width=200))
        assert out["result_envelopes"][0]["extent"] == {"x": 300.0, "y": 200.0}
        assert "no 'extent'" not in out["note"]

    def test_a_3d_envelope_publishes_a_three_axis_extent(self):
        _, af = _install([], ["A:1"])
        out = _payload(ar.handler(shapes="A:1", solver="3d", envelope_plane="xy",
                                  envelope_length=200, envelope_width=200, envelope_height=100))
        assert out["result_envelopes"][0]["extent"] == {"x": 200.0, "y": 200.0, "z": 100.0}

    def test_the_published_envelope_rows_stop_at_the_cap(self):
        # 13 envelopes, 12 rows: the COUNT still reports every one, so a capped list never reads as
        # the whole set.
        _, af = _install([_sketch("B")], ["A:1"])
        af.envelope_rows = [(f"Envelope{i}", 1) for i in range(1, 14)]
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert out["envelopes"] == 13
        assert len(out["result_envelopes"]) == 12
        assert out["result_envelopes"][-1]["name"] == "Envelope12"

    def test_a_moved_input_is_named_in_moved(self):
        # A solver that really repositions the input reports it in 'moved'.
        design, af = _install([_sketch("B")], ["A:1"])
        real_add = af.add
        def _moving_add(inp):
            design.rootComponent.allOccurrences[0].transform2.translation = _vec(5.0, 0.0, 0.0)
            return real_add(inp)
        af.add = _moving_add
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1"))
        assert out["moved"] == ["A:1"]

    def test_a_narrower_shortfall_and_units_composition_fits_the_wire_budget(self):
        # This composes the partial-shortfall clause with the units sentence and the restructure
        # clause (a narrower case than the reachable worst case below) - test_prose_budget cannot
        # see a runtime composition, so it is measured here.
        design, af = _install([], ["A:1", "B:1", "C:1"])
        af.unarranged = 1
        real_add = af.add
        def _moving_add(inp):
            design.rootComponent.allOccurrences[0].transform2.translation = _vec(5.0, 0.0, 0.0)
            return real_add(inp)
        af.add = _moving_add
        out = _payload(ar.handler(shapes="A:1, B:1, C:1", solver="rectangular",
                                  envelope_plane="xy", envelope_length=300, envelope_width=200,
                                  partial=True))
        note = out["note"]
        assert "did not fit" in note and "volumes in mm^3" in note
        assert "restructured" in note      # the short clause: this scenario MOVED an input too
        assert len(note) <= 400, len(note)      # test_prose_budget.NOTE_BUDGET_CHARS

    def test_a_shortfall_and_the_null_unarranged_clause_never_compose_together(self):
        # stat_unarranged truthy (the shortfall fires) and stat_unarranged is None (the null clause
        # fires) are mutually exclusive branches - one JSON cannot carry both, so the reachable
        # worst case is whichever of the two is longer beside the rest, never both at once.
        _, af = _install([_sketch("B")], ["A:1", "B:1", "C:1"])
        af.statistics = json.dumps({
            "name": "Arrange1",
            "statistics": {"Components Arranged": {"value": 2}},
        })
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1, B:1, C:1"))
        note = out["note"]
        assert "did not fit" not in note
        assert "components_unarranged reads null, not zero" in note

    def test_the_full_worst_case_composition_fits_the_wire_budget(self):
        # The reachable worst case (measured): the SHORTFALL clause is skipped in favor of the
        # longer 'stat_unarranged is None' clause, beside a boxless (profile) envelope row, unmoved
        # COPIES, both measurement units, and the envelope-list cap - test_prose_budget cannot see
        # this (it is composed at RUN TIME).
        _, af = _install([_sketch("B")], ["A:1", "B:1", "C:1"])
        af.statistics = json.dumps({
            "name": "Arrange1",
            "statistics": {"Components Arranged": {"value": 3}, "Components Volume": {"value": 16}},
            "envelopes": [{"name": f"Envelope{i}", "statistics": {"Envelope Area": {"value": 600}}}
                         for i in range(1, 14)],
        })
        out = _payload(ar.handler(boundary_sketch="B", shapes="A:1, B:1, C:1"))
        note = out["note"]
        assert "did not fit" not in note        # the shortfall clause did NOT also fire
        assert "extent" in note and "new_occurrences" in note
        assert "areas are in mm^2" in note and "volumes in mm^3" in note
        assert "components_unarranged reads null, not zero" in note
        assert "first 12 of 14" in note
        assert len(note) <= 400, len(note)      # test_prose_budget.NOTE_BUDGET_CHARS
