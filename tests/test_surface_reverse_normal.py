"""Unit tests for surface_reverse_normal - flip open-surface normals with an isParamReversed read-back.

Pins: ALL input bodies are handed to reverseNormalFeatures.add; reversed_confirmed is TRUE only when
the isParamReversed read-back shows every face toggled and FALSE (an honest ok, not an error) when it
doesn't; a solid body is rejected; missing bodies is rejected; a null feature is an error.

Uses the shared install()/make_design() plumbing (both design seams patched) per tests/CLAUDE.md; the
BRepBody/BRepFace type classes are monkeypatched so the input-kind isinstance checks fire.
"""

import types

import adsk.fusion
import pytest

from conftest import load_tool, make_design, install, payload, error_message, MakeComp

srn = load_tool("surface_reverse_normal")


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i] if 0 <= i < len(self._i) else None


class _Face:
    def __init__(self, reversed_=False, body=None):
        self.isParamReversed = reversed_
        self.body = body


class _Body:
    def __init__(self, name, is_solid=False, faces=()):
        self.name = name
        self.isSolid = is_solid
        self._faces = list(faces)
        for f in self._faces:
            if f.body is None:
                f.body = self
    @property
    def faces(self):
        return _Coll(self._faces)


class _Feature:
    def __init__(self, name="ReverseNormal1", bodies=(), faces=()):
        self.name = name
        self._bodies = list(bodies)
        self._faces = list(faces)
    @property
    def bodies(self):
        return _Coll(self._bodies)
    @property
    def faces(self):
        return _Coll(self._faces)


class _RevFeatures:
    def __init__(self, result):
        self._result = result
        self.calls = []          # each entry is the ObjectCollection add() received
    def add(self, coll):
        self.calls.append(coll)
        r = self._result
        return r(coll) if callable(r) else r


@pytest.fixture(autouse=True)
def _types(monkeypatch):
    monkeypatch.setattr(adsk.fusion, "BRepBody", _Body, raising=False)
    monkeypatch.setattr(adsk.fusion, "BRepFace", _Face, raising=False)


def _wire(rev_result, tokens):
    comp = MakeComp()
    comp.features = types.SimpleNamespace(reverseNormalFeatures=_RevFeatures(rev_result))
    design = make_design(comp=comp, tokens=tokens)
    install(srn, design)
    return comp.features.reverseNormalFeatures


def _flip_result(coll):
    """A realistic feature: every input body's faces come back with isParamReversed toggled."""
    result_bodies, flipped = [], []
    for b in coll:
        ff = [_Face(reversed_=not f.isParamReversed) for f in b._faces]
        flipped.extend(ff)
        result_bodies.append(_Body(b.name, faces=ff))
    return _Feature(bodies=result_bodies, faces=flipped)


def _noop_result(coll):
    """A feature that changed nothing - the read-back must NOT confirm a flip."""
    result_bodies, same = [], []
    for b in coll:
        ff = [_Face(reversed_=f.isParamReversed) for f in b._faces]
        same.extend(ff)
        result_bodies.append(_Body(b.name, faces=ff))
    return _Feature(bodies=result_bodies, faces=same)


def test_confirms_flip_via_isparamreversed_readback():
    body = _Body("Srf1", faces=[_Face(False), _Face(False)])
    _wire(_flip_result, {"H1": body})
    out = payload(srn.reverse_normal_handler(bodies=["H1"]))
    assert out["reversed"] is True
    assert out["reversed_confirmed"] is True
    assert out["faces_total"] == 2
    assert out["reversed_before"] == 0 and out["reversed_after"] == 2


def test_noop_reported_honestly_not_confirmed():
    body = _Body("Srf1", faces=[_Face(False), _Face(False)])
    _wire(_noop_result, {"H1": body})
    res = srn.reverse_normal_handler(bodies=["H1"])
    assert res["isError"] is False            # honest ok, not an error
    out = payload(res)
    assert out["reversed_confirmed"] is False
    assert "did NOT confirm" in out["note"]


def test_all_input_bodies_handed_to_add():
    b1 = _Body("Srf1", faces=[_Face(False)])
    b2 = _Body("Srf2", faces=[_Face(True)])
    rev = _wire(_flip_result, {"H1": b1, "H2": b2})
    out = payload(srn.reverse_normal_handler(bodies=["H1", "H2"]))
    assert out["body_count"] == 2
    assert rev.calls and rev.calls[0].count == 2      # both bodies in the collection

def test_solid_body_rejected():
    solid = _Body("Block", is_solid=True, faces=[_Face(False)])
    _wire(_flip_result, {"H1": solid})
    res = srn.reverse_normal_handler(bodies=["H1"])
    msg = error_message(res)
    assert "SURFACE" in msg or "SOLID" in msg


def test_missing_bodies_rejected():
    _wire(_flip_result, {})
    res = srn.reverse_normal_handler(bodies=None)
    assert res["isError"] is True


def test_null_feature_is_error():
    body = _Body("Srf1", faces=[_Face(False)])
    _wire(lambda coll: None, {"H1": body})
    res = srn.reverse_normal_handler(bodies=["H1"])
    assert error_message(res)
    assert "no feature" in error_message(res)
