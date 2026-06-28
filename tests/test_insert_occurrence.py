"""Unit tests for ``insert_occurrence.py`` placement transform.

The cloud URN resolution needs live data; these pin the new placement/orientation logic added to the
handler — that x/y/z scales to cm, rotate_deg builds a rotation, and bad units/axis are rejected.
The DataFile resolution + component lookup are monkeypatched so the test stays offline.
"""

import json
from conftest import load_tool

io = load_tool("insert_occurrence")


class FakeMatrix:
    def __init__(self):
        self.translation = None
        self.rotation = None
    def setToRotation(self, angle, axis, origin):
        self.rotation = (angle, axis, origin)


class FakeOcc:
    name = "Part:1"
    isReferencedComponent = True


class FakeOccurrences:
    def __init__(self):
        self.last_transform = None
    def addByInsert(self, data_file, transform, as_ref):
        self.last_transform = transform
        return FakeOcc()


class FakeComp:
    def __init__(self):
        self.occurrences = FakeOccurrences()


def _install(monkeypatch):
    comp = FakeComp()
    df = type("DF", (), {"name": "Part"})()
    monkeypatch.setattr(io, "_design", lambda: object())
    monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, raw))
    monkeypatch.setattr(io, "_find_component", lambda design, name: (comp, "root component"))
    import adsk.core
    adsk.core.Matrix3D.create = staticmethod(FakeMatrix)
    adsk.core.Vector3D.create = staticmethod(lambda x, y, z: ("vec", x, y, z))
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    return comp


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestPlacement:
    def test_default_identity(self, monkeypatch):
        comp = _install(monkeypatch)
        _payload(io.handler(document_id="urn:x"))
        assert comp.occurrences.last_transform.translation is None

    def test_position_scales_to_cm(self, monkeypatch):
        comp = _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x", x=10, y=0, z=5, units="mm"))
        t = comp.occurrences.last_transform.translation
        assert abs(t[1] - 1.0) < 1e-9 and abs(t[3] - 0.5) < 1e-9
        assert out["placed_at"]["x"] == 10

    def test_rotation_built(self, monkeypatch):
        comp = _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x", rotate_deg=90, rotate_axis="y"))
        assert comp.occurrences.last_transform.rotation is not None
        assert out["rotate_deg"] == 90

    def test_bad_units(self, monkeypatch):
        _install(monkeypatch)
        res = io.handler(document_id="urn:x", x=5, units="furlongs")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_bad_rotate_axis(self, monkeypatch):
        _install(monkeypatch)
        res = io.handler(document_id="urn:x", rotate_deg=45, rotate_axis="w")
        assert res["isError"] is True and "rotate_axis" in res["message"]
