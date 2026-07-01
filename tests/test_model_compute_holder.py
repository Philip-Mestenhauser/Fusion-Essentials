"""Unit tests for ``model_compute_holder.py`` + the pure half of ``_holder.py``.

The geometry reduction (get_tool_profile and its cylindrical-coordinate helpers) needs a full
body-of-revolution BRep and is covered by live validation, not mocks — reconstructing a faithful
faces/edges/Cone/Cylinder graph in a fake would test the fake, not the code. What IS pure logic and
worth pinning here:

  1. _holder.build_holder_data — the cm→mm segment conversion (height ×10, diameter = radius ×10×2),
     the holder JSON shape (type='holder', millimeters), and metadata pass-through. A wrong unit
     factor here ships a holder the wrong size with no exception — exactly the silent bug to catch.
  2. model_compute_holder.handler — the resolve/guard branches: no design, an axis handle that doesn't
     define an axis (get_axis -> None), an end datum not normal to the axis (is_valid_axial_datum ->
     None), an empty profile, and the happy path (returns segments_mm + holder_json, writes NO library).

The handler reaches the geometry core through the module's ``_holder`` reference, so the tests patch
that with a tiny fake core — pinning the handler's branching without a live BRep.
"""

import json

from conftest import load_tool

mch = load_tool("model_compute_holder")
holder = load_tool("_holder")


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


# ── _holder.build_holder_data: the cm→mm conversion + JSON shape (pure) ─────────────────────────

class TestBuildHolderData:
    def test_segment_units_cm_to_mm(self):
        # one segment from z0=0 to z1=2 cm, r 0.5cm -> 1.0cm: height 20mm, lower dia 10mm, upper 20mm
        data = holder.build_holder_data([[0.0, 2.0, 0.5, 1.0]], "H")
        assert data["type"] == "holder" and data["unit"] == "millimeters"
        seg = data["segments"][0]
        assert seg["height"] == 20.0
        assert seg["lower-diameter"] == 10.0     # 0.5cm radius -> 1.0cm dia -> 10mm
        assert seg["upper-diameter"] == 20.0     # 1.0cm radius -> 2.0cm dia -> 20mm

    def test_multiple_segments_in_order(self):
        data = holder.build_holder_data([[0, 1, 0.5, 0.5], [1, 3, 0.5, 0.8]], "H")
        assert [s["height"] for s in data["segments"]] == [10.0, 20.0]

    def test_metadata_passthrough(self):
        data = holder.build_holder_data([[0, 1, 0.5, 0.5]], "MyHolder",
                                        prodid="PID-1", prodlink="http://x", prodvendor="Acme")
        assert data["description"] == "MyHolder"
        assert data["product-id"] == "PID-1"
        assert data["product-link"] == "http://x"
        assert data["vendor"] == "Acme"

    def test_guid_and_reference_guid_match(self):
        # the command pairs guid == reference_guid; pin it so a refactor can't split them
        data = holder.build_holder_data([[0, 1, 0.5, 0.5]], "H")
        assert data["guid"] == data["reference_guid"]
        assert data["guid"].startswith("00000000-0000-0000-0000-")

    def test_empty_profile_gives_no_segments(self):
        assert holder.build_holder_data([], "H")["segments"] == []


# ── handler: a fake geometry core to drive the branches ─────────────────────────────────────────

class _FakeAxisLine:
    pass


class _FakePoint:
    pass


def _install_core(*, axis=_FakeAxisLine(), datum=_FakePoint(), profile=None):
    """Patch the module's _holder with a fake core so the handler's branching is exercised without a
    live BRep. axis=None makes get_axis fail; datum=None makes the datum invalid; profile drives
    get_tool_profile. build_holder_data stays the REAL one (we want its JSON)."""
    real_build = holder.build_holder_data

    class _Core:
        get_axis = staticmethod(lambda ent: axis)
        is_valid_axial_datum = staticmethod(lambda ent, ax: datum)
        get_tool_profile = staticmethod(lambda body, ax, pt: (profile if profile is not None else []))
        build_holder_data = staticmethod(real_build)

    mch._holder = _Core()


def _install_design(has_design=True):
    design = object() if has_design else None
    mch._common.design = lambda: design
    mch._inputs._common.design = lambda: design
    # the three input kinds resolve to opaque entities; patch them to succeed by default
    mch._BODY.resolve = lambda raw: ("body_ent", None)
    mch._AXIS.resolve = lambda raw: ("axis_ent", None)
    mch._END.resolve = lambda raw: ("end_ent", None)
    # app.activeDocument.name default for the holder name
    mch.app = type("A", (), {"activeDocument": type("D", (), {"name": "DocHolder"})()})()
    return design


# ── handler happy path + guards ──────────────────────────────────────────────────────────────────

class TestHandler:
    def test_happy_path_returns_segments_and_json_no_library(self):
        _install_design()
        _install_core(profile=[[0.0, 2.0, 0.5, 1.0]])
        out = _payload(mch.handler(body="h_body", axis="h_axis", end_datum="h_end", name="Collet"))
        assert out["computed"] is True
        assert out["name"] == "Collet"
        assert out["segment_count"] == 1
        assert out["segments_mm"][0]["height"] == 20.0
        assert out["holder_json"]["type"] == "holder"
        # READ-ONLY contract: nothing about a library write in the result
        assert "library" not in json.dumps(out).lower() or "library tool family" in out["note"].lower()

    def test_name_defaults_to_active_document(self):
        _install_design()
        _install_core(profile=[[0, 1, 0.5, 0.5]])
        out = _payload(mch.handler(body="h", axis="h", end_datum="h"))
        assert out["name"] == "DocHolder"

    def test_no_active_design_errors(self):
        _install_design(has_design=False)
        res = mch.handler(body="h", axis="h", end_datum="h")
        assert res["isError"] is True and "no active design" in res["message"].lower()

    def test_axis_not_an_axis_errors(self):
        _install_design()
        _install_core(axis=None)                 # get_axis returns None
        res = mch.handler(body="h", axis="h", end_datum="h")
        assert res["isError"] is True and "axis of rotation" in res["message"].lower()

    def test_invalid_end_datum_errors(self):
        _install_design()
        _install_core(datum=None)                # is_valid_axial_datum returns None
        res = mch.handler(body="h", axis="h", end_datum="h")
        assert res["isError"] is True and "end datum" in res["message"].lower()

    def test_empty_profile_errors(self):
        _install_design()
        _install_core(profile=[])                # no coaxial faces reduced
        res = mch.handler(body="h", axis="h", end_datum="h")
        assert res["isError"] is True and "no holder profile" in res["message"].lower()

    def test_bad_body_handle_errors(self):
        _install_design()
        _install_core(profile=[[0, 1, 0.5, 0.5]])
        mch._BODY.resolve = lambda raw: (None, "'body': no body named 'x'.")
        res = mch.handler(body="x", axis="h", end_datum="h")
        assert res["isError"] is True and "body" in res["message"].lower()
