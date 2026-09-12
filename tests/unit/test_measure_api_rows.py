# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The measurement registry's shape: every ROW composes into a script Fusion can run.

A row's body is source text that only ever executes inside live Fusion, so a syntax error in one
reaches the run as an ERROR row hours later - and a duplicate id silently overwrites a claim's
ledger cell. Both are decidable offline, against the same _compose the runner calls.
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
from types import SimpleNamespace

import adsk.core
import adsk.core as adsk_core
import adsk.cam as adsk_cam
import adsk.fusion
import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import measure_api  # noqa: E402


class TestRowRegistry:
    def test_every_row_composes_into_compilable_python(self):
        broken = []
        for row in measure_api.ROWS:
            try:
                compile(measure_api._compose(row), row["id"], "exec")
            except SyntaxError as e:
                broken.append(f"{row['id']}: line {e.lineno}: {e.msg}")
        assert not broken, (
            "measurement row bodies that do not compile - they would reach live Fusion as ERROR "
            "rows:\n  " + "\n  ".join(broken))

    def test_row_ids_are_unique(self):
        seen, dupes = set(), []
        for row in measure_api.ROWS:
            if row["id"] in seen:
                dupes.append(row["id"])
            seen.add(row["id"])
        assert not dupes, "duplicate measurement row ids: " + ", ".join(sorted(set(dupes)))

    def test_every_row_carries_a_claim_and_an_encoding(self):
        # the two columns write_ledger publishes - a row missing either lands a blank ledger cell
        thin = [row["id"] for row in measure_api.ROWS
                if not str(row.get("claim", "")).strip()
                or not str(row.get("encoded_in", "")).strip()]
        assert not thin, "rows with an empty claim or encoded_in: " + ", ".join(thin)


class _Items:
    def __init__(self, values):
        self.values = list(values)
        self.count = len(self.values)

    def item(self, index):
        return self.values[index]


class _EllipseGeometry:
    curveType = adsk.core.Curve3DTypes.Ellipse3DCurveType

    def __init__(self, center, major=2.0, minor=1.0):
        self.center = SimpleNamespace(x=center[0], y=center[1], z=center[2])
        self.majorRadius = major
        self.minorRadius = minor


def _run_ellipse_row(caps):
    ellipse_geometries = [_EllipseGeometry(*cap) for cap in caps]
    solid = SimpleNamespace(edges=_Items(
        [SimpleNamespace(geometry=geometry) for geometry in ellipse_geometries]))
    extrudes = SimpleNamespace(addSimple=lambda *_args: SimpleNamespace(bodies=_Items([solid])))
    sketch = SimpleNamespace(
        sketchCurves=SimpleNamespace(sketchEllipses=SimpleNamespace(add=lambda *_args: None)),
        profiles=_Items([object()]))
    root = SimpleNamespace(
        xYConstructionPlane=object(), sketches=SimpleNamespace(add=lambda _plane: sketch),
        features=SimpleNamespace(extrudeFeatures=extrudes))
    design = SimpleNamespace(rootComponent=root)
    closed = []
    document = SimpleNamespace(
        products=SimpleNamespace(itemByProductType=lambda _kind: design),
        close=lambda save: closed.append(save))
    emitted = []
    row = next(row for row in measure_api.ROWS if row["id"] == "shape-dump-ellipse3d")
    exec(textwrap.dedent(row["body"]), {
        "adsk": adsk,
        "app": SimpleNamespace(documents=SimpleNamespace(add=lambda _kind: document)),
        "dump_shape": lambda _label, obj: len(dir(obj)),
        "emit": lambda passed, detail: emitted.append((passed, detail)),
    })
    assert closed == [False]
    assert len(emitted) == 1
    return emitted[0]


@pytest.mark.parametrize(("caps", "expected"), [
    ((((0.0, 0.0, 1.0), 2.0, 1.0), ((0.0, 0.0, 0.0), 2.0, 1.0)), True),
    ((((0.0, 0.0, 0.0), 2.0, 1.0), ((0.0, 0.0, 1.0), 2.0, 1.0)), True),
    ((((0.0, 0.0, 0.0), 2.0, 1.0), ((-1e-12, 0.0, 1.0), 2.0, 1.0)), True),
    ((((0.0, 0.0, 0.0), 2.0, 1.0),), False),
    ((((0.0, 0.0, 0.0), 2.0, 1.0), ((0.0, 0.0, 1.0), 2.0, 0.5)), False),
])
def test_ellipse_row_checks_both_cap_curves_without_edge_order(caps, expected):
    passed, detail = _run_ellipse_row(caps)
    assert passed is expected
    assert "ellipse edges=" in detail


class _OutOfRangeCollection:
    def __init__(self, outcome, count):
        self.outcome = outcome
        self.count = count
        self.calls = []

    def item(self, index):
        self.calls.append(index)
        assert index == 9999
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("row_id", ["item-oor-brepbodies", "item-oor-sketches"])
@pytest.mark.parametrize(("outcome", "count", "expected", "called"), [
    (RuntimeError("native range error"), 0, True, True),
    (ValueError("wrong error"), 0, False, True),
    (None, 0, False, True),
    (RuntimeError("boundary range error"), 9999, True, True),
    (RuntimeError("must not be called"), 10000, False, False),
])
def test_collection_out_of_range_rows_require_a_caught_runtime_error(
        row_id, outcome, count, expected, called):
    collection = _OutOfRangeCollection(outcome, count)
    root = SimpleNamespace(bRepBodies=collection, sketches=collection)
    design = SimpleNamespace(rootComponent=root)
    products = SimpleNamespace(itemByProductType=lambda kind: (
        design if kind == "DesignProductType" else None))
    app = SimpleNamespace(activeDocument=SimpleNamespace(products=products))
    adsk = SimpleNamespace(fusion=SimpleNamespace(
        Design=SimpleNamespace(cast=lambda product: product)))
    emitted = []
    row = next(row for row in measure_api.ROWS if row["id"] == row_id)
    exec(textwrap.dedent(row["body"]), {
        "adsk": adsk,
        "app": app,
        "emit": lambda passed, detail: emitted.append((passed, detail)),
    })
    assert row["read_only"] is True
    assert "need_box" not in row and "expect" not in row
    assert len(emitted) == 1
    assert emitted[0][0] is expected
    assert collection.calls == ([9999] if called else [])


def _execute_row_body(body, scope):
    exec("def _row():\n" + textwrap.indent(textwrap.dedent(body), "    "), scope)
    scope["_row"]()


class _ClosedDocument:
    def __init__(self, close_mode="ok", name_mode="runtime"):
        self.close_mode = close_mode
        self.name_mode = name_mode
        self.closed = False
        self.close_calls = 0

    @property
    def isValid(self):
        return not self.closed

    @property
    def name(self):
        if self.name_mode == "runtime":
            raise RuntimeError("An API Object refers to a deleted Object")
        if self.name_mode == "wrong":
            raise ValueError("wrong read")
        if self.name_mode == "wrong_runtime":
            raise RuntimeError("different runtime failure")
        return "closed"

    def close(self, _save):
        self.close_calls += 1
        if self.close_mode == "raise":
            raise RuntimeError("close failed")
        if self.close_mode == "raise_once" and self.close_calls == 1:
            raise RuntimeError("close failed once")
        self.closed = True


@pytest.mark.parametrize(("close_mode", "name_mode", "expected"), [
    ("ok", "runtime", True),
    ("ok", "wrong", False),
    ("ok", "value", False),
    ("ok", "wrong_runtime", False),
    ("raise_once", "runtime", False),
    ("raise", "runtime", False),
])
def test_closed_document_row_requires_cleanup_and_runtime_error(close_mode, name_mode, expected):
    document = _ClosedDocument(close_mode, name_mode)
    adsk = SimpleNamespace(core=SimpleNamespace(
        DocumentTypes=adsk_core.DocumentTypes))
    app = SimpleNamespace(documents=SimpleNamespace(add=lambda _kind: document))
    emitted = []
    row = next(row for row in measure_api.ROWS if row["id"] == "closed-document-name-raises")
    _execute_row_body(row["body"], {
        "adsk": adsk, "app": app,
        "emit": lambda passed, detail: emitted.append((passed, detail)),
    })
    assert emitted[0][0] is expected
    assert row["read_only"] is True and "expect" not in row
    assert document.close_calls == (1 if close_mode == "ok" else 2)
    if close_mode == "raise_once":
        assert document.isValid is False
    if not expected:
        assert emitted[0][1]


class _TemplateURL:
    leafName = "owned-template.f3d"
    def toString(self):
        return "local://owned-template.f3d"


class _TemplateLibrary:
    def __init__(self, template_mode="runtime", delete_mode="ok"):
        self.template_mode = template_mode
        self.delete_mode = delete_mode
        self.url = _TemplateURL()
        self.asset_present = False
        self.delete_calls = 0

    def urlByLocation(self, _location):
        return "local"

    def importTemplate(self, _template, _local):
        self.asset_present = True
        return self.url

    def deleteAsset(self, _url):
        self.delete_calls += 1
        if self.delete_mode == "raise":
            raise RuntimeError("delete failed")
        if self.delete_mode == "false":
            return False
        self.asset_present = False
        return True

    def childAssetURLs(self, _local):
        return [self.url] if self.asset_present else []

    def templateAtURL(self, _url):
        if self.template_mode == "runtime":
            raise RuntimeError("3 : Given URL does not point to a template")
        if self.template_mode == "wrong":
            raise ValueError("wrong exception")
        if self.template_mode == "wrong_runtime":
            raise RuntimeError("different runtime failure")
        return None


@pytest.mark.parametrize(("template_mode", "delete_mode", "expected"), [
    ("runtime", "ok", True),
    ("wrong", "ok", False),
    ("wrong_runtime", "ok", False),
    ("return", "ok", False),
    ("runtime", "false", False),
    ("runtime", "raise", False),
])
def test_deleted_template_row_requires_exact_exception_and_cleanup(
        template_mode, delete_mode, expected):
    library = _TemplateLibrary(template_mode, delete_mode)
    template = SimpleNamespace(name=None)
    operation = object()
    setup = SimpleNamespace(name="MeasureSetup", allOperations=[operation])
    cam = SimpleNamespace(setups=SimpleNamespace(count=1, item=lambda _index: setup))
    products = SimpleNamespace(itemByProductType=lambda kind: cam)
    app = SimpleNamespace(activeDocument=SimpleNamespace(products=products))
    adsk = SimpleNamespace(
        cam=SimpleNamespace(
            CAM=SimpleNamespace(cast=lambda product: product),
            Operation=SimpleNamespace(cast=lambda operation: operation),
            CAMTemplate=SimpleNamespace(
                createFromOperations=lambda _ops: template,
                cast=lambda result: result),
            CAMManager=SimpleNamespace(get=lambda: SimpleNamespace(
                libraryManager=SimpleNamespace(templateLibrary=library))),
            LibraryLocations=adsk_cam.LibraryLocations))
    emitted = []
    row = next(row for row in measure_api.ROWS
               if row["id"] == "cam-templateaturl-raises-on-deleted-url")
    _execute_row_body(row["body"], {
        "adsk": adsk, "app": app,
        "emit": lambda passed, detail: emitted.append((passed, detail)),
    })
    assert emitted[0][0] is expected
    assert row["read_only"] is True and "expect" not in row
    assert library.asset_present is (delete_mode != "ok")
    assert library.delete_calls == (1 if delete_mode == "ok" else 2)
    if not expected:
        assert "local://owned-template.f3d" in emitted[0][1]


_METRIC_DESIGNATIONS = ("M5x0.8", "M10x1.5", "M6x1")
_METRIC_TYPES = ("ANSI Metric M Profile", "GB Metric profile", "ISO Metric profile")
_UNC_DESIGNATION = "1/4-20 UNC"
_UNC_TYPE = "ANSI Unified Screw Threads"


class _ThreadQuery:
    """ThreadDataQuery double for the unmeasured query surface used by this row."""

    def __init__(self, classes):
        self.classes = classes
        self.allThreadTypes = list(_METRIC_TYPES) + [_UNC_TYPE]

    def allSizes(self, thread_type):
        return ["size"]

    def allDesignations(self, thread_type, _size):
        return [desig for desig, carried_by in self.classes if carried_by == thread_type]

    def allClasses(self, _internal, thread_type, designation):
        return list(self.classes.get((designation, thread_type), ()))


class _ThreadFeatures:
    """ThreadFeatures double for its unmeasured thread-info creation surface."""

    def __init__(self, query):
        self.threadDataQuery = query
        self.create_calls = []

    def createThreadInfo(self, _internal, thread_type, designation, thread_class):
        allowed = set(self.threadDataQuery.allClasses(False, thread_type, designation))
        assert thread_class in allowed, (
            f"createThreadInfo received noncommon class {thread_class!r} for {thread_type!r}")
        self.create_calls.append((thread_type, designation, thread_class))
        return SimpleNamespace(
            majorDiameter=1.0, minorDiameter=0.8, pitchDiameter=0.9,
            threadPitch=0.1, threadAngle=60.0)


def _thread_classes(per_type):
    classes = {(_UNC_DESIGNATION, _UNC_TYPE): ("2A",)}
    for designation in _METRIC_DESIGNATIONS:
        for thread_type, carried in zip(_METRIC_TYPES, per_type):
            classes[(designation, thread_type)] = tuple(carried)
    return classes


def _run_thread_identity_row(classes):
    row = next(row for row in measure_api.ROWS
               if row["id"] == "thread-designation-multi-type-identity")
    query = _ThreadQuery(classes)
    features = _ThreadFeatures(query)
    des = SimpleNamespace(rootComponent=SimpleNamespace(
        features=SimpleNamespace(threadFeatures=features)))
    emitted = []
    exec(textwrap.dedent(row["body"]), {
        "des": des,
        "emit": lambda passed, detail: emitted.append((passed, detail)),
    })
    assert len(emitted) == 1
    return emitted[0], features.create_calls


class TestThreadDesignationMultiTypeIdentity:
    def test_disjoint_early_carriers_are_not_reset_by_a_later_carrier(self):
        (passed, detail), calls = _run_thread_identity_row(
            _thread_classes((("ClassA",), ("ClassB",), ("ClassC",))))
        assert passed is False
        assert calls == []
        assert all(designation in detail for designation in _METRIC_DESIGNATIONS)

    def test_a_truly_shared_class_is_used_for_every_carrier(self):
        (passed, _detail), calls = _run_thread_identity_row(
            _thread_classes((("Shared", "A"), ("Shared", "B"), ("Shared", "C"))))
        assert passed is True
        assert len(calls) == 9
        assert {call[2] for call in calls} == {"Shared"}

    def test_an_empty_first_carrier_keeps_the_intersection_empty(self):
        (passed, _detail), calls = _run_thread_identity_row(
            _thread_classes(((), ("Shared",), ("Shared",))))
        assert passed is False
        assert calls == []

    def test_absent_metric_carriers_fail_without_creating_thread_info(self):
        classes = {(_UNC_DESIGNATION, _UNC_TYPE): ("2A",)}
        (passed, detail), calls = _run_thread_identity_row(classes)
        assert passed is False
        assert calls == []
        assert all(designation in detail for designation in _METRIC_DESIGNATIONS)


class TestDxfFatalProbe:
    @pytest.fixture
    def dxf_probe(self):
        row = next(row for row in measure_api.ROWS
                   if row["id"] == "dxf-sketch-options-units-read-is-fatal")
        marker = {
            "row_id": row["id"], "nonce": "n", "scratch": "session:s",
            "source_sha256": "h", "phase": "getter_ready",
            "options_object_type": "adsk::fusion::DXFSketchExportOptions",
            "curve_count": 1,
        }
        return row, marker

    def test_exact_fatal_boundary_passes(self, dxf_probe):
        row, marker = dxf_probe
        status, detail = measure_api._judge_dxf_fatal(
            marker, True,
            "Traceback\nRuntimeError: 3 : Distance unit is not supported by DXF. "
            "Please select a different unit\n",
            row["id"], "n", "session:s", "h")
        assert status == "PASS"
        assert "exact_fatal_getter" in detail

    def test_wrong_boundary_and_error_have_specific_refusals(self, dxf_probe):
        row, marker = dxf_probe
        cases = [
            (None, True, "RuntimeError: 3 : Distance unit is not supported by DXF. "
             "Please select a different unit", "missing_or_malformed_marker", "ERROR"),
            ({**marker, "phase": "getter_returned"}, True,
             "RuntimeError: 3 : Distance unit is not supported by DXF. Please select a different unit",
             "getter_did_not_abort", "FAIL"),
            ({**marker, "phase": "getter_caught"}, True,
             "RuntimeError: 3 : Distance unit is not supported by DXF. Please select a different unit",
             "getter_did_not_abort", "FAIL"),
            ({**marker, "nonce": "other"}, True,
             "RuntimeError: 3 : Distance unit is not supported by DXF. Please select a different unit",
             "marker_identity_mismatch", "ERROR"),
            (marker, True,
             "wrapper: RuntimeError: 3 : Distance unit is not supported by DXF. "
             "Please select a different unit",
             "different_native_error", "ERROR"),
            (marker, False, "printed no error", "no_channel_error", "FAIL"),
        ]
        for candidate, is_error, payload, cause, expected_status in cases:
            status, detail = measure_api._judge_dxf_fatal(
                candidate, is_error, payload, row["id"], "n", "session:s", "h")
            assert status == expected_status
            assert cause in detail

    def test_marker_requires_measured_options_and_curve(self, dxf_probe):
        row, marker = dxf_probe
        for field, value in (("options_object_type", "other"), ("curve_count", 2)):
            candidate = dict(marker)
            candidate[field] = value
            status, detail = measure_api._judge_dxf_fatal(
                candidate, True,
                "RuntimeError: 3 : Distance unit is not supported by DXF. "
                "Please select a different unit",
                row["id"], "n", "session:s", "h")
            assert status == "ERROR"
            assert ("options_type_unproven" in detail
                    or "one_curve_unproven" in detail)

    def test_measure_helper_binds_marker_and_removes_temp_probe(
            self, monkeypatch, dxf_probe):
        row, _marker = dxf_probe
        probe_dir = tempfile.mkdtemp(prefix="fe_measure_dxf_fatal_quote_'")
        seen = {}
        error_line = ('RuntimeError: 3 : Distance unit is not supported by DXF. '
                      'Please select a different unit')

        def fake_call(_tool, args):
            assert args.get('read_only') in (None, False)
            script = args['script']
            marker_path = ast.literal_eval(
                re.search(r'_probe_path = (.+)', script).group(1))
            nonce = ast.literal_eval(
                re.search(r'_probe_nonce = (.+)', script).group(1))
            scratch = ast.literal_eval(
                re.search(r'_probe_scratch = (.+)', script).group(1))
            source_sha = ast.literal_eval(
                re.search(r'_probe_source_sha = (.+)', script).group(1))
            assert marker_path == os.path.join(probe_dir, 'marker.json')
            compile(script, 'instrumented-dxf', 'exec')
            seen['probe_dir'] = os.path.dirname(marker_path)
            with open(marker_path, 'w', encoding='utf-8') as fh:
                json.dump({'row_id': row['id'], 'nonce': nonce, 'scratch': scratch,
                           'source_sha256': source_sha, 'phase': 'getter_ready',
                           'options_object_type': 'adsk::fusion::DXFSketchExportOptions',
                           'curve_count': 1}, fh)
            return True, error_line

        monkeypatch.setattr(measure_api.tempfile, 'mkdtemp', lambda prefix: probe_dir)
        monkeypatch.setattr(measure_api, 'call', fake_call)
        status, detail = measure_api._measure_dxf_units(row, 'session:s')
        assert status == 'PASS'
        assert 'exact_fatal_getter' in detail
        assert not os.path.exists(seen['probe_dir'])

    def test_transport_failure_still_cleans_probe(self, monkeypatch, dxf_probe):
        row, _marker = dxf_probe
        probe_dir = tempfile.mkdtemp(prefix='fe_measure_dxf_fatal_transport_')
        monkeypatch.setattr(measure_api.tempfile, 'mkdtemp', lambda prefix: probe_dir)
        monkeypatch.setattr(measure_api, 'call',
                            lambda *_args: (_ for _ in ()).throw(RuntimeError('transport')))
        status, detail = measure_api._measure_dxf_units(row, 'session:s')
        assert status == 'ERROR'
        assert 'transport_exception' in detail
        assert not os.path.exists(probe_dir)

    def test_probe_cleanup_failure_is_error(self, monkeypatch, dxf_probe):
        row, _marker = dxf_probe
        probe_dir = tempfile.mkdtemp(prefix='fe_measure_dxf_fatal_cleanup_')
        real_rmtree = shutil.rmtree
        monkeypatch.setattr(measure_api.tempfile, 'mkdtemp', lambda prefix: probe_dir)
        monkeypatch.setattr(measure_api, 'call',
                            lambda *_args: (_ for _ in ()).throw(RuntimeError('transport')))
        monkeypatch.setattr(measure_api.shutil, 'rmtree',
                            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('cleanup')))
        try:
            status, detail = measure_api._measure_dxf_units(row, 'session:s')
            assert status == 'ERROR'
            assert 'probe_cleanup_failed' in detail
            assert 'transport_exception' in detail
            assert os.path.isdir(probe_dir)
        finally:
            real_rmtree(probe_dir, ignore_errors=True)
