"""Tests for `pmi_get` (rich read over the design's PMI) and the shared `_pmi` substrate it rides:
the router's composition (default light records, include= slices, truncation, geometry narrowing),
the {symbol} markup codec, and find_annotation's ambiguity refusal."""

import json
from types import SimpleNamespace

import pytest

from conftest import load_tool, error_message, _NamedCollection

pg = load_tool("pmi_get")

import adsk.fusion  # noqa: E402  the installed mock - PMISymbolTypes members come from it


def _payload(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def _ann(name="Note1", suffix="PMILeaderLineNote", text="DEBURR", visible=True,
         out_of_date=False, warning="", **extra):
    a = SimpleNamespace(name=name, objectType="adsk::fusion::" + suffix, plainText=text,
                        isVisible=visible, isOutOfDate=out_of_date, isSuppressed=False,
                        errorOrWarningMessage=warning)
    for k, v in extra.items():
        setattr(a, k, v)
    return a


@pytest.fixture
def two_notes(monkeypatch):
    comp = SimpleNamespace(name="Root")
    anns = [_ann("Note1"), _ann("Hole Note1", suffix="PMIHoleThreadNote", text="QTY",
                 quantity=2, isThrough=True, isThreaded=False,
                 diameter=SimpleNamespace(hasValue=True, value=0.6))]
    monkeypatch.setattr(pg._common, "design", lambda: object())
    monkeypatch.setattr(pg._pmi, "walk_annotations", lambda d: iter((comp, a) for a in anns))
    return comp, anns


class TestDefaultSlice:
    def test_default_is_light_records_plus_counts(self, two_notes):
        out = _payload(pg.handler())
        assert out["total"] == 2
        assert out["by_kind"] == {"note": 1, "hole_note": 1}
        recs = out["annotations"]
        assert [r["name"] for r in recs] == ["Note1", "Hole Note1"]
        assert recs[0]["kind"] == "note" and recs[1]["kind"] == "hole_note"
        # heavy slices absent by default
        assert "markup" not in recs[0] and "parametric" not in recs[0] and "diameter" not in recs[1]

    def test_default_note_advertises_the_slices(self, two_notes):
        out = _payload(pg.handler())
        assert "segments" in out["note"] and "detail" in out["note"]

    def test_healthy_records_omit_noise_flags(self, two_notes):
        rec = _payload(pg.handler())["annotations"][0]
        assert "out_of_date" not in rec and "suppressed" not in rec and "warning" not in rec

    def test_out_of_date_and_warning_surface(self, monkeypatch):
        comp = SimpleNamespace(name="Root")
        bad = _ann("Note9", out_of_date=True, warning="reference lost")
        monkeypatch.setattr(pg._common, "design", lambda: object())
        monkeypatch.setattr(pg._pmi, "walk_annotations", lambda d: iter([(comp, bad)]))
        rec = _payload(pg.handler())["annotations"][0]
        assert rec["out_of_date"] is True and rec["warning"] == "reference lost"


class TestSlices:
    def test_segments_slice_adds_markup(self, two_notes, monkeypatch):
        monkeypatch.setattr(pg._pmi, "segments_markup", lambda a: "{flatness}0.05")
        rec = _payload(pg.handler(include=["segments"]))["annotations"][0]
        assert rec["markup"] == "{flatness}0.05"

    def test_detail_slice_scales_hole_numbers_to_units(self, two_notes):
        recs = _payload(pg.handler(include=["detail"], units="mm"))["annotations"]
        hole = recs[1]
        assert hole["diameter"] == 6.0          # 0.6 cm -> 6 mm
        assert hole["quantity"] == 2 and hole["is_through"] is True

    def test_unknown_include_is_refused(self, two_notes):
        msg = error_message(pg.handler(include=["bogus"]))
        assert "bogus" in msg and "segments" in msg


class TestBoundsAndGuards:
    def test_truncates_at_max_results_but_counts_all(self, monkeypatch):
        comp = SimpleNamespace(name="Root")
        anns = [_ann(f"N{i}") for i in range(5)]
        monkeypatch.setattr(pg._common, "design", lambda: object())
        monkeypatch.setattr(pg._pmi, "walk_annotations", lambda d: iter((comp, a) for a in anns))
        out = _payload(pg.handler(max_results=2))
        assert out["total"] == 5 and len(out["annotations"]) == 2 and out["truncated"] is True

    def test_no_active_design_is_an_error(self, monkeypatch):
        monkeypatch.setattr(pg._common, "design", lambda: None)
        assert "No active design" in error_message(pg.handler())

    def test_bad_units_refused(self, two_notes):
        assert "units" in error_message(pg.handler(units="furlong"))

    def test_geometry_resolve_error_surfaces(self, two_notes, monkeypatch):
        monkeypatch.setattr(pg._GEOMETRY, "resolve", lambda raw: (None, "stale handle"))
        assert "stale handle" in error_message(pg.handler(geometry=["h1"]))

    def test_geometry_narrows_via_items_by_entities(self, monkeypatch):
        keep = _ann("Kept")
        drop = _ann("Dropped")
        coll = SimpleNamespace(itemsByEntities=lambda ents: [keep])
        comp = SimpleNamespace(name="Root", pmiAnnotations=coll)
        monkeypatch.setattr(pg._common, "design", lambda: object())
        monkeypatch.setattr(pg._common, "all_components", lambda d: [comp])
        monkeypatch.setattr(pg._pmi, "walk_annotations",
                            lambda d: iter([(comp, keep), (comp, drop)]))
        monkeypatch.setattr(pg._GEOMETRY, "resolve", lambda raw: ([object()], None))
        out = _payload(pg.handler(geometry=["h1"]))
        assert [r["name"] for r in out["annotations"]] == ["Kept"] and out["total"] == 1


# ── the shared _pmi substrate (codec + resolver), reached through this module's import ────────────

class TestMarkupCodec:
    def test_unknown_token_error_lists_the_vocabulary(self):
        segs, err = pg._pmi.build_segments("{bogus}0.05")
        assert segs is None and "'{bogus}'" in err and "flatness" in err and "mmc" in err

    def test_empty_text_is_refused(self):
        segs, err = pg._pmi.build_segments("")
        assert segs is None and "empty" in err

    def test_symbol_text_and_linebreak_segment_counts(self):
        segs, err = pg._pmi.build_segments("{flatness}0.05")
        assert err is None and len(segs) == 2       # symbol + text
        segs, err = pg._pmi.build_segments("A\nB")
        assert err is None and len(segs) == 3       # text + break + text

    def test_markup_round_trips_through_segments(self):
        sym = SimpleNamespace(objectType="adsk::fusion::PMISymbolSegment",
                              pmiSymbolType=adsk.fusion.PMISymbolTypes.FlatnessPMISymbolType)
        txt = SimpleNamespace(objectType="adsk::fusion::PMITextSegment", text="0.05")
        ann = SimpleNamespace(segments=[sym, txt])
        assert pg._pmi.segments_markup(ann) == "{flatness}0.05"


class TestFindAnnotation:
    def _design(self, monkeypatch, comps):
        monkeypatch.setattr(pg._pmi._common, "all_components", lambda d: comps)
        return object()

    def test_ambiguous_across_components_is_refused_naming_each(self, monkeypatch):
        c1 = SimpleNamespace(name="A", pmiAnnotations=_NamedCollection([_ann("Note1")]))
        c2 = SimpleNamespace(name="B", pmiAnnotations=_NamedCollection([_ann("Note1")]))
        d = self._design(monkeypatch, [c1, c2])
        ann, comp, err = pg._pmi.find_annotation(d, "Note1")
        assert ann is None and "2 components" in err and "A" in err and "B" in err

    def test_component_scope_disambiguates(self, monkeypatch):
        c1 = SimpleNamespace(name="A", pmiAnnotations=_NamedCollection([_ann("Note1")]))
        c2 = SimpleNamespace(name="B", pmiAnnotations=_NamedCollection([_ann("Note1")]))
        d = self._design(monkeypatch, [c1, c2])
        ann, comp, err = pg._pmi.find_annotation(d, "note1", component="B")
        assert err is None and comp is c2

    def test_miss_lists_available_names(self, monkeypatch):
        c1 = SimpleNamespace(name="A", pmiAnnotations=_NamedCollection([_ann("Note1")]))
        d = self._design(monkeypatch, [c1])
        ann, comp, err = pg._pmi.find_annotation(d, "Nope")
        assert ann is None and "Note1" in err
