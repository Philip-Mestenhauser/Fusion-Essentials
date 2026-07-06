"""Unit tests for surface_untrim - restore a trimmed surface face to its natural extent.

Pins: the loop_type maps to the right UntrimLoopTypes enum and the extension is scaled to cm and passed
to createInputFromFaces; extent_grew is TRUE only when the created-face area exceeds the input area and
FALSE (an honest ok) when it doesn't; a solid face is rejected naming the offender; an unknown loop_type
and missing faces are rejected; a null feature is an error.
"""

import types

import adsk.core
import adsk.fusion
import pytest

from conftest import load_tool, make_design, install, payload, error_message, MakeComp

su = load_tool("surface_untrim")


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i] if 0 <= i < len(self._i) else None


class _Body:
    def __init__(self, name="Srf1", is_solid=False):
        self.name = name
        self.isSolid = is_solid


class _Face:
    def __init__(self, area=1.0, body=None):
        self.area = area
        self.body = body if body is not None else _Body()


class _Feature:
    def __init__(self, name="Untrim1", faces=(), bodies=()):
        self.name = name
        self._faces = list(faces)
        self._bodies = list(bodies)
    @property
    def faces(self):
        return _Coll(self._faces)
    @property
    def bodies(self):
        return _Coll(self._bodies)


class _UntrimFeatures:
    def __init__(self, feature):
        self._feature = feature
        self.create_calls = []       # (faces, loop_type, extension) tuples
    def createInputFromFaces(self, faces, loop_type, extension):
        self.create_calls.append((faces, loop_type, extension))
        return types.SimpleNamespace(faces=faces, loop_type=loop_type, extension=extension)
    def add(self, inp):
        return self._feature


@pytest.fixture(autouse=True)
def _types(monkeypatch):
    monkeypatch.setattr(adsk.fusion, "BRepFace", _Face, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepBody", _Body, raising=False)
    monkeypatch.setattr(adsk.fusion, "UntrimLoopTypes", types.SimpleNamespace(
        AllLoopsUntrimType="all", ExternalLoopsUntrimType="external",
        InternalLoopsUntrimType="internal"), raising=False)
    # capture the scaled extension the handler hands to ValueInput.createByReal
    monkeypatch.setattr(adsk.core.ValueInput, "createByReal",
                        staticmethod(lambda v: ("real", v)), raising=False)


def _wire(feature, tokens):
    comp = MakeComp()
    uf = _UntrimFeatures(feature)
    comp.features = types.SimpleNamespace(untrimFeatures=uf)
    design = make_design(comp=comp, tokens=tokens)
    install(su, design)
    return uf


def test_extent_grew_true_when_area_increases():
    f = _Face(area=1.0)
    feat = _Feature(faces=[_Face(area=3.0), _Face(area=2.0)], bodies=[_Body("Srf1")])
    _wire(feat, {"H1": f})
    out = payload(su.untrim_handler(faces=["H1"]))
    assert out["untrimmed"] is True
    assert out["area_before"] == 1.0 and out["area_after"] == 5.0
    assert out["extent_grew"] is True


def test_no_growth_reported_honestly():
    f = _Face(area=2.0)
    feat = _Feature(faces=[_Face(area=2.0)], bodies=[_Body("Srf1")])   # same area -> no growth
    _wire(feat, {"H1": f})
    res = su.untrim_handler(faces=["H1"])
    assert res["isError"] is False
    out = payload(res)
    assert out["extent_grew"] is False
    assert "did not exceed" in out["note"]


def test_loop_type_maps_to_enum():
    f = _Face(area=1.0)
    feat = _Feature(faces=[_Face(area=2.0)], bodies=[_Body("Srf1")])
    uf = _wire(feat, {"H1": f})
    payload(su.untrim_handler(faces=["H1"], loop_type="external"))
    _faces, loop_type, _ext = uf.create_calls[0]
    assert loop_type == "external"


def test_extension_scaled_to_cm():
    f = _Face(area=1.0)
    feat = _Feature(faces=[_Face(area=2.0)], bodies=[_Body("Srf1")])
    uf = _wire(feat, {"H1": f})
    payload(su.untrim_handler(faces=["H1"], loop_type="all", extension=2, units="mm"))
    _faces, _loop, ext = uf.create_calls[0]
    assert ext[0] == "real" and abs(ext[1] - 0.2) < 1e-9      # 2 mm -> 0.2 cm


def test_solid_face_rejected():
    f = _Face(area=1.0, body=_Body("Block", is_solid=True))
    feat = _Feature(faces=[_Face(area=2.0)])
    _wire(feat, {"H1": f})
    res = su.untrim_handler(faces=["H1"])
    assert "SOLID" in error_message(res)


def test_unknown_loop_type_rejected():
    f = _Face(area=1.0)
    _wire(_Feature(faces=[_Face(area=2.0)]), {"H1": f})
    res = su.untrim_handler(faces=["H1"], loop_type="diagonal")
    assert "all, external, internal" in error_message(res)


def test_missing_faces_rejected():
    _wire(_Feature(), {})
    res = su.untrim_handler(faces=None)
    assert res["isError"] is True
    assert "needs a list of geometry handles" in error_message(res)


def test_null_feature_is_error():
    f = _Face(area=1.0)

    class _NullUntrim:
        def createInputFromFaces(self, *a):
            return object()
        def add(self, inp):
            return None

    comp = MakeComp()
    comp.features = types.SimpleNamespace(untrimFeatures=_NullUntrim())
    design = make_design(comp=comp, tokens={"H1": f})
    install(su, design)
    res = su.untrim_handler(faces=["H1"])
    assert "no feature" in error_message(res)
