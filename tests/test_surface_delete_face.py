"""Unit tests for surface_delete_face - delete faces, optionally healing, with a face-count read-back.

Pins: heal=true routes to deleteFaceFeatures (heal) and heal=false to surfaceDeleteFaceFeatures (no
heal); the body face-count delta is reported; a delete that consumes a whole body is reported (not a
bare success); a heal that raises / returns null is an error naming the heal=false fallback; missing
faces is rejected.
"""

import types

import adsk.fusion
import pytest

from conftest import load_tool, make_design, install, payload, error_message, MakeComp

sdf = load_tool("surface_delete_face")


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i] if 0 <= i < len(self._i) else None


class _Face:
    def __init__(self, body=None):
        self.body = body


class _Body:
    def __init__(self, name="Srf1", is_solid=False, face_count=0, faces=None):
        self.name = name
        self.isSolid = is_solid
        if faces is not None:
            self._faces = list(faces)
        else:
            self._faces = [_Face() for _ in range(face_count)]
        for f in self._faces:
            if f.body is None:
                f.body = self
    @property
    def faces(self):
        return _Coll(self._faces)


class _Feature:
    def __init__(self, name="DeleteFace1", bodies=()):
        self.name = name
        self._bodies = list(bodies)
    @property
    def bodies(self):
        return _Coll(self._bodies)


class _DelFeatures:
    def __init__(self, result):
        self._result = result
        self.calls = 0
    def add(self, coll):
        self.calls += 1
        r = self._result
        return r(coll) if callable(r) else r


class _Raises:
    def __init__(self):
        self.calls = 0
    def add(self, coll):
        self.calls += 1
        raise RuntimeError("body cannot be healed")


@pytest.fixture(autouse=True)
def _types(monkeypatch):
    monkeypatch.setattr(adsk.fusion, "BRepFace", _Face, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepBody", _Body, raising=False)


def _wire(tokens, delete=None, surface_delete=None):
    comp = MakeComp()
    comp.features = types.SimpleNamespace(
        deleteFaceFeatures=delete, surfaceDeleteFaceFeatures=surface_delete)
    design = make_design(comp=comp, tokens=tokens)
    install(sdf, design)
    return comp.features


def test_plain_delete_reports_face_count_delta():
    body = _Body("Srf1", face_count=6)
    target = body._faces[0]
    result = _Feature(bodies=[_Body("Srf1", face_count=5)])
    feats = _wire({"F1": target}, surface_delete=_DelFeatures(result), delete=_DelFeatures(result))
    out = payload(sdf.delete_face_handler(faces=["F1"], heal=False))
    assert out["heal"] is False
    assert out["faces_before"] == 6 and out["faces_after"] == 5
    assert out["bodies_consumed"] == 0
    assert feats.surfaceDeleteFaceFeatures.calls == 1
    assert feats.deleteFaceFeatures.calls == 0        # non-heal path only


def test_heal_routes_to_deleteFaceFeatures():
    body = _Body("Solid1", is_solid=True, face_count=6)
    target = body._faces[0]
    result = _Feature(bodies=[_Body("Solid1", is_solid=True, face_count=6)])
    feats = _wire({"F1": target}, delete=_DelFeatures(result), surface_delete=_DelFeatures(result))
    out = payload(sdf.delete_face_handler(faces=["F1"], heal=True))
    assert out["heal"] is True
    assert feats.deleteFaceFeatures.calls == 1
    assert feats.surfaceDeleteFaceFeatures.calls == 0
    assert "healed" in out["note"]


def test_consumed_body_is_reported():
    body = _Body("Srf1", face_count=1)
    target = body._faces[0]
    result = _Feature(bodies=[])            # no result body -> the body vanished
    _wire({"F1": target}, surface_delete=_DelFeatures(result))
    res = sdf.delete_face_handler(faces=["F1"], heal=False)
    assert res["isError"] is False           # reported, not hidden - but surfaced explicitly
    out = payload(res)
    assert out["bodies_consumed"] == 1
    assert "warning" in out and "consumed" in out["warning"]


def test_heal_failure_is_error_pointing_to_no_heal():
    body = _Body("Solid1", is_solid=True, face_count=6)
    target = body._faces[0]
    _wire({"F1": target}, delete=_Raises())
    res = sdf.delete_face_handler(faces=["F1"], heal=True)
    msg = error_message(res)
    assert "heal=false" in msg


def test_heal_null_feature_is_error():
    body = _Body("Solid1", is_solid=True, face_count=6)
    target = body._faces[0]
    _wire({"F1": target}, delete=_DelFeatures(lambda coll: None))
    res = sdf.delete_face_handler(faces=["F1"], heal=True)
    assert "heal=false" in error_message(res)


def test_faces_from_two_bodies_tracked():
    b1 = _Body("Srf1", face_count=4)
    b2 = _Body("Srf2", face_count=3)
    f1, f2 = b1._faces[0], b2._faces[0]
    result = _Feature(bodies=[_Body("Srf1", face_count=3), _Body("Srf2", face_count=2)])
    _wire({"F1": f1, "F2": f2}, surface_delete=_DelFeatures(result))
    out = payload(sdf.delete_face_handler(faces=["F1", "F2"], heal=False))
    assert sorted(out["input_bodies"]) == ["Srf1", "Srf2"]
    assert out["bodies_consumed"] == 0
    assert out["faces_requested"] == 2


def test_missing_faces_rejected():
    _wire({}, surface_delete=_DelFeatures(_Feature()))
    res = sdf.delete_face_handler(faces=None)
    assert res["isError"] is True
