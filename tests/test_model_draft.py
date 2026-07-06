"""Unit tests for ``model_draft.py`` - taper faces to a pull direction (the Draft feature).

Pinned here (no live Fusion): the angle guards (non-number / zero / out-of-range), that the resolved
faces + pull plane reach DraftFeatures.createInput, that 'symmetric' flows into setSingleAngle, the
drafted-face read-back, the no-active-design guard, resolution-error propagation, and the honesty
verifier that a feature which computes with a health ERROR is reported as failure, not a false ok.
"""

import pytest

from conftest import (load_tool, make_design, install, MakeComp, payload,
                      error_message, assert_no_active_design)

dr = load_tool("model_draft")


# ── fakes: the draftFeatures collection + input + created feature ───────────────────────────────

class FakeInputFaces:
    def __init__(self, n):
        self._n = n

    @property
    def count(self):
        return self._n


class FakeDraftInput:
    def __init__(self, faces, plane, tangent):
        self.faces = faces
        self.plane = plane
        self.isTangentChain = tangent
        self.isDirectionFlipped = False
        self.single = None

    def setSingleAngle(self, symmetric, angle):
        self.single = (symmetric, angle)
        return True


class FakeDraftFeature:
    def __init__(self, name="Draft1", n_faces=2, health=0):
        self.name = name
        self.inputFaces = FakeInputFaces(n_faces)
        self.healthState = health
        self.errorOrWarningMessage = "geometry undercut"


class FakeDraftFeatures:
    def __init__(self, feature=None):
        self.last = None
        self._feature = feature

    def createInput(self, faces, plane, tangent):
        self.last = FakeDraftInput(faces, plane, tangent)
        return self.last

    def add(self, inp):
        return self._feature if self._feature is not None else FakeDraftFeature()


def _wire(monkeypatch, feature=None, faces=None, plane=("plane", "xy")):
    """Install a design whose component carries a fake draftFeatures, and stub the two input kinds so
    the handler gets canned entities (the kinds' own resolution is covered by test_inputs)."""
    feats = FakeDraftFeatures(feature)
    comp = MakeComp(name="Comp")
    comp.features = type("F", (), {"draftFeatures": feats})()
    install(dr, make_design(comp=comp))
    resolved_faces = faces if faces is not None else [object(), object()]
    monkeypatch.setattr(dr._FACES, "resolve", lambda raw: (resolved_faces, None))
    monkeypatch.setattr(dr._PULL, "resolve", lambda raw: (plane, None))
    return feats


class TestGuards:
    def test_angle_not_a_number(self, monkeypatch):
        _wire(monkeypatch)
        msg = error_message(dr.handler(faces=["h"], pull_direction="xy", angle_deg="wide"))
        assert "angle_deg" in msg and "number" in msg

    def test_zero_angle_rejected(self, monkeypatch):
        _wire(monkeypatch)
        msg = error_message(dr.handler(faces=["h"], pull_direction="xy", angle_deg=0))
        assert "non-zero" in msg

    def test_angle_out_of_range_rejected(self, monkeypatch):
        _wire(monkeypatch)
        msg = error_message(dr.handler(faces=["h"], pull_direction="xy", angle_deg=90))
        assert "-90 and 90" in msg

    def test_no_active_design(self, monkeypatch):
        _wire(monkeypatch)
        assert_no_active_design(dr, dr.handler, faces=["h"], pull_direction="xy", angle_deg=3)

    def test_face_resolution_error_propagates(self, monkeypatch):
        _wire(monkeypatch)
        monkeypatch.setattr(dr._FACES, "resolve", lambda raw: (None, "'faces' must be a face, but the handle points at a BRepEdge."))
        msg = error_message(dr.handler(faces=["edge"], pull_direction="xy", angle_deg=3))
        assert "must be a face" in msg

    def test_pull_direction_error_propagates(self, monkeypatch):
        _wire(monkeypatch)
        monkeypatch.setattr(dr._PULL, "resolve", lambda raw: (None, "'pull_direction' is required (a plane alias, name, or handle)."))
        msg = error_message(dr.handler(faces=["h"], pull_direction="", angle_deg=3))
        assert "pull_direction" in msg


class TestDraft:
    def test_happy_path_reports_read_back_count(self, monkeypatch):
        feats = _wire(monkeypatch, feature=FakeDraftFeature(n_faces=3),
                      faces=[object(), object()])
        out = payload(dr.handler(faces=["a", "b"], pull_direction="xy", angle_deg=5))
        assert out["drafted"] is True
        assert out["feature"] == "Draft1"
        assert out["faces_requested"] == 2      # two handles resolved
        assert out["faces_drafted"] == 3        # read from feature.inputFaces (tangent chain grew it)
        assert out["angle_deg"] == 5

    def test_createinput_gets_faces_and_plane(self, monkeypatch):
        resolved = [object(), object()]
        feats = _wire(monkeypatch, faces=resolved, plane=("plane", "xy"))
        payload(dr.handler(faces=["a", "b"], pull_direction="xy", angle_deg=5))
        assert feats.last.faces == resolved         # a Python list, per the live signature
        assert feats.last.plane == ("plane", "xy")

    def test_symmetric_flows_into_set_single_angle(self, monkeypatch):
        feats = _wire(monkeypatch)
        payload(dr.handler(faces=["a"], pull_direction="xy", angle_deg=5, symmetric=True))
        assert feats.last.single is not None and feats.last.single[0] is True

    def test_default_not_symmetric(self, monkeypatch):
        feats = _wire(monkeypatch)
        payload(dr.handler(faces=["a"], pull_direction="xy", angle_deg=5))
        assert feats.last.single[0] is False

    def test_flip_sets_direction_flipped(self, monkeypatch):
        feats = _wire(monkeypatch)
        payload(dr.handler(faces=["a"], pull_direction="xy", angle_deg=5, flip=True))
        assert feats.last.isDirectionFlipped is True

    def test_health_error_reported_not_false_ok(self, monkeypatch):
        # The feature was ADDED but computes with a health ERROR (2) - honesty demands isError, not ok.
        _wire(monkeypatch, feature=FakeDraftFeature(health=2))
        res = dr.handler(faces=["a"], pull_direction="xy", angle_deg=5)
        assert res["isError"] is True
        assert "failed to compute" in res["message"] and "geometry undercut" in res["message"]

    def test_add_returns_none_is_error(self, monkeypatch):
        feats = _wire(monkeypatch)
        feats.add = lambda inp: None
        res = dr.handler(faces=["a"], pull_direction="xy", angle_deg=5)
        assert res["isError"] is True and "no feature" in res["message"]

    def test_declared_outputs_present(self, monkeypatch):
        _wire(monkeypatch)
        out = payload(dr.handler(faces=["a"], pull_direction="xy", angle_deg=5))
        for o in dr.RETURNS:
            assert o.assert_present(out) == "", o.key
