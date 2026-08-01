"""Unit tests for ``model_offset_face.py`` - push/pull faces along their normal by a signed distance.

Pinned: distance/units resolution (zero rejected, negative ALLOWED - it pushes inward), that the
resolved faces reach OffsetFacesFeatures.createInput as a plain LIST (live-verified: the SWIG binding
wants a vector, and an ObjectCollection raises a vector-type argument error - the fake's createInput
asserts the shape so a regression back to ObjectCollection goes red) alongside the distance ValueInput,
the no-active-design guard, resolution-error propagation, the health-error honesty gate, and the
volume-diff honesty gate - an offset the API reports as success but that leaves every affected body's
volume unchanged must surface as an error, not a false ok.
"""

from conftest import (load_tool, make_design, install, MakeComp, payload,
                      error_message, assert_no_active_design, assert_unknown_units)

of = load_tool("model_offset_face")


# ── fakes: a face+body pair + the offsetFacesFeatures collection ────────────────────────────────

class _VI:
    """A ValueInput stand-in carrying the raw cm value the handler set."""
    def __init__(self, value):
        self.value = value


class FakeBody:
    """A solid body whose volume the offset feature mutates in place (no new body, no face-count
    change - unlike Shell, a push/pull edits the selected face(s) of the SAME body)."""
    def __init__(self, name="Body1", volume=100.0):
        self.name = name
        self.volume = volume


class FakeFace:
    """A BRep face resolved from a find_geometry handle; carries its owning body."""
    def __init__(self, body):
        self.body = body


class FakeOffsetInput:
    def __init__(self, face_list, distance):
        self.faces = face_list
        self.distance = distance


class FakeOffsetFeature:
    def __init__(self, name="OffsetFaces1", health=0):
        self.name = name
        self.healthState = health
        self.errorOrWarningMessage = "geometry self-intersects"


class FakeOffsetFacesFeatures:
    """createInput/add that simulate a push/pull: add() shifts each affected body's volume by
    `volume_delta` (split evenly across the distinct bodies given), unless volume_delta is 0 - which
    models a silent no-op the API still reports as success."""
    def __init__(self, bodies, volume_delta=10.0, return_feature=True, health=0):
        self.bodies = list(bodies)
        self.volume_delta = volume_delta
        self.return_feature = return_feature
        self.health = health
        self.last_input = None

    def createInput(self, face_list, distance):
        # live-verified: the SWIG binding wants a Python list (a vector), NOT an ObjectCollection -
        # passing one raises a vector-type argument error. Pin the shape so a regression goes red.
        assert isinstance(face_list, list), "createInput must receive a plain list, not an ObjectCollection"
        self.last_input = FakeOffsetInput(face_list, distance)
        return self.last_input

    def add(self, inp):
        if not self.return_feature:
            return None
        per_body = self.volume_delta / len(self.bodies) if self.bodies else 0.0
        for b in self.bodies:
            b.volume += per_body
        return FakeOffsetFeature(health=self.health)


def _wire(monkeypatch, feats, faces):
    """Install a component carrying `feats` (features.offsetFacesFeatures), stub _FACES.resolve to
    hand back canned face entities (the kind's own resolution is covered by test_inputs), and model
    the adsk ValueInput factory the handler calls."""
    import adsk.core
    comp = MakeComp(name="Comp")
    comp.features = type("F", (), {"offsetFacesFeatures": feats})()
    install(of, make_design(comp=comp))
    monkeypatch.setattr(of._FACES, "resolve", lambda raw: (faces, None))
    adsk.core.ValueInput.createByReal = staticmethod(lambda v: _VI(v))


# ── happy path ────────────────────────────────────────────────────────────────

class TestOffset:
    def test_happy_path_reports_volume_delta(self, monkeypatch):
        body = FakeBody(name="Wall", volume=100.0)
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=8.0)
        _wire(monkeypatch, feats, [face])
        out = payload(of.handler(faces=["h"], distance=2, units="mm"))
        assert out["offset"] is True
        assert out["feature"] == "OffsetFaces1"
        assert out["faces_requested"] == 1
        assert out["bodies"] == ["Wall"]
        assert out["volume_delta_cm3"] == 8.0

    def test_negative_distance_is_allowed_and_pushes_inward(self, monkeypatch):
        body = FakeBody(volume=100.0)
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=-8.0)
        _wire(monkeypatch, feats, [face])
        out = payload(of.handler(faces=["h"], distance=-2, units="mm"))
        assert out["distance"] == -2
        assert out["volume_delta_cm3"] == -8.0
        # negative mm distance -> negative cm ValueInput
        assert round(feats.last_input.distance.value, 6) == -0.2

    def test_createinput_gets_faces_and_distance(self, monkeypatch):
        body = FakeBody(volume=100.0)
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=5.0)
        _wire(monkeypatch, feats, [face])
        payload(of.handler(faces=["h"], distance=3, units="mm"))
        assert isinstance(feats.last_input.faces, list) and len(feats.last_input.faces) == 1
        assert round(feats.last_input.distance.value, 6) == 0.3   # 3mm -> 0.3cm

    def test_multi_body_faces_dedupe_and_sum_deltas(self, monkeypatch):
        b1 = FakeBody(name="A", volume=50.0)
        b2 = FakeBody(name="B", volume=60.0)
        faces = [FakeFace(b1), FakeFace(b1), FakeFace(b2)]   # two faces on b1, one on b2
        feats = FakeOffsetFacesFeatures([b1, b2], volume_delta=10.0)
        _wire(monkeypatch, feats, faces)
        out = payload(of.handler(faces=["h1", "h2", "h3"], distance=1, units="mm"))
        assert out["faces_requested"] == 3
        assert sorted(out["bodies"]) == ["A", "B"]
        assert out["volume_delta_cm3"] == 10.0     # 5.0 into each body, summed back

    def test_cm_units_scale_distance(self, monkeypatch):
        body = FakeBody(volume=100.0)
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=1.0)
        _wire(monkeypatch, feats, [face])
        payload(of.handler(faces=["h"], distance=2, units="cm"))
        assert feats.last_input.distance.value == 2.0   # 2cm -> 2.0cm internal


# ── guards ───────────────────────────────────────────────────────────────────

class TestGuards:
    def test_no_active_design(self, monkeypatch):
        body = FakeBody()
        face = FakeFace(body)
        _wire(monkeypatch, FakeOffsetFacesFeatures([body]), [face])
        assert_no_active_design(of, of.handler, faces=["h"], distance=1, units="mm")

    def test_bad_units(self, monkeypatch):
        body = FakeBody()
        face = FakeFace(body)
        _wire(monkeypatch, FakeOffsetFacesFeatures([body]), [face])
        assert_unknown_units(of.handler, faces=["h"], distance=1)

    def test_zero_distance_rejected(self, monkeypatch):
        body = FakeBody()
        face = FakeFace(body)
        _wire(monkeypatch, FakeOffsetFacesFeatures([body]), [face])
        msg = error_message(of.handler(faces=["h"], distance=0, units="mm"))
        assert "distance" in msg and "non-zero" in msg

    def test_face_resolution_error_propagates(self, monkeypatch):
        body = FakeBody()
        _wire(monkeypatch, FakeOffsetFacesFeatures([body]), [FakeFace(body)])
        monkeypatch.setattr(of._FACES, "resolve",
                            lambda raw: (None, "'faces' must be a face, but the handle points at a BRepEdge."))
        msg = error_message(of.handler(faces=["edge"], distance=1, units="mm"))
        assert "must be a face" in msg


# ── honesty ──────────────────────────────────────────────────────────────────

class TestHonesty:
    def test_unchanged_volume_reports_error_not_ok(self, monkeypatch):
        # add() returns a feature but leaves volume identical - a silent no-op the API still calls
        # success. The handler must catch that and return isError, never a false 'offset'.
        body = FakeBody(volume=100.0)
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=0.0)
        _wire(monkeypatch, feats, [face])
        res = of.handler(faces=["h"], distance=2, units="mm")
        assert res["isError"] is True and "unchanged" in res["message"]

    def test_health_error_reported_not_false_ok(self, monkeypatch):
        body = FakeBody(volume=100.0)
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=8.0, health=2)
        _wire(monkeypatch, feats, [face])
        res = of.handler(faces=["h"], distance=2, units="mm")
        assert res["isError"] is True
        assert "failed to compute" in res["message"] and "self-intersects" in res["message"]

    def test_no_feature_returned_is_error(self, monkeypatch):
        body = FakeBody()
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], return_feature=False)
        _wire(monkeypatch, feats, [face])
        res = of.handler(faces=["h"], distance=1, units="mm")
        assert res["isError"] is True and "no feature" in res["message"].lower()

    def test_add_raising_surfaces_as_error(self, monkeypatch):
        body = FakeBody()
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body])
        feats.add = lambda inp: (_ for _ in ()).throw(RuntimeError("offset undercuts the body"))
        _wire(monkeypatch, feats, [face])
        res = of.handler(faces=["h"], distance=999, units="mm")
        assert res["isError"] is True and "Offset face failed" in res["message"]

    def test_face_with_no_readable_body_is_error(self, monkeypatch):
        face = FakeFace(None)   # body attribute reads None - nothing to offset against
        feats = FakeOffsetFacesFeatures([])
        _wire(monkeypatch, feats, [face])
        res = of.handler(faces=["h"], distance=1, units="mm")
        assert res["isError"] is True and "owning body" in res["message"]


# ── declared output contract ─────────────────────────────────────────────────

class TestOutputContract:
    def test_feature_output_is_minted(self, monkeypatch):
        body = FakeBody(name="Block")
        face = FakeFace(body)
        feats = FakeOffsetFacesFeatures([body], volume_delta=5.0)
        _wire(monkeypatch, feats, [face])
        out = payload(of.handler(faces=["h"], distance=1, units="mm"))
        for o in of.RETURNS:
            assert o.assert_present(out) == "", o.key
