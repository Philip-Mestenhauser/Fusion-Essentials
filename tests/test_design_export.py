"""Unit tests for ``design_export.py`` — export a body/component/whole-design to a neutral CAD file.

Covers the format dispatch (step/iges/sat/stl), target resolution (handle / body name /
component name / whole design), path defaulting + extension handling, and that the right
ExportManager.create*Options call is used per format. No live Fusion — fakes mimic ExportManager.
"""

import json
from conftest import load_tool

dx = load_tool("design_export")


# ── fakes ────────────────────────────────────────────────────────────────────

class FakeBody:
    def __init__(self, name):
        self.name = name


class FakeBodies:
    def __init__(self, bodies):
        self._b = {b.name: b for b in bodies}
        self._list = bodies
    def itemByName(self, n):
        return self._b.get(n)
    @property
    def count(self):
        return len(self._list)
    def item(self, i):
        return self._list[i]


class FakeOccs:
    def __init__(self):
        self._l = []
    def itemByName(self, n):
        return None
    def __iter__(self):
        return iter(self._l)


class FakeComp:
    def __init__(self, name, bodies):
        self.name = name
        self.bRepBodies = FakeBodies(bodies)
        self.occurrences = FakeOccs()
        self.allOccurrences = []


class FakeExportManager:
    """Records which create*Options was called + with what geometry, and that execute ran."""
    def __init__(self):
        self.calls = []
        self.executed = None
    def _opt(self, kind, path, geom=None):
        rec = {"kind": kind, "path": path, "geom": geom}
        self.calls.append(rec)
        return rec
    def createSTEPExportOptions(self, path, geom=None):
        return self._opt("step", path, geom)
    def createIGESExportOptions(self, path, geom=None):
        return self._opt("iges", path, geom)
    def createSATExportOptions(self, path, geom=None):
        return self._opt("sat", path, geom)
    def createSTLExportOptions(self, geom, path):
        # STL signature is (geometry, filename) in the real API
        rec = {"kind": "stl", "path": path, "geom": geom}
        self.calls.append(rec)
        return rec
    def execute(self, opts):
        self.executed = opts
        return True


class FakeDesign:
    def __init__(self, comp, em):
        self.rootComponent = comp
        self.exportManager = em
        self._tokens = {}
    def findEntityByToken(self, t):
        e = self._tokens.get(t)
        return [e] if e is not None else []


def _install(bodies=None, comp_name="Root"):
    bodies = bodies if bodies is not None else [FakeBody("Body1")]
    comp = FakeComp(comp_name, bodies)
    em = FakeExportManager()
    design = FakeDesign(comp, em)
    dx.app = type("A", (), {"activeProduct": design})()
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    adsk.fusion.BRepBody = FakeBody
    return design, em, comp


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── format dispatch ──────────────────────────────────────────────────────────

class TestFormatDispatch:
    def test_step_uses_step_options(self, tmp_path):
        _, em, _ = _install()
        out = _payload(dx.handler(format="step", file_path=str(tmp_path / "p.step")))
        assert out["exported"] is True
        assert em.calls[-1]["kind"] == "step"
        assert em.executed is not None

    def test_iges_uses_iges_options(self, tmp_path):
        _, em, _ = _install()
        _payload(dx.handler(format="iges", file_path=str(tmp_path / "p.igs")))
        assert em.calls[-1]["kind"] == "iges"

    def test_sat_uses_sat_options(self, tmp_path):
        _, em, _ = _install()
        _payload(dx.handler(format="sat", file_path=str(tmp_path / "p.sat")))
        assert em.calls[-1]["kind"] == "sat"

    def test_stl_uses_stl_options(self, tmp_path):
        _, em, _ = _install()
        _payload(dx.handler(format="stl", file_path=str(tmp_path / "p.stl")))
        assert em.calls[-1]["kind"] == "stl"

    def test_unknown_format_errors(self, tmp_path):
        _install()
        res = dx.handler(format="dwg", file_path=str(tmp_path / "p.dwg"))
        assert res["isError"] is True and "format" in res["message"]


# ── target resolution ────────────────────────────────────────────────────────

class TestTargetResolution:
    def test_whole_design_when_no_target(self, tmp_path):
        _, em, comp = _install()
        out = _payload(dx.handler(format="step", file_path=str(tmp_path / "p.step")))
        # whole-design export passes the root component as the geometry
        assert em.calls[-1]["geom"] is comp
        assert "design" in out["target"].lower() or "root" in out["target"].lower()

    def test_body_by_name(self, tmp_path):
        _, em, _ = _install(bodies=[FakeBody("Widget")])
        out = _payload(dx.handler(format="step", target="Widget", file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"].name == "Widget"
        assert "Widget" in out["target"]

    def test_body_by_handle(self, tmp_path):
        design, em, _ = _install(bodies=[FakeBody("Body1")])
        h = "/v" + "X" * 70
        design._tokens[h] = FakeBody("FromHandle")
        out = _payload(dx.handler(format="step", target=h, file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"].name == "FromHandle"

    def test_missing_named_target_errors(self, tmp_path):
        _install(bodies=[FakeBody("Body1")])
        res = dx.handler(format="step", target="Nope", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True and "Nope" in res["message"]


# ── path handling ────────────────────────────────────────────────────────────

class TestPathHandling:
    def test_missing_path_errors(self):
        _install()
        res = dx.handler(format="step")
        assert res["isError"] is True and "file_path" in res["message"]

    def test_extension_auto_appended(self, tmp_path):
        _, em, _ = _install()
        p = str(tmp_path / "noext")
        out = _payload(dx.handler(format="step", file_path=p))
        # the path handed to the exporter ends with the format extension
        assert em.calls[-1]["path"].lower().endswith(".step")
        assert out["file_path"].lower().endswith(".step")
