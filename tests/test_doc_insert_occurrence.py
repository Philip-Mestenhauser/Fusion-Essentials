"""Unit tests for ``insert_occurrence.py`` placement transform.

The cloud URN resolution needs live data; these pin the new placement/orientation logic added to the
handler — that x/y/z scales to cm, rotate_deg builds a rotation, and bad units/axis are rejected.
The DataFile resolution + component lookup are monkeypatched so the test stays offline.
"""

import json
from conftest import load_tool

io = load_tool("doc_insert_occurrence")


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
    monkeypatch.setattr(io._common, "design", lambda: object())
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


# ── _resolve_data_file: URN / web-URL identifier resolution ───────────────────

import base64


class _Data:
    """app.data.findFileById: returns a DataFile only for ids in `known`."""
    def __init__(self, known):
        self._known = dict(known)
        self.queried = []

    def findFileById(self, c):
        self.queried.append(c)
        return self._known.get(c)


def _set_data(monkeypatch, known):
    data = _Data(known)
    monkeypatch.setattr(io, "app", type("A", (), {"data": data})())
    return data


class TestResolveDataFile:
    def test_plain_urn_resolves_directly(self, monkeypatch):
        df = object()
        _set_data(monkeypatch, {"urn:adsk.wipprod:dm.lineage:abc": df})
        got, resolved = io._resolve_data_file("urn:adsk.wipprod:dm.lineage:abc")
        assert got is df and resolved == "urn:adsk.wipprod:dm.lineage:abc"

    def test_urn_extracted_from_surrounding_text(self, monkeypatch):
        df = object()
        urn = "urn:adsk.wipprod:dm.lineage:xYz123"
        _set_data(monkeypatch, {urn: df})
        # raw isn't itself a known id, but the embedded urn:adsk... token is
        got, resolved = io._resolve_data_file(f"some text {urn} trailing")
        assert got is df and resolved == urn

    def test_web_url_base64_segment_decoded(self, monkeypatch):
        urn = "urn:adsk.wipprod:dm.lineage:Zb64Decoded"
        # build a base64url segment that decodes to the urn (as a Fusion web URL embeds it)
        seg = base64.b64encode(urn.encode()).decode().replace('+', '-').replace('/', '_').rstrip('=')
        df = object()
        _set_data(monkeypatch, {urn: df})
        url = f"https://myhub.autodesk360.com/g/data/{seg}/something"
        got, resolved = io._resolve_data_file(url)
        assert got is df and resolved == urn

    def test_unresolvable_returns_none(self, monkeypatch):
        _set_data(monkeypatch, {})
        got, resolved = io._resolve_data_file("urn:adsk.nope:1")
        assert got is None and resolved is None


class TestB64UrlDecode:
    def test_roundtrip(self):
        raw = "urn:adsk:lineage:Hello"
        seg = base64.b64encode(raw.encode()).decode().replace('+', '-').replace('/', '_').rstrip('=')
        assert io._b64url_decode(seg) == raw

    def test_garbage_returns_none(self):
        assert io._b64url_decode("!!!notb64!!!") is None


# ── _find_component / _find_child_occurrence ──────────────────────────────────

class _FComp:
    def __init__(self, name, occurrences=()):
        self.name = name
        self.occurrences = list(occurrences)


class _FOcc:
    def __init__(self, name, comp_name):
        self.name = name
        self.component = _FComp(comp_name)


class _FRoot:
    def __init__(self, name="Root", all_occs=(), occurrences=()):
        self.name = name
        self.allOccurrences = list(all_occs)
        self.occurrences = list(occurrences)


class _FDesign:
    def __init__(self, root):
        self.rootComponent = root


class TestFindComponent:
    def test_empty_name_returns_root(self):
        root = _FRoot("Root")
        comp, desc = io._find_component(_FDesign(root), "")
        assert comp is root and "root" in desc.lower()

    def test_root_name_returns_root(self):
        root = _FRoot("Root")
        comp, desc = io._find_component(_FDesign(root), "Root")
        assert comp is root

    def test_match_by_occurrence_name(self):
        occ = _FOcc("Chassis:1", "Chassis")
        root = _FRoot("Root", all_occs=[occ])
        comp, desc = io._find_component(_FDesign(root), "Chassis:1")
        assert comp is occ.component and "Chassis" in desc

    def test_match_by_component_name(self):
        occ = _FOcc("inst:1", "Chassis")
        root = _FRoot("Root", all_occs=[occ])
        comp, desc = io._find_component(_FDesign(root), "Chassis")
        assert comp is occ.component

    def test_unknown_returns_none(self):
        root = _FRoot("Root", all_occs=[_FOcc("A:1", "A")])
        comp, desc = io._find_component(_FDesign(root), "Ghost")
        assert comp is None and desc is None


class TestFindChildOccurrence:
    def test_match_by_occurrence_name(self):
        occ = _FOcc("Wheel:1", "Wheel")
        comp = _FComp("Parent", occurrences=[occ])
        assert io._find_child_occurrence(comp, "Wheel:1") is occ

    def test_match_by_component_name(self):
        occ = _FOcc("inst:1", "Wheel")
        comp = _FComp("Parent", occurrences=[occ])
        assert io._find_child_occurrence(comp, "Wheel") is occ

    def test_no_match_returns_none(self):
        comp = _FComp("Parent", occurrences=[_FOcc("A:1", "A")])
        assert io._find_child_occurrence(comp, "Ghost") is None


# ── handler error gates that don't reach placement ────────────────────────────

class TestHandlerGates:
    def test_empty_document_id_errors(self):
        res = io.handler(document_id="")
        assert res["isError"] is True and "document_id" in res["message"]

    def test_no_active_design(self, monkeypatch):
        monkeypatch.setattr(io._common, "design", lambda: None)
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "No active design" in res["message"]

    def test_unresolvable_document_errors(self, monkeypatch):
        monkeypatch.setattr(io._common, "design", lambda: object())
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (None, None))
        res = io.handler(document_id="urn:nope")
        assert res["isError"] is True and "Could not resolve" in res["message"]

    def test_component_not_found_errors(self, monkeypatch):
        df = type("DF", (), {"name": "Part"})()
        monkeypatch.setattr(io._common, "design", lambda: object())
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, raw))
        monkeypatch.setattr(io, "_find_component", lambda d, n: (None, None))
        res = io.handler(document_id="urn:x", into_component="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_remove_existing_missing_errors(self, monkeypatch):
        comp = FakeComp()
        df = type("DF", (), {"name": "Part"})()
        monkeypatch.setattr(io._common, "design", lambda: object())
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, raw))
        monkeypatch.setattr(io, "_find_component", lambda d, n: (comp, "root component"))
        monkeypatch.setattr(io, "_find_child_occurrence", lambda c, n: None)
        res = io.handler(document_id="urn:x", remove_existing="OldPart")
        assert res["isError"] is True and "OldPart" in res["message"]

    def test_addByInsert_returns_nothing_errors(self, monkeypatch):
        comp = _install(monkeypatch)
        comp.occurrences.addByInsert = lambda *a: None
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "addByInsert returned nothing" in res["message"]

    def test_remove_existing_then_insert(self, monkeypatch):
        comp = _install(monkeypatch)
        removed = {"called": False}
        class _Old:
            name = "OldPart:1"
            def deleteMe(self_):
                removed["called"] = True
                return True
        monkeypatch.setattr(io, "_find_child_occurrence", lambda c, n: _Old())
        out = _payload(io.handler(document_id="urn:x", remove_existing="OldPart:1"))
        assert removed["called"] is True
        assert out["removed_occurrence"] == "OldPart:1"
