"""Unit tests for ``sketch_set_text.py`` — set/create sketch-text strings.

Pinned here (no live Fusion): the quote/unquote round-trip (the textParameter expression is the
QUOTED string, with single-quote escaping), the sketch-text iterator across components + sketches
with a name filter, the per-sketch 0-based index selection, the before/after change tally + the
_MAX cap, the recompute gating (only in parametric mode), and the create path's unit scaling and
guards. The actual engraving is a live side-effect.
"""

import json

from conftest import load_tool

st = load_tool("sketch_set_text")


# ── quote / unquote (round-trip + escaping) ─────────────────────────────────

class TestQuoteUnquote:
    def test_quote_wraps_in_single_quotes(self):
        assert st._quote("Hello") == "'Hello'"

    def test_quote_escapes_inner_single_quote(self):
        assert st._quote("It's") == "'It\\'s'"

    def test_unquote_strips_single_quotes(self):
        assert st._unquote("'Label'") == "Label"

    def test_unquote_strips_double_quotes(self):
        assert st._unquote('"Label"') == "Label"

    def test_unquote_passes_unquoted_through(self):
        assert st._unquote("bare") == "bare"

    def test_unquote_none_is_none(self):
        assert st._unquote(None) is None

    def test_unquote_single_char_not_stripped(self):
        # length < 2 -> can't be a quoted pair
        assert st._unquote("'") == "'"

    def test_quote_round_trips_for_quote_free_text(self):
        # _quote escapes inner quotes but _unquote only strips the outer pair (no unescape), so the
        # round-trip is an identity ONLY for text with no single quotes.
        for s in ("plain", "two words", ""):
            assert st._unquote(st._quote(s)) == s


# ── fakes ───────────────────────────────────────────────────────────────────

class FakeParam:
    def __init__(self, expr):
        self.expression = expr


class FakeText:
    def __init__(self, expr):
        self.textParameter = FakeParam(expr)


class _Coll:
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]


class FakeSketch:
    def __init__(self, name, texts):
        self.name = name
        self.sketchTexts = _Coll(texts)


class FakeComp:
    def __init__(self, name, sketches):
        self.name = name
        self.sketches = _Coll(sketches)


class FakeDesign:
    def __init__(self, comps, design_type=1):
        self._comps = list(comps)
        self.designType = design_type
        self.computed = False
    @property
    def allComponents(self):
        return self._comps
    # resolve_sketch (used by the create path) searches rootComponent + all_components.
    @property
    def rootComponent(self):
        return self._comps[0] if self._comps else None
    def computeAll(self):
        self.computed = True


def _install(comps, design_type=1):
    design = FakeDesign(comps, design_type)
    st.app = type("A", (), {"activeProduct": design})()
    st._common.app = st.app
    import adsk.fusion
    adsk.fusion.Design.cast = lambda x: x if isinstance(x, FakeDesign) else None
    return design


def _payload(res):
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# ── _iter_sketch_texts ──────────────────────────────────────────────────────

class TestIterSketchTexts:
    def test_collects_across_components_and_sketches(self):
        c1 = FakeComp("Root", [FakeSketch("S1", [FakeText("'a'")]),
                               FakeSketch("S2", [FakeText("'b'"), FakeText("'c'")])])
        c2 = FakeComp("Sub", [FakeSketch("S3", [FakeText("'d'")])])
        design = _install([c1, c2])
        got = list(st._iter_sketch_texts(design, ""))
        assert len(got) == 4
        # tuple shape: (component_name, sketch_name, sketch_text)
        assert got[0][0] == "Root" and got[0][1] == "S1"

    def test_name_filter_limits_to_one_sketch(self):
        c1 = FakeComp("Root", [FakeSketch("Label", [FakeText("'x'")]),
                               FakeSketch("Other", [FakeText("'y'")])])
        design = _install([c1])
        got = list(st._iter_sketch_texts(design, "Label"))
        assert len(got) == 1 and got[0][1] == "Label"

    def test_no_texts_yields_empty(self):
        design = _install([FakeComp("Root", [FakeSketch("Empty", [])])])
        assert list(st._iter_sketch_texts(design, "")) == []


# ── edit handler: tally / index / recompute ─────────────────────────────────

class TestEditHandler:
    def test_sets_all_texts_and_reports_before_after(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [FakeText("'old1'"), FakeText("'old2'")])])])
        out = _payload(st.handler(text="New"))
        assert out["set"] is True
        assert out["changed_count"] == 2
        assert out["changed"][0]["before"] == "old1"
        assert out["changed"][0]["after"] == "New"

    def test_index_selects_one_text_within_sketch(self):
        sk = FakeSketch("S", [FakeText("'zero'"), FakeText("'one'"), FakeText("'two'")])
        design = _install([FakeComp("Root", [sk])])
        out = _payload(st.handler(text="Picked", index=1))
        assert out["changed_count"] == 1
        assert out["changed"][0]["before"] == "one"
        # the other two were left as quoted originals
        assert sk.sketchTexts.item(0).textParameter.expression == "'zero'"
        assert sk.sketchTexts.item(2).textParameter.expression == "'two'"

    def test_index_out_of_range_is_error(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text="X", index=5)
        assert res["isError"] is True and "index 5" in res["message"]

    def test_index_counter_is_per_sketch(self):
        # index=0 must pick the FIRST text of EACH sketch, not the first overall
        s1 = FakeSketch("S1", [FakeText("'a0'"), FakeText("'a1'")])
        s2 = FakeSketch("S2", [FakeText("'b0'"), FakeText("'b1'")])
        design = _install([FakeComp("Root", [s1, s2])])
        out = _payload(st.handler(text="Z", index=0))
        assert out["changed_count"] == 2
        befores = {c["before"] for c in out["changed"]}
        assert befores == {"a0", "b0"}

    def test_no_text_in_named_sketch_errors(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text="X", sketch_name="Ghost")
        assert res["isError"] is True and "Ghost" in res["message"]

    def test_no_text_in_design_errors(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [])])])
        res = st.handler(text="X")
        assert res["isError"] is True and "No sketch text found" in res["message"]

    def test_recompute_runs_in_parametric(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])], design_type=1)
        out = _payload(st.handler(text="X"))
        assert out["recomputed"] is True
        assert design.computed is True

    def test_recompute_skipped_in_direct_mode(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])], design_type=0)
        out = _payload(st.handler(text="X"))
        assert out["recomputed"] is False
        assert design.computed is False

    def test_set_failure_is_reported(self):
        sk = FakeSketch("S", [FakeText("'a'")])

        class _Bad:
            @property
            def expression(self):
                return "'a'"
            @expression.setter
            def expression(self, v):
                raise RuntimeError("locked")
        sk.sketchTexts.item(0).textParameter = _Bad()
        design = _install([FakeComp("Root", [sk])])
        res = st.handler(text="X")
        assert res["isError"] is True and "Failed to set sketch text" in res["message"]

    def test_max_cap_limits_changes(self):
        # build _MAX + 5 texts in one sketch; only _MAX are changed
        n = st._MAX + 5
        sk = FakeSketch("S", [FakeText("'t'") for _ in range(n)])
        design = _install([FakeComp("Root", [sk])])
        out = _payload(st.handler(text="X"))
        assert out["changed_count"] == st._MAX

    def test_none_text_errors(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text=None)
        assert res["isError"] is True and "Provide 'text'" in res["message"]


# ── create path ─────────────────────────────────────────────────────────────

class FakeTextInput:
    def __init__(self, text, height):
        self.text = text
        self.height = height
        self.multiline = None
    def setAsMultiLine(self, p1, p2, halign, valign, angle):
        self.multiline = (p1, p2, halign, valign, angle)


class FakeSketchTexts:
    def __init__(self):
        self.last_input = None
    def createInput2(self, text, height):
        self.last_input = FakeTextInput(text, height)
        return self.last_input
    def add(self, ipt):
        return type("T", (), {"name": "Text1"})()


class FakeSketchForCreate:
    def __init__(self, name):
        self.name = name
        self.sketchTexts = FakeSketchTexts()


class _NamedColl(_Coll):
    def itemByName(self, name):
        for it in self._i:
            if getattr(it, "name", None) == name:
                return it
        return None


def _install_create(sketch_name="Plate"):
    sk = FakeSketchForCreate(sketch_name)
    comp = type("C", (), {"name": "Root", "sketches": _NamedColl([sk])})()
    design = _install([comp])
    import adsk.core
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    ha = adsk.core.HorizontalAlignments
    ha.LeftHorizontalAlignment = "left"
    va = adsk.core.VerticalAlignments
    va.BottomVerticalAlignment = "bottom"
    return design, sk


class TestCreate:
    def test_creates_text_with_scaled_height(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="LBL", create=True, sketch_name="Plate", height=10, units="mm"))
        assert out["created"] is True and out["text"] == "LBL"
        # height 10mm -> 1.0cm handed to createInput2
        assert sk.sketchTexts.last_input.height == 1.0

    def test_create_position_scaled(self):
        design, sk = _install_create()
        _payload(st.handler(text="A", create=True, sketch_name="Plate", x=20, y=30, units="mm"))
        p1 = sk.sketchTexts.last_input.multiline[0]
        # x 20mm -> 2cm, y 30mm -> 3cm
        assert p1 == ("pt", 2.0, 3.0, 0)

    def test_create_requires_sketch_name(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="")
        assert res["isError"] is True and "sketch_name" in res["message"]

    def test_create_unknown_units(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="Plate", units="furlong")
        assert res["isError"] is True and "Unknown units" in res["message"]

    def test_create_nonpositive_height(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="Plate", height=0)
        assert res["isError"] is True and "height" in res["message"].lower()

    def test_create_missing_sketch(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="NoSuch")
        assert res["isError"] is True and "NoSuch" in res["message"]
