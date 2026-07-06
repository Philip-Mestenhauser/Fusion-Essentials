"""Unit tests for ``model_split.py`` - SplitBody / SplitFace dispatched by 'split'.

Pinned here (no live Fusion): the split dispatch, the exactly-one-cutter guard (neither / both), the
no-active-design guard, resolution-error propagation, and the honesty verifiers - a body split that
yields a SINGLE body is reported as an error (no silent ok), a face split that creates no new faces is
an error, and the resulting body count / net face-count increase are read back and reported.
"""

import pytest

from conftest import (load_tool, make_design, install, MakeComp, payload,
                      error_message, assert_no_active_design)

sp = load_tool("model_split")


# ── body-split fakes ────────────────────────────────────────────────────────

class FakeBodies:
    def __init__(self, names):
        self._names = list(names)

    @property
    def count(self):
        return len(self._names)

    def item(self, i):
        return type("B", (), {"name": self._names[i]})()


class FakeSplitBodyFeature:
    def __init__(self, result_names=("Body1", "Body2"), health=0):
        self.name = "Split1"
        self.bodies = FakeBodies(result_names)
        self.healthState = health
        self.errorOrWarningMessage = "no intersection"


class FakeSplitBodyFeatures:
    def __init__(self, feature=None):
        self.last = None
        self._feature = feature

    def createInput(self, body, tool, extend):
        self.last = (body, tool, extend)
        return type("I", (), {})()

    def add(self, inp):
        return self._feature if self._feature is not None else FakeSplitBodyFeature()


# ── face-split fakes ────────────────────────────────────────────────────────

class FakeFaceCount:
    def __init__(self, n):
        self.count = n


class FakeOwnBody:
    def __init__(self, n):
        self.faces = FakeFaceCount(n)


class FakeSplitFaceFeature:
    def __init__(self, created=2, health=0):
        self.name = "SplitFace1"
        self.faces = FakeFaceCount(created)
        self.healthState = health
        self.errorOrWarningMessage = ""


class FakeSplitFaceFeatures:
    def __init__(self, bumps=1, feature=None):
        self.last = None
        self._bumps = bumps        # how many faces the split adds to the owning body
        self._feature = feature
        self._body = None

    def bind_body(self, body):
        self._body = body

    def createInput(self, faces, tool, extend):
        self.last = (faces, tool, extend)
        return type("I", (), {})()

    def add(self, inp):
        if self._body is not None:
            self._body.faces.count += self._bumps      # simulate the split growing the face count
        return self._feature if self._feature is not None else FakeSplitFaceFeature()


def _comp_with(body_feats=None, face_feats=None):
    comp = MakeComp(name="Comp")
    comp.features = type("F", (), {
        "splitBodyFeatures": body_feats if body_feats is not None else FakeSplitBodyFeatures(),
        "splitFaceFeatures": face_feats if face_feats is not None else FakeSplitFaceFeatures(),
    })()
    return comp


def _wire_body(monkeypatch, feature=None, target=("body-obj", None), cutter=("plane", None)):
    feats = FakeSplitBodyFeatures(feature)
    install(sp, make_design(comp=_comp_with(body_feats=feats)))
    monkeypatch.setattr(sp._TARGET, "resolve", lambda raw: target)
    monkeypatch.setattr(sp._PLANE, "resolve", lambda raw: cutter)
    monkeypatch.setattr(sp._TOOLBODY, "resolve", lambda raw: cutter)
    return feats


def _wire_face(monkeypatch, feats, faces, cutter=("plane", None)):
    install(sp, make_design(comp=_comp_with(face_feats=feats)))
    monkeypatch.setattr(sp._FACES, "resolve", lambda raw: (faces, None))
    monkeypatch.setattr(sp._PLANE, "resolve", lambda raw: cutter)
    return feats


class TestCutterGuard:
    def test_no_cutter_is_error(self, monkeypatch):
        _wire_body(monkeypatch)
        msg = error_message(sp.handler(split="body", target="Body1"))
        assert "No cutter" in msg

    def test_both_cutters_is_error(self, monkeypatch):
        _wire_body(monkeypatch)
        msg = error_message(sp.handler(split="body", target="Body1",
                                       split_plane="xy", split_tool_body="ToolBody"))
        assert "not both" in msg

    def test_bad_split_kind_is_error(self, monkeypatch):
        _wire_body(monkeypatch)
        msg = error_message(sp.handler(split="chunks", target="Body1", split_plane="xy"))
        assert "split" in msg

    def test_no_active_design(self, monkeypatch):
        _wire_body(monkeypatch)
        assert_no_active_design(sp, sp.handler, split="body", target="Body1", split_plane="xy")


class TestSplitBody:
    def test_two_bodies_is_ok(self, monkeypatch):
        _wire_body(monkeypatch, feature=FakeSplitBodyFeature(("A", "B")))
        out = payload(sp.handler(split="body", target="Body1", split_plane="xy"))
        assert out["split"] == "body"
        assert out["result_count"] == 2
        assert out["result_bodies"] == ["A", "B"]
        assert out["feature"] == "Split1"

    def test_single_body_is_error_not_silent_ok(self, monkeypatch):
        # The cutter did not divide the body: one resulting body -> honesty demands isError.
        _wire_body(monkeypatch, feature=FakeSplitBodyFeature(("OnlyOne",)))
        res = sp.handler(split="body", target="Body1", split_plane="xy")
        assert res["isError"] is True and "did not divide" in res["message"]

    def test_target_resolution_error_propagates(self, monkeypatch):
        _wire_body(monkeypatch, target=(None, "'target': no body named 'X'."))
        msg = error_message(sp.handler(split="body", target="X", split_plane="xy"))
        assert "no body named 'X'" in msg

    def test_cutter_via_tool_body(self, monkeypatch):
        feats = _wire_body(monkeypatch, cutter=("surf-body", None))
        payload(sp.handler(split="body", target="Body1", split_tool_body="Surf"))
        assert feats.last[1] == "surf-body"        # the resolved tool body reached createInput

    def test_health_error_reported(self, monkeypatch):
        _wire_body(monkeypatch, feature=FakeSplitBodyFeature(("A", "B"), health=2))
        res = sp.handler(split="body", target="Body1", split_plane="xy")
        assert res["isError"] is True and "failed to compute" in res["message"]

    def test_declared_outputs_present(self, monkeypatch):
        _wire_body(monkeypatch, feature=FakeSplitBodyFeature(("A", "B")))
        out = payload(sp.handler(split="body", target="Body1", split_plane="xy"))
        for o in sp.RETURNS:
            assert o.assert_present(out) == "", o.key


class TestSplitFace:
    def test_face_delta_reported(self, monkeypatch):
        body = FakeOwnBody(6)
        faces = [type("Fc", (), {"body": body})(), type("Fc", (), {"body": body})()]
        feats = FakeSplitFaceFeatures(bumps=1, feature=FakeSplitFaceFeature(created=2))
        feats.bind_body(body)
        _wire_face(monkeypatch, feats, faces)
        out = payload(sp.handler(split="face", faces=["a", "b"], split_plane="xy"))
        assert out["split"] == "face"
        assert out["faces_targeted"] == 2
        assert out["faces_created"] == 2
        assert out["result_count"] == 1        # 6 -> 7 net increase on the owning body

    def test_no_new_faces_is_error(self, monkeypatch):
        body = FakeOwnBody(6)
        faces = [type("Fc", (), {"body": body})()]
        feats = FakeSplitFaceFeatures(bumps=0, feature=FakeSplitFaceFeature(created=0))
        feats.bind_body(body)
        _wire_face(monkeypatch, feats, faces)
        res = sp.handler(split="face", faces=["a"], split_plane="xy")
        assert res["isError"] is True and "no new faces" in res["message"]

    def test_missing_faces_is_error(self, monkeypatch):
        feats = FakeSplitFaceFeatures()
        _wire_face(monkeypatch, feats, [])
        msg = error_message(sp.handler(split="face", faces=[], split_plane="xy"))
        assert "'faces' is required" in msg

    def test_faces_reach_createinput_as_collection(self, monkeypatch):
        body = FakeOwnBody(4)
        faces = [type("Fc", (), {"body": body})()]
        feats = FakeSplitFaceFeatures(bumps=1)
        feats.bind_body(body)
        _wire_face(monkeypatch, feats, faces)
        payload(sp.handler(split="face", faces=["a"], split_plane="xy"))
        assert feats.last[0].count == 1        # the ObjectCollection carries the one face
