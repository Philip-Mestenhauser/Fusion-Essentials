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


class FakeOcc:
    def __init__(self, name, full_path=None):
        self.name = name
        self.fullPathName = full_path or name


class FakeOccs:
    def __init__(self, occs=()):
        self._l = list(occs)
    def itemByName(self, n):
        return None
    @property
    def count(self):
        return len(self._l)
    def item(self, i):
        return self._l[i]
    def __iter__(self):
        return iter(self._l)


class FakeComp:
    def __init__(self, name, bodies, occurrences=()):
        self.name = name
        self.bRepBodies = FakeBodies(bodies)
        self.occurrences = FakeOccs(occurrences)
        self.allOccurrences = list(occurrences)


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
        # actually write a stub file so the handler's os.path.getsize/exists checks see it
        try:
            with open(opts["path"], "w") as f:
                f.write("stub")
        except Exception:
            pass
        return True


class FakeDesign:
    def __init__(self, comp, em):
        self.rootComponent = comp
        self.exportManager = em
        self._tokens = {}
    def findEntityByToken(self, t):
        e = self._tokens.get(t)
        return [e] if e is not None else []


def _install(bodies=None, comp_name="Root", occurrences=()):
    bodies = bodies if bodies is not None else [FakeBody("Body1")]
    comp = FakeComp(comp_name, bodies, occurrences)
    em = FakeExportManager()
    design = FakeDesign(comp, em)
    dx.app = type("A", (), {"activeProduct": design})()
    dx._common.app = dx.app
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

    def test_long_body_name_not_mistaken_for_handle(self, tmp_path):
        # PR-review #7: a 60+ char body NAME used to fall into the handle path (len>60 heuristic) and
        # fail to resolve. Resolution now tries _resolve_token_entity first (None for a non-token) and
        # falls through to the name lookup — so a long body name exports by NAME.
        long_name = "Left-Outrigger-Pivot-Bracket-Weldment-Subassembly-Body-Number-Seven"
        assert len(long_name) > 60
        _, em, _ = _install(bodies=[FakeBody(long_name)])
        out = _payload(dx.handler(format="step", target=long_name, file_path=str(tmp_path / "p.step")))
        assert em.calls[-1]["geom"].name == long_name
        assert long_name in out["target"]

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


# ── split_by_component (one file per top-level occurrence) ─────────────────────

class TestSplitByComponent:
    def test_one_file_per_occurrence(self, tmp_path):
        occs = [FakeOcc("Body:1"), FakeOcc("Cab:1"), FakeOcc("Wheels:1")]
        _, em, _ = _install(occurrences=occs)
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["split_by_component"] is True
        assert out["file_count"] == 3
        # each occurrence was the geometry handed to the exporter (one execute per part)
        geoms = [c["geom"].name for c in em.calls]
        assert set(geoms) == {"Body:1", "Cab:1", "Wheels:1"}

    def test_filenames_sanitized_and_extensioned(self, tmp_path):
        _, _, _ = _install(occurrences=[FakeOcc("Loader Arm:1")])
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        fp = out["files"][0]["file_path"]
        # ':1' instance suffix dropped, space -> '_', extension applied
        assert fp.replace("\\", "/").endswith("/Loader_Arm.stl")

    def test_duplicate_stems_disambiguated(self, tmp_path):
        # two instances whose sanitized stem collides must not overwrite each other
        _install(occurrences=[FakeOcc("Wheel:1"), FakeOcc("Wheel:2")])
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        paths = [f["file_path"] for f in out["files"]]
        assert len(set(paths)) == 2                       # distinct files
        assert any(p.endswith("Wheel.stl") for p in paths)
        assert any(p.endswith("Wheel_2.stl") for p in paths)

    def test_no_occurrences_errors(self, tmp_path):
        _install(occurrences=[])
        res = dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True)
        assert res["isError"] is True and "no top-level occurrences" in res["message"].lower()

    def test_partial_failure_records_failed_list(self, tmp_path):
        # one occurrence exports, one fails -> exported=true, file_count counts only the good one,
        # and the failures land under a 'failed' key (not silently dropped).
        good = FakeOcc("Good:1")
        bad = FakeOcc("Bad:1")
        _, em, _ = _install(occurrences=[good, bad])

        real_exec = em.execute
        def selective(opts):
            if "Bad" in opts["path"]:
                return False               # Fusion declines this one
            return real_exec(opts)
        em.execute = selective

        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["exported"] is True          # at least one succeeded
        assert out["file_count"] == 1
        assert [f["occurrence"] for f in out["files"]] == ["Good:1"]
        assert "failed" in out
        assert out["failed"][0]["occurrence"] == "Bad:1"

    def test_all_fail_exported_false(self, tmp_path):
        _, em, _ = _install(occurrences=[FakeOcc("A:1")])
        em.execute = lambda opts: False
        out = _payload(dx.handler(format="stl", file_path=str(tmp_path), split_by_component=True))
        assert out["exported"] is False and out["file_count"] == 0


# ── _sanitize (filename safety) ───────────────────────────────────────────────

class TestSanitize:
    def test_drops_instance_suffix(self):
        assert dx._sanitize("Loader Arm:1") == "Loader_Arm"

    def test_keeps_safe_chars(self):
        assert dx._sanitize("Part-A_1.v2") == "Part-A_1.v2"

    def test_swaps_illegal_chars(self):
        assert dx._sanitize("A/B\\C:1") == "A_B_C"

    def test_empty_becomes_part(self):
        assert dx._sanitize("") == "part"
        assert dx._sanitize(None) == "part"

    def test_all_illegal_becomes_part(self):
        # base reduces to all-underscore (still non-empty), so it stays underscores, not "part"
        assert dx._sanitize("***") == "___"


# ── _export_one (per-file write result) ───────────────────────────────────────

class TestExportOne:
    def test_stl_arg_order_is_geom_then_path(self):
        em = FakeExportManager()
        okk, err = dx._export_one(em, "createSTLExportOptions", True, "GEOM", "C:/out.stl")
        assert okk is True and err is None
        # STL records (geom, path); the call captured the geometry, not the path, as geom
        assert em.calls[-1]["geom"] == "GEOM" and em.calls[-1]["path"] == "C:/out.stl"

    def test_non_stl_arg_order_is_path_then_geom(self):
        em = FakeExportManager()
        dx._export_one(em, "createSTEPExportOptions", False, "GEOM", "C:/out.step")
        assert em.calls[-1]["geom"] == "GEOM" and em.calls[-1]["path"] == "C:/out.step"

    def test_execute_false_is_a_failure(self):
        em = FakeExportManager()
        em.execute = lambda opts: False
        okk, err = dx._export_one(em, "createSTEPExportOptions", False, "G", "p")
        assert okk is False and "nothing was written" in err

    def test_exception_captured_as_error_string(self):
        em = FakeExportManager()
        def boom(path, geom=None):
            raise RuntimeError("disk full")
        em.createSTEPExportOptions = boom
        okk, err = dx._export_one(em, "createSTEPExportOptions", False, "G", "p")
        assert okk is False and "disk full" in err


# ── _resolve_target ordering ──────────────────────────────────────────────────

class TestResolveTargetExtra:
    def test_handle_resolving_to_non_body_is_not_found(self, tmp_path):
        # a long token that resolves to something that is NOT a BRepBody -> (None) -> handler error
        design, _, _ = _install(bodies=[FakeBody("Body1")])
        h = "/v" + "Z" * 70
        design._tokens[h] = object()              # not a FakeBody (BRepBody)
        res = dx.handler(format="step", target=h, file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True and "not found" in res["message"].lower()

    def test_no_active_design_errors(self, tmp_path):
        # design() returns None -> the no-design error, not a crash
        dx._common.design = lambda: None
        res = dx.handler(format="step", file_path=str(tmp_path / "p.step"))
        assert res["isError"] is True and "no active design" in res["message"].lower()
