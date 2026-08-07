"""Unit tests for ``sketch_set_text.py`` — set/create sketch-text strings.

Pinned here (no live Fusion): the quote/unquote round-trip (the textParameter expression is the
QUOTED string, with single-quote escaping), the sketch-text iterator across components + sketches
with a name filter, the per-sketch 0-based index selection, the before/after change tally + the
_MAX cap, the recompute gating (only in parametric mode), the create path's unit scaling and
guards, the three layout modes with the inputs each one refuses, the definition read-back that
says which mode actually landed, and the font applied on both paths with its read-back. The actual
engraving is a live side-effect.
"""

import json
import math
import types

import pytest

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
    """A SketchText. `fontName` is a real property so the set-then-read-back has something to read:
    `font_error` models Fusion refusing a name (the setter raises and the font is kept), and
    `font_lands=False` models a setter that accepts the assignment while the font stays put, and
    `font_readable=False` a text whose font will not read at all."""
    def __init__(self, expr, font="Arial", font_error=None, font_lands=True, font_readable=True):
        self.textParameter = FakeParam(expr)
        self._font = font
        self.font_error = font_error
        self.font_lands = font_lands
        self.font_readable = font_readable

    @property
    def fontName(self):
        if not self.font_readable:
            raise RuntimeError("fontName is not available")
        return self._font

    @fontName.setter
    def fontName(self, value):
        if self.font_error:
            raise RuntimeError(self.font_error)
        if self.font_lands:
            self._font = value


class _Coll:
    # Like a live adsk collection: counted (count/item) AND iterable - consumers use both styles.
    def __init__(self, items):
        self._i = list(items)
    @property
    def count(self):
        return len(self._i)
    def item(self, i):
        return self._i[i]
    def __iter__(self):
        return iter(self._i)


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
        # a counted collection on the DESIGN, as in the live API (Component has no such attribute)
        return _Coll(self._comps)
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
        # Hitting the cap must be reported, not silently truncated
        assert out["truncated"] is True

    def test_truncated_is_false_when_under_the_cap(self):
        design = _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'"), FakeText("'b'")])])])
        out = _payload(st.handler(text="X"))
        assert out["truncated"] is False

    def test_none_text_errors(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text=None)
        assert res["isError"] is True and "Provide 'text'" in res["message"]


# ── create path ─────────────────────────────────────────────────────────────

class FakeTextInput:
    """A SketchTextInput: the three setAs* placements plus the formatting properties. angle/flips
    are real attributes so set_verified's read-back succeeds, as it does live. `place_returns`
    models Fusion REFUSING a placement - the setAs* call answers false and nothing is placed."""
    def __init__(self, text, height, place_returns=True):
        self.text = text
        self.height = height
        self.multiline = None
        self.along = None
        self.fit = None
        self.mode = None
        self.place_returns = place_returns
        self.angle = 0.0
        self.isHorizontalFlip = False
        self.isVerticalFlip = False
        # the input takes any string; Fusion checks the name at add()
        self.fontName = None
    def _placed(self, mode):
        if not self.place_returns:
            return False
        self.mode = mode
        return True
    def setAsMultiLine(self, p1, p2, halign, valign, spacing):
        self.multiline = (p1, p2, halign, valign, spacing)
        return self._placed("multi_line")
    def setAsAlongPath(self, path, above, halign, spacing):
        self.along = (path, above, halign, spacing)
        return self._placed("along_path")
    def setAsFitOnPath(self, path, above):
        self.fit = (path, above)
        return self._placed("fit_on_path")


# mode -> the objectType the created text's definition reports. Bindings-sourced for all three;
# the fit-on-path spelling is the one measured live, typo included.
_DEFINITION_TYPES = {"multi_line": "adsk::fusion::MultiLineTextDefinition",
                     "along_path": "adsk::fusion::AlongPathTextDefinition",
                     "fit_on_path": "adsk::fusion::FitOnPathTextDefintion"}


def _fake_definition(ipt, obj_type, blind):
    """The definition object a created text carries. Unless `blind`, it reports the placement
    values back the way AlongPath/FitOnPath/MultiLine definitions do."""
    definition = types.SimpleNamespace(objectType=obj_type)
    if blind:
        return definition
    if ipt.mode == "along_path":
        _path, above, halign, spacing = ipt.along
        definition.isAbovePath = above
        definition.horizontalAlignment = halign
        definition.characterSpacing = spacing
    elif ipt.mode == "fit_on_path":
        definition.isAbovePath = ipt.fit[1]
    elif ipt.mode == "multi_line":
        definition.horizontalAlignment = ipt.multiline[2]
        definition.characterSpacing = ipt.multiline[4]
    return definition


class FakeSketchTexts:
    """createInput2/add that model a real SketchTexts collection: add() APPENDS so `count` rises -
    unless `materialize=False`, which returns a truthy text object while the collection stays flat
    (the on-face silent-no-op the honesty read-back must catch). `raise_on_add` models Fusion
    rejecting the placement at add() time; `definition_type` forces the landed definition;
    `blind_definition` makes every placement read off it fail; `placement_override` makes the
    definition report a placement that DISAGREES with the request; `place_returns=False` makes the
    setAs* call refuse. A font is checked at ADD time: `font_raises` models Fusion rejecting the
    name there, `landed_font` a text landing with a font other than the one asked for, and
    `blind_font` a created text whose fontName will not read."""
    def __init__(self, initial=0, materialize=True, add_returns=True, raise_on_add=None,
                 definition_type=None, blind_definition=False, place_returns=True,
                 placement_override=None, font_raises=None, landed_font=None, blind_font=False):
        self.last_input = None
        self._texts = [type("T", (), {"name": f"T{i}"})() for i in range(initial)]
        self.materialize = materialize
        self.add_returns = add_returns
        self.raise_on_add = raise_on_add
        self.definition_type = definition_type
        self.blind_definition = blind_definition
        self.place_returns = place_returns
        self.placement_override = placement_override or {}
        self.font_raises = font_raises
        self.landed_font = landed_font
        self.blind_font = blind_font
        self.add_calls = 0
    @property
    def count(self):
        return len(self._texts)
    def createInput2(self, text, height):
        self.last_input = FakeTextInput(text, height, place_returns=self.place_returns)
        return self.last_input
    def add(self, ipt):
        self.add_calls += 1
        if self.raise_on_add:
            raise RuntimeError(self.raise_on_add)
        if self.font_raises and getattr(ipt, "fontName", None):
            raise RuntimeError(self.font_raises)
        obj_type = (_DEFINITION_TYPES.get(ipt.mode) if self.definition_type is None
                    else self.definition_type)
        definition = _fake_definition(ipt, obj_type, self.blind_definition)
        for prop, value in self.placement_override.items():
            setattr(definition, prop, value)
        st = types.SimpleNamespace(name="Text1", definition=definition)
        font = self.landed_font if self.landed_font is not None else getattr(ipt, "fontName", None)
        if font is not None and not self.blind_font:
            st.fontName = font
        if self.materialize:
            self._texts.append(st)
        return st if self.add_returns else None


class FakeSketchForCreate:
    def __init__(self, name, texts=None, lines=0, circles=0):
        self.name = name
        self.sketchTexts = texts if texts is not None else FakeSketchTexts()
        # the '<type>:<index>' collections resolve_entity_ref indexes
        self.sketchCurves = types.SimpleNamespace(
            sketchLines=_Coll([types.SimpleNamespace(kind="line", i=i) for i in range(lines)]),
            sketchCircles=_Coll([types.SimpleNamespace(kind="circle", i=i) for i in range(circles)]))
        self.sketchPoints = _Coll([types.SimpleNamespace(kind="point", i=0)])


class _NamedColl(_Coll):
    def itemByName(self, name):
        for it in self._i:
            if getattr(it, "name", None) == name:
                return it
        return None


def _install_create(sketch_name="Plate", texts=None, lines=0, circles=0):
    sk = FakeSketchForCreate(sketch_name, texts=texts, lines=lines, circles=circles)
    comp = type("C", (), {"name": "Root", "sketches": _NamedColl([sk])})()
    design = _install([comp])
    import adsk.core
    adsk.core.Point3D.create = staticmethod(lambda x, y, z: ("pt", x, y, z))
    # HorizontalAlignments/VerticalAlignments members arrive pre-seeded with the measured ints
    # (live_api_facts via conftest) - the fake reads them, never assigns them.
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

    # ── honesty read-back: verify the text actually materialized ──────────────

    def test_create_reports_verified_count_delta(self):
        # count must be read back off the collection and reported (0 -> 1 here)
        design, sk = _install_create(texts=FakeSketchTexts(initial=0))
        out = _payload(st.handler(text="LBL", create=True, sketch_name="Plate"))
        assert out["created"] is True
        assert out["sketch_text_count"] == 1

    def test_create_count_delta_from_nonzero_base(self):
        design, sk = _install_create(texts=FakeSketchTexts(initial=3))
        out = _payload(st.handler(text="LBL", create=True, sketch_name="Plate"))
        assert out["sketch_text_count"] == 4

    def test_create_silent_noop_is_error_not_false_ok(self):
        # add() returns a truthy text object but the collection count does NOT rise: nothing
        # materialized. Reporting created:true here would be a false ok - the cardinal sin.
        sk_texts = FakeSketchTexts(initial=0, materialize=False, add_returns=True)
        design, sk = _install_create(texts=sk_texts)
        res = st.handler(text="LBL", create=True, sketch_name="Plate", x=0, y=0, height=5)
        assert res["isError"] is True
        assert "did not materialize" in res["message"]
        # and it names the coordinate-space fix (sketch-plane coords / frame)
        assert "SKETCH-plane" in res["message"] or "frame" in res["message"]

    def test_create_add_returns_none_is_error(self):
        sk_texts = FakeSketchTexts(initial=0, materialize=False, add_returns=False)
        design, sk = _install_create(texts=sk_texts)
        res = st.handler(text="LBL", create=True, sketch_name="Plate")
        assert res["isError"] is True and "did not materialize" in res["message"]

    def test_create_uses_sketch_plane_not_world_coordinates(self):
        # the corner point handed to setAsMultiLine is the raw (x,y) in SKETCH space (scaled to cm),
        # with no world/model transform applied - on-face coordinate handling.
        design, sk = _install_create()
        _payload(st.handler(text="AB", create=True, sketch_name="Plate", x=12, y=8, units="mm"))
        corner, diagonal = sk.sketchTexts.last_input.multiline[0], sk.sketchTexts.last_input.multiline[1]
        assert corner[0] == "pt" and corner[3] == 0
        assert corner[1] == pytest.approx(1.2) and corner[2] == pytest.approx(0.8)   # 12mm,8mm -> cm
        # diagonal is strictly offset in BOTH axes so the text box is never degenerate
        assert diagonal[1] > corner[1] and diagonal[2] > corner[2]

    def test_default_mode_is_multi_line(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate"))
        assert out["mode"] == "multi_line"
        assert sk.sketchTexts.last_input.mode == "multi_line"

    def test_unknown_mode_is_refused(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="wrap_around")
        assert res["isError"] is True and "wrap_around" in res["message"]


# ── path modes: along_path / fit_on_path ────────────────────────────────────

# a find_geometry handle: the composite locator form is_handle recognises
_EDGE_HANDLE = "qwertyuiopasdfghjklzxcvbnm0123456789==|@edge:2"


class TestPathModes:
    def test_along_path_passes_curve_alignment_and_spacing(self):
        design, sk = _install_create(lines=2)
        out = _payload(st.handler(text="RIM", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:1", align="center", character_spacing=25))
        import adsk.core
        curve, above, halign, spacing = sk.sketchTexts.last_input.along
        assert curve is sk.sketchCurves.sketchLines.item(1)
        assert halign is adsk.core.HorizontalAlignments.CenterHorizontalAlignment
        assert above is True and spacing == 25.0
        assert out["mode"] == "along_path" and out["path"] == "line:1"
        # published from the DEFINITION's own read-back, not echoed from the request
        assert out["align"] == "center" and out["character_spacing"] == 25.0
        assert out["above_path"] is True and "requested" not in out

    def test_along_path_on_a_closed_circle(self):
        # the headline case: text wrapped right around a hole
        design, sk = _install_create(circles=1)
        out = _payload(st.handler(text="M8", create=True, sketch_name="Plate", mode="along_path",
                                  path="circle:0"))
        assert sk.sketchTexts.last_input.along[0] is sk.sketchCurves.sketchCircles.item(0)
        assert out["mode_verified"] is True
        assert out["definition_type"].endswith("AlongPathTextDefinition")
        # LeftHorizontalAlignment is 0: a falsy-but-real member must map back to its wire key, not
        # be read as unreadable
        assert out["align"] == "left" and "requested" not in out

    def test_above_path_false_is_passed_through(self):
        design, sk = _install_create(lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0", above_path=False))
        assert sk.sketchTexts.last_input.along[1] is False
        assert out["above_path"] is False

    def test_fit_on_path_uses_the_two_argument_call(self):
        design, sk = _install_create(lines=1)
        out = _payload(st.handler(text="FIT", create=True, sketch_name="Plate", mode="fit_on_path",
                                  path="line:0"))
        assert sk.sketchTexts.last_input.fit == (sk.sketchCurves.sketchLines.item(0), True)
        assert sk.sketchTexts.last_input.along is None
        # fit spaces the glyphs itself, so no alignment/spacing is reported
        assert "align" not in out and "character_spacing" not in out

    def test_fit_on_path_definition_typo_is_matched_as_landed(self):
        # the live objectType carries the binding's misspelled 'FitOnPathTextDefintion'
        design, sk = _install_create(lines=1)
        out = _payload(st.handler(text="FIT", create=True, sketch_name="Plate", mode="fit_on_path",
                                  path="line:0"))
        assert out["definition_type"] == "adsk::fusion::FitOnPathTextDefintion"
        assert out["mode_verified"] is True

    def test_path_mode_requires_a_path(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path")
        assert res["isError"] is True and "'path' is required" in res["message"]

    def test_edge_handle_path_is_refused_before_anything_is_created(self):
        # a BRepEdge path is accepted by setAsAlongPath and then rejected at add() live, so the
        # handle never reaches createInput2
        design, sk = _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path=_EDGE_HANDLE)
        assert res["isError"] is True
        assert "sketch_project" in res["message"] and "pSketchCurve" in res["message"]
        assert sk.sketchTexts.last_input is None
        assert sk.sketchTexts.count == 0

    def test_point_path_is_refused(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="point:0")
        assert res["isError"] is True and "POINT" in res["message"]

    def test_unresolvable_path_names_the_ref(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="line:7")
        assert res["isError"] is True and "line:7" in res["message"]

    def test_failing_add_surfaces_the_platform_error_verbatim(self):
        texts = FakeSketchTexts(raise_on_add="2 : InternalValidationError : pSketchCurve")
        design, sk = _install_create(texts=texts, lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="line:0")
        assert res["isError"] is True
        assert "InternalValidationError : pSketchCurve" in res["message"]

    @pytest.mark.parametrize("mode,path", [("multi_line", ""), ("along_path", "line:0"),
                                           ("fit_on_path", "line:0")])
    def test_a_refused_placement_creates_nothing(self, mode, path):
        # setAs* answering false is an explicit refusal: add() must never be reached, and the call
        # must report the failure rather than a text that was never placed
        texts = FakeSketchTexts(place_returns=False)
        design, sk = _install_create(texts=texts, lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode=mode, path=path)
        assert res["isError"] is True
        assert mode in res["message"] and "no text was placed" in res["message"]
        assert texts.add_calls == 0
        assert texts.count == 0

    def test_path_mode_noop_error_points_at_the_curve_not_the_frame(self):
        texts = FakeSketchTexts(materialize=False)
        design, sk = _install_create(texts=texts, lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="line:0")
        assert res["isError"] is True and "did not materialize" in res["message"]
        assert "line:0" in res["message"]


# ── the inputs each mode has no API slot for ────────────────────────────────

class TestModeInputRefusals:
    def test_fit_on_path_refuses_align(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="fit_on_path",
                         path="line:0", align="center")
        assert res["isError"] is True
        assert "'align'" in res["message"] and "along_path" in res["message"]

    def test_fit_on_path_refuses_character_spacing(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="fit_on_path",
                         path="line:0", character_spacing=0)
        assert res["isError"] is True and "'character_spacing'" in res["message"]

    def test_path_mode_refuses_x_and_y(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="line:0", x=10)
        assert res["isError"] is True and "'x'" in res["message"]

    def test_multi_line_refuses_a_path(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", path="line:0")
        assert res["isError"] is True and "'path'" in res["message"]

    def test_multi_line_refuses_above_path(self):
        _install_create(lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", above_path=False)
        assert res["isError"] is True and "'above_path'" in res["message"]

    def test_multi_line_still_takes_align_and_spacing(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="AB", create=True, sketch_name="Plate", align="right",
                                  character_spacing=10))
        import adsk.core
        assert (sk.sketchTexts.last_input.multiline[2]
                is adsk.core.HorizontalAlignments.RightHorizontalAlignment)
        assert sk.sketchTexts.last_input.multiline[4] == 10.0
        assert out["align"] == "right"

    def test_multi_line_defaults_match_the_previous_hard_coded_values(self):
        import adsk.core
        design, sk = _install_create()
        _payload(st.handler(text="AB", create=True, sketch_name="Plate"))
        assert (sk.sketchTexts.last_input.multiline[2]
                is adsk.core.HorizontalAlignments.LeftHorizontalAlignment)
        assert (sk.sketchTexts.last_input.multiline[3]
                is adsk.core.VerticalAlignments.BottomVerticalAlignment)
        assert sk.sketchTexts.last_input.multiline[4] == 0.0

    def test_non_numeric_character_spacing_is_refused(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="Plate", character_spacing="wide")
        assert res["isError"] is True and "character_spacing" in res["message"]


# ── angle + flips: set on the input, published as REQUESTED ─────────────────

class TestFormatting:
    def test_angle_is_converted_from_degrees_to_radians(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", angle_deg=45))
        assert sk.sketchTexts.last_input.angle == pytest.approx(math.radians(45))
        # the payload publishes the REQUESTED degrees, not a read-back of the created text
        assert out["requested"]["angle_deg"] == 45.0

    def test_flips_are_set_and_reported(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", flip_h=True,
                                  flip_v=True))
        assert sk.sketchTexts.last_input.isHorizontalFlip is True
        assert sk.sketchTexts.last_input.isVerticalFlip is True
        assert out["requested"] == {"flip_h": True, "flip_v": True}

    def test_untouched_formatting_is_not_reported(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate"))
        assert "requested" not in out
        assert sk.sketchTexts.last_input.angle == 0.0

    def test_note_marks_the_requested_values_as_unverified(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", angle_deg=30))
        assert "REQUESTED" in out["note"] and "were not read back off the created text" in out["note"]

    def test_angle_that_does_not_take_is_an_error(self):
        # a SWIG proxy accepts an assignment to a name it does not define; only the read-back
        # catches it, and a create that silently ignored the rotation must not report success
        class _Deaf(FakeTextInput):
            @property
            def angle(self):
                return 0.0
            @angle.setter
            def angle(self, v):
                pass
        texts = FakeSketchTexts()
        texts.createInput2 = lambda text, height: setattr(
            texts, "last_input", _Deaf(text, height)) or texts.last_input
        design, sk = _install_create(texts=texts)
        res = st.handler(text="A", create=True, sketch_name="Plate", angle_deg=45)
        assert res["isError"] is True and "angle_deg" in res["message"]
        assert sk.sketchTexts.count == 0

    def test_non_numeric_angle_is_refused(self):
        _install_create()
        res = st.handler(text="A", create=True, sketch_name="Plate", angle_deg="sideways")
        assert res["isError"] is True and "DEGREES" in res["message"]


# ── the definition read-back decides whether the right mode landed ──────────

class TestDefinitionReadBack:
    def test_a_different_mode_landing_is_an_error_not_a_false_ok(self):
        texts = FakeSketchTexts(definition_type="adsk::fusion::MultiLineTextDefinition")
        design, sk = _install_create(texts=texts, lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="line:0")
        assert res["isError"] is True
        assert "multi_line" in res["message"] and "WAS created" in res["message"]

    def test_an_unrecognised_definition_is_published_without_a_claim(self):
        texts = FakeSketchTexts(definition_type="adsk::fusion::SomethingElse")
        design, sk = _install_create(texts=texts, lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0"))
        assert out["mode_verified"] is False
        assert out["definition_type"] == "adsk::fusion::SomethingElse"

    def test_placement_is_published_from_the_definition_not_the_request(self):
        # asked for center/above; the definition says right/below - the payload must report what
        # the definition says, or the read-back is just an echo of the request
        import adsk.core
        texts = FakeSketchTexts(placement_override={
            "horizontalAlignment": adsk.core.HorizontalAlignments.RightHorizontalAlignment,
            "isAbovePath": False})
        design, sk = _install_create(texts=texts, lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0", align="center", above_path=True))
        assert out["align"] == "right" and out["above_path"] is False

    def test_unreadable_placement_is_none_and_falls_back_to_requested(self):
        texts = FakeSketchTexts(blind_definition=True)
        design, sk = _install_create(texts=texts, lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0", align="center", character_spacing=25,
                                  above_path=False))
        assert out["above_path"] is None and out["align"] is None
        assert out["character_spacing"] is None
        assert out["requested"] == {"above_path": False, "align": "center",
                                    "character_spacing": 25.0}

    def test_fit_on_path_reads_back_only_above_path(self):
        design, sk = _install_create(lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="fit_on_path",
                                  path="line:0", above_path=False))
        assert out["above_path"] is False
        assert "align" not in out and "character_spacing" not in out

    def test_an_alignment_that_matches_no_member_is_unreadable(self):
        texts = FakeSketchTexts(placement_override={"horizontalAlignment": "sideways"})
        design, sk = _install_create(texts=texts, lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0"))
        assert out["align"] is None and out["requested"]["align"] == "left"

    def test_note_omits_the_definition_clause_when_it_did_not_read(self):
        texts = FakeSketchTexts(definition_type="")
        design, sk = _install_create(texts=texts, lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0"))
        assert "definition None" not in out["note"]
        assert "sketchTexts 0 -> 1" in out["note"]

    def test_missing_definition_is_not_a_failure(self):
        texts = FakeSketchTexts(definition_type="")
        design, sk = _install_create(texts=texts, lines=1)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                                  path="line:0"))
        assert out["mode_verified"] is False and out["definition_type"] is None


# ── create-only inputs on the edit path ─────────────────────────────────────

class TestCreateOnlyGuard:
    def test_mode_without_create_is_refused(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text="X", mode="along_path")
        assert res["isError"] is True
        assert "'mode'" in res["message"] and "create=true" in res["message"]

    def test_angle_without_create_is_refused_rather_than_ignored(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text="X", angle_deg=15)
        assert res["isError"] is True and "'angle_deg'" in res["message"]

    def test_flip_false_still_counts_as_supplied(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text="X", flip_h=False)
        assert res["isError"] is True and "'flip_h'" in res["message"]

    def test_position_without_create_is_refused_like_the_other_create_inputs(self):
        # x/y default to None, so a supplied one is detectable - refusing flip_h but silently
        # dropping x=10 would be the asymmetry
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        res = st.handler(text="X", x=10)
        assert res["isError"] is True and "'x'" in res["message"]

    def test_height_and_units_stay_exempt(self):
        # both carry non-None defaults, so a caller cannot be told apart from the default
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        out = _payload(st.handler(text="X", height=9, units="in"))
        assert out["changed_count"] == 1

    def test_plain_edit_is_unaffected(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        out = _payload(st.handler(text="X"))
        assert out["changed_count"] == 1


# ── font_name: applied on both paths, verified by reading it back ───────────

_BAD_FONT = "ZzNoSuchFont"
# the sentence Fusion raises for a font name it does not know, on add() and on the setter alike
_FONT_SENTENCE = "3 : invalid input font name"


class TestFontOnCreate:
    def test_font_is_set_on_the_input_and_read_back_off_the_landed_text(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", font_name="Arial"))
        assert sk.sketchTexts.last_input.fontName == "Arial"
        assert out["font"] == "Arial"
        assert "requested" not in out

    def test_a_different_font_landing_is_an_error_not_a_false_ok(self):
        # the text landed with a font other than the one asked for: reporting created:true with the
        # requested name would be an echo, not a read-back
        texts = FakeSketchTexts(landed_font="Courier New")
        design, sk = _install_create(texts=texts)
        res = st.handler(text="A", create=True, sketch_name="Plate", font_name="Arial")
        assert res["isError"] is True
        assert "'Arial'" in res["message"] and "'Courier New'" in res["message"]
        assert "WAS created" in res["message"]

    def test_unreadable_font_is_published_none_and_falls_back_to_requested(self):
        texts = FakeSketchTexts(blind_font=True)
        design, sk = _install_create(texts=texts)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", font_name="Arial"))
        assert out["font"] is None
        assert out["requested"]["font"] == "Arial"
        assert "REQUESTED" in out["note"]

    def test_refused_font_carries_the_platform_sentence_and_the_offending_name(self):
        texts = FakeSketchTexts(font_raises=_FONT_SENTENCE)
        design, sk = _install_create(texts=texts)
        res = st.handler(text="A", create=True, sketch_name="Plate", font_name=_BAD_FONT)
        assert res["isError"] is True
        assert _FONT_SENTENCE in res["message"] and _BAD_FONT in res["message"]
        # no font-name validation API exists, so the message must not pretend to list legal names
        assert "no API lists the legal names" in res["message"]
        assert texts.count == 0

    def test_a_non_font_add_failure_is_not_blamed_on_the_font(self):
        # the font is named as what was applied, but only a platform sentence about the FONT earns
        # the font advice - otherwise the tool would guess the cause
        texts = FakeSketchTexts(raise_on_add="2 : InternalValidationError : pSketchCurve")
        design, sk = _install_create(texts=texts, lines=1)
        res = st.handler(text="A", create=True, sketch_name="Plate", mode="along_path",
                         path="line:0", font_name="Arial")
        assert res["isError"] is True
        assert "pSketchCurve" in res["message"] and "'Arial'" in res["message"]
        assert "no API lists the legal names" not in res["message"]

    def test_a_case_variant_landing_is_a_mismatch_not_a_match(self):
        # Fusion normalizes no case, so a text reporting 'arial' when 'Arial' was asked for is a
        # real disagreement - comparing case-insensitively would wave it through
        texts = FakeSketchTexts(landed_font="arial")
        design, sk = _install_create(texts=texts)
        res = st.handler(text="A", create=True, sketch_name="Plate", font_name="Arial")
        assert res["isError"] is True
        assert "'Arial'" in res["message"] and "'arial'" in res["message"]

    def test_an_empty_font_read_back_is_no_name_at_all(self):
        # "" is not a font name: publishing it as the confirmed font would claim a read-back that
        # says nothing, so it is None and the requested name is labelled as unverified
        texts = FakeSketchTexts(landed_font="")
        design, sk = _install_create(texts=texts)
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate", font_name="Arial"))
        assert out["font"] is None
        assert out["requested"]["font"] == "Arial"

    def test_a_case_variant_font_is_refused_by_fusion_not_normalized_here(self):
        # font names are case-sensitive and nothing normalizes them: 'arial' raises the same
        # sentence, which the tool carries with the name that caused it
        texts = FakeSketchTexts(font_raises=_FONT_SENTENCE)
        design, sk = _install_create(texts=texts)
        res = st.handler(text="A", create=True, sketch_name="Plate", font_name="arial")
        assert res["isError"] is True
        assert _FONT_SENTENCE in res["message"] and "'arial'" in res["message"]

    def test_no_font_given_publishes_no_font_and_touches_the_input(self):
        design, sk = _install_create()
        out = _payload(st.handler(text="A", create=True, sketch_name="Plate"))
        assert "font" not in out and "requested" not in out
        assert sk.sketchTexts.last_input.fontName is None


class TestFontOnEdit:
    def test_edit_applies_the_font_and_reports_the_read_back(self):
        text = FakeText("'old'", font="Courier New")
        _install([FakeComp("Root", [FakeSketch("S", [text])])])
        out = _payload(st.handler(text="X", font_name="Arial"))
        assert text.fontName == "Arial"
        assert out["changed"][0]["font"] == "Arial"
        assert out["changed"][0]["after"] == "X"
        assert "Arial" in out["note"]

    def test_refused_font_leaves_the_string_untouched(self):
        # the font goes on FIRST, so a refusal aborts before the expression is rewritten
        text = FakeText("'old'", font_error=_FONT_SENTENCE)
        _install([FakeComp("Root", [FakeSketch("S", [text])])])
        res = st.handler(text="X", font_name=_BAD_FONT)
        assert res["isError"] is True
        assert _FONT_SENTENCE in res["message"] and _BAD_FONT in res["message"]
        assert text.textParameter.expression == "'old'"
        assert "No sketch text was changed." in res["message"]

    def test_a_partial_run_names_what_was_already_updated(self):
        good = FakeText("'a'")
        bad = FakeText("'b'", font_error=_FONT_SENTENCE)
        _install([FakeComp("Root", [FakeSketch("S1", [good]), FakeSketch("S2", [bad])])])
        res = st.handler(text="X", font_name=_BAD_FONT)
        assert res["isError"] is True
        assert "1 sketch text(s) earlier in this call were already updated ('S1')" in res["message"]
        assert good.textParameter.expression == "'X'"
        assert bad.textParameter.expression == "'b'"

    def test_a_font_that_does_not_take_is_an_error(self):
        # the setter accepts the assignment and the text keeps its old font - only the read-back
        # catches it, and a silent no-op must never report success
        text = FakeText("'old'", font="Arial", font_lands=False)
        _install([FakeComp("Root", [FakeSketch("S", [text])])])
        res = st.handler(text="X", font_name="Courier New")
        assert res["isError"] is True
        assert "did not take" in res["message"]
        assert "'Arial'" in res["message"] and "'Courier New'" in res["message"]
        assert text.textParameter.expression == "'old'"

    def test_a_font_that_will_not_read_is_published_as_none(self):
        # the entry carries the READ-BACK, so a font that cannot be read is None - never the
        # requested name echoed back as if it had been confirmed
        text = FakeText("'old'", font_readable=False)
        _install([FakeComp("Root", [FakeSketch("S", [text])])])
        out = _payload(st.handler(text="X", font_name="Arial"))
        assert out["changed"][0]["font"] is None
        assert out["changed"][0]["after"] == "X"

    def test_a_case_variant_read_back_is_a_font_that_did_not_take(self):
        # nothing normalizes case, so a text still reporting 'arial' after 'Arial' was set kept its
        # own font - a case-insensitive compare would report that silent no-op as success
        text = FakeText("'old'", font="arial", font_lands=False)
        _install([FakeComp("Root", [FakeSketch("S", [text])])])
        res = st.handler(text="X", font_name="Arial")
        assert res["isError"] is True
        assert "did not take" in res["message"]
        assert "'arial'" in res["message"] and "'Arial'" in res["message"]

    def test_an_empty_font_read_back_is_no_name_at_all(self):
        # the setter accepts the name, the text reports "" - that confirms nothing, so the entry
        # publishes None rather than an empty string dressed up as a read-back
        text = FakeText("'old'", font="", font_lands=False)
        _install([FakeComp("Root", [FakeSketch("S", [text])])])
        out = _payload(st.handler(text="X", font_name="Arial"))
        assert out["changed"][0]["font"] is None
        assert out["changed"][0]["after"] == "X"

    def test_a_landed_font_with_a_failed_string_write_is_reported_as_partial(self):
        # the font landed on this text and the string did not: 'failed' on its own would read as
        # no effect at all, so the message must say the font changed
        good = FakeText("'a'")
        bad = FakeText("'b'")

        class _Locked:
            @property
            def expression(self):
                return "'b'"
            @expression.setter
            def expression(self, v):
                raise RuntimeError("locked")
        bad.textParameter = _Locked()
        _install([FakeComp("Root", [FakeSketch("S1", [good]), FakeSketch("S2", [bad])])])
        res = st.handler(text="X", font_name="Arial")
        assert res["isError"] is True
        assert "locked" in res["message"]
        assert "font WAS changed to 'Arial'" in res["message"]
        assert "1 sketch text(s) earlier in this call were already updated ('S1')" in res["message"]
        assert bad.fontName == "Arial" and bad.textParameter.expression == "'b'"

    def test_font_without_create_is_not_refused_as_a_create_only_input(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        out = _payload(st.handler(text="X", font_name="Arial"))
        assert out["changed_count"] == 1

    def test_no_font_given_leaves_the_edit_payload_as_it_was(self):
        _install([FakeComp("Root", [FakeSketch("S", [FakeText("'a'")])])])
        out = _payload(st.handler(text="X"))
        assert "font" not in out["changed"][0]
        assert "font" not in out["note"].lower()
