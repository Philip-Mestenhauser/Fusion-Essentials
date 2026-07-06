"""Unit tests for ``doc_insert_occurrence.py`` placement transform + occurrence resolution.

The cloud URN resolution needs live data; these pin the placement/orientation logic (x/y/z scales
to cm, rotate_deg builds a rotation, bad units/axis are rejected), plus into_component/
remove_existing resolution via the shared OccurrenceRef kind (fullPathName/name-preferring,
ambiguity REFUSED - never the wrong instance). The DataFile resolution is monkeypatched so the
test stays offline; occurrence resolution goes through the real _inputs kind against a fake design
(dual seam: both io._common.design and io._inputs._common.design point at the same fake).
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


class FakeOccurrences:
    def __init__(self):
        self.last_transform = None
        self.insert_result = "default"

    def addByInsert(self, data_file, transform, as_ref):
        self.last_transform = transform
        if self.insert_result == "default":
            return type("NewOcc", (), {"name": "Part:1", "isReferencedComponent": as_ref})()
        return self.insert_result


class FakeComp:
    def __init__(self, name="Root"):
        self.name = name
        self.occurrences = FakeOccurrences()


class FakeOcc:
    """A fake assembly Occurrence: name + fullPathName + .component + deleteMe()."""
    def __init__(self, name, component=None, full_path=None, delete_returns=True):
        self.name = name
        self.fullPathName = full_path or name
        self.component = component
        self._delete_returns = delete_returns
        self.deleted = False

    def deleteMe(self):
        self.deleted = True
        return self._delete_returns


class FakeRoot:
    def __init__(self, comp, occurrences=()):
        self.name = comp.name
        self._comp = comp
        self.allOccurrences = list(occurrences)


class FakeDesign:
    def __init__(self, root_comp, occurrences=()):
        self.rootComponent = FakeRoot(root_comp, occurrences)
        # rootComponent must behave like the real root Component too (has .occurrences)
        self.rootComponent.occurrences = root_comp.occurrences
        self.rootComponent.name = root_comp.name


def _install(monkeypatch, occurrences=(), root_comp=None):
    """Point BOTH design seams (the tool's and the shared _inputs resolver's) at one fake design."""
    root_comp = root_comp or FakeComp("Root")
    design = FakeDesign(root_comp, occurrences)
    monkeypatch.setattr(io._common, "design", lambda: design)
    monkeypatch.setattr(io._inputs._common, "design", lambda: design)
    df = type("DF", (), {"name": "Part"})()
    monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (df, raw, [raw]))
    import adsk.core
    monkeypatch.setattr(adsk.core.Matrix3D, "create", staticmethod(FakeMatrix))
    monkeypatch.setattr(adsk.core.Vector3D, "create", staticmethod(lambda x, y, z: ("vec", x, y, z)))
    monkeypatch.setattr(adsk.core.Point3D, "create", staticmethod(lambda x, y, z: ("pt", x, y, z)))
    return design, root_comp


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


class TestPlacement:
    def test_default_identity(self, monkeypatch):
        design, root_comp = _install(monkeypatch)
        _payload(io.handler(document_id="urn:x"))
        assert root_comp.occurrences.last_transform.translation is None

    def test_invalid_inserted_occurrence_bites(self, monkeypatch):
        # addByInsert returns an occurrence object that reads isValid=false -> error, not ok
        design, root_comp = _install(monkeypatch)
        root_comp.occurrences.insert_result = type(
            "BadOcc", (), {"name": "Part:1", "isValid": False})()
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True
        assert "isValid=false" in res["message"]

    def test_position_scales_to_cm(self, monkeypatch):
        design, root_comp = _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x", x=10, y=0, z=5, units="mm"))
        t = root_comp.occurrences.last_transform.translation
        assert abs(t[1] - 1.0) < 1e-9 and abs(t[3] - 0.5) < 1e-9
        assert out["placed_at"]["x"] == 10

    def test_rotation_built(self, monkeypatch):
        design, root_comp = _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x", rotate_deg=90, rotate_axis="y"))
        assert root_comp.occurrences.last_transform.rotation is not None
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
    monkeypatch.setattr(io._data_common, "app", type("A", (), {"data": data})())
    return data


class TestResolveDataFile:
    def test_plain_urn_resolves_directly(self, monkeypatch):
        df = object()
        _set_data(monkeypatch, {"urn:adsk.wipprod:dm.lineage:abc": df})
        got, resolved, candidates = io._resolve_data_file("urn:adsk.wipprod:dm.lineage:abc")
        assert got is df and resolved == "urn:adsk.wipprod:dm.lineage:abc"

    def test_urn_extracted_from_surrounding_text(self, monkeypatch):
        df = object()
        urn = "urn:adsk.wipprod:dm.lineage:xYz123"
        _set_data(monkeypatch, {urn: df})
        # raw isn't itself a known id, but the embedded urn:adsk... token is
        got, resolved, candidates = io._resolve_data_file(f"some text {urn} trailing")
        assert got is df and resolved == urn

    def test_web_url_base64_segment_decoded(self, monkeypatch):
        urn = "urn:adsk.wipprod:dm.lineage:Zb64Decoded"
        # build a base64url segment that decodes to the urn (as a Fusion web URL embeds it)
        seg = base64.b64encode(urn.encode()).decode().replace('+', '-').replace('/', '_').rstrip('=')
        df = object()
        _set_data(monkeypatch, {urn: df})
        url = f"https://myhub.autodesk360.com/g/data/{seg}/something"
        got, resolved, candidates = io._resolve_data_file(url)
        assert got is df and resolved == urn

    def test_unresolvable_returns_none(self, monkeypatch):
        _set_data(monkeypatch, {})
        got, resolved, candidates = io._resolve_data_file("urn:adsk.nope:1")
        assert got is None and resolved is None


class TestB64UrlDecode:
    def test_roundtrip(self):
        raw = "urn:adsk:lineage:Hello"
        seg = base64.b64encode(raw.encode()).decode().replace('+', '-').replace('/', '_').rstrip('=')
        assert io._b64url_decode(seg) == raw

    def test_garbage_returns_none(self):
        assert io._b64url_decode("!!!notb64!!!") is None


# ── into_component / remove_existing via the shared OccurrenceRef kind ────────

class TestIntoComponent:
    def test_empty_uses_root(self, monkeypatch):
        design, root_comp = _install(monkeypatch)
        out = _payload(io.handler(document_id="urn:x"))
        assert out["into_component"] == "root component"

    def test_named_occurrence_resolves_its_component(self, monkeypatch):
        chassis_comp = FakeComp("Chassis")
        occ = FakeOcc("Chassis:1", component=chassis_comp)
        design, root_comp = _install(monkeypatch, occurrences=[occ])
        out = _payload(io.handler(document_id="urn:x", into_component="Chassis:1"))
        assert "Chassis" in out["into_component"]
        assert chassis_comp.occurrences.last_transform is not None

    def test_unknown_occurrence_errors(self, monkeypatch):
        _install(monkeypatch, occurrences=[FakeOcc("A:1", component=FakeComp("A"))])
        res = io.handler(document_id="urn:x", into_component="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_ambiguous_into_component_refused_not_first_match(self, monkeypatch):
        # two instances share local name "Bolt:1" under different sub-assemblies — a bare "Bolt"
        # substring must ERROR (naming both fullPathNames), NOT silently grab the first.
        a = FakeOcc("Bolt:1", component=FakeComp("Bolt"), full_path="Sub-A:1+Bolt:1")
        b = FakeOcc("Bolt:1", component=FakeComp("Bolt"), full_path="Sub-B:1+Bolt:1")
        _install(monkeypatch, occurrences=[a, b])
        res = io.handler(document_id="urn:x", into_component="Bolt")
        assert res["isError"] is True
        assert "ambiguous" in res["message"].lower()
        assert "Sub-A:1+Bolt:1" in res["message"] and "Sub-B:1+Bolt:1" in res["message"]


class TestRemoveExisting:
    def test_missing_errors(self, monkeypatch):
        _install(monkeypatch)
        res = io.handler(document_id="urn:x", remove_existing="OldPart")
        assert res["isError"] is True and "OldPart" in res["message"]

    def test_removes_then_inserts(self, monkeypatch):
        old = FakeOcc("OldPart:1", component=FakeComp("OldPart"))
        design, root_comp = _install(monkeypatch, occurrences=[old])
        out = _payload(io.handler(document_id="urn:x", remove_existing="OldPart:1"))
        assert old.deleted is True
        assert out["removed_occurrence"] == "OldPart:1"

    def test_delete_returns_false_errors(self, monkeypatch):
        old = FakeOcc("OldPart:1", component=FakeComp("OldPart"), delete_returns=False)
        _install(monkeypatch, occurrences=[old])
        res = io.handler(document_id="urn:x", remove_existing="OldPart:1")
        assert res["isError"] is True and "OldPart:1" in res["message"]

    def test_ambiguous_remove_existing_refused(self, monkeypatch):
        a = FakeOcc("Wheel:1", component=FakeComp("Wheel"), full_path="Sub-A:1+Wheel:1")
        b = FakeOcc("Wheel:1", component=FakeComp("Wheel"), full_path="Sub-B:1+Wheel:1")
        _install(monkeypatch, occurrences=[a, b])
        res = io.handler(document_id="urn:x", remove_existing="Wheel")
        assert res["isError"] is True and "ambiguous" in res["message"].lower()
        assert a.deleted is False and b.deleted is False


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
        monkeypatch.setattr(io, "_resolve_data_file", lambda raw: (None, None, []))
        res = io.handler(document_id="urn:nope")
        assert res["isError"] is True and "Could not resolve" in res["message"]

    def test_addByInsert_returns_nothing_errors(self, monkeypatch):
        design, root_comp = _install(monkeypatch)
        root_comp.occurrences.insert_result = None
        res = io.handler(document_id="urn:x")
        assert res["isError"] is True and "addByInsert returned nothing" in res["message"]
