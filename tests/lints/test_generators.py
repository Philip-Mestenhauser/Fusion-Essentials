# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Unit tests for the harness generators (gen_manifest / gen_wiring / gen_posture /
gen_api_surface) and check_all's flag contract.

test_generated_docs_current.py pins output FRESHNESS (the committed doc matches the generator);
these pin the generators' LOGIC (the generator matches the truth). A generator defect that lies
consistently sails through a freshness check, so the load-bearing transforms are pinned here on
synthetic input - above all gen_wiring's per-tool attribution, where sibling tools sharing a name
stem must never swap notes, and the two ways a check can green over a truth it never established
(a stale table nobody could recompute; a name collision each module hides in isolation).
"""

import ast
import os
import types

import pytest

import check_all
import gen_api_surface
import gen_manifest
import gen_posture
import gen_wiring


# ── gen_wiring: registration call-site attribution ───────────────────────────

class TestToolHandlerMap:
    def _map(self, src):
        return gen_wiring._tool_handler_map(ast.parse(src))

    def test_sibling_tools_sharing_a_stem_keep_their_own_handlers(self):
        src = (
            'tool_a = Tool.create_simple(name="doc_save", description=D)\n'
            'item_a = Item.create_tool_item(tool=tool_a, write="write", handler=save_handler)\n'
            'tool_b = Tool.create_simple(name="doc_save_as", description=D2)\n'
            'item_b = Item.create_tool_item(tool=tool_b, write="write", handler=save_as_handler)\n'
        )
        m = self._map(src)
        assert m == {"doc_save": "save_handler", "doc_save_as": "save_as_handler"}

    def test_chained_builder_calls_still_resolve(self):
        src = (
            'tool = (Tool.create_simple(name="view_screenshot", description=D)\n'
            '        .add_input_property("width", {})\n'
            '        .strict_schema())\n'
            'item = Item.create_tool_item(tool=tool, write="read", handler=handler)\n'
        )
        assert self._map(src) == {"view_screenshot": "handler"}

    def test_create_with_string_input_form_resolves(self):
        src = (
            'tool = Tool.create_with_string_input(name="param_set", description=D,\n'
            '                                     input_param_name="name")\n'
            'item = Item.create_tool_item(tool=tool, write="write", handler=set_handler)\n'
        )
        assert self._map(src) == {"param_set": "set_handler"}

    def test_registration_without_a_name_or_handler_is_absent(self):
        src = (
            'tool = Tool.create_simple(description=D)\n'
            'item = Item.create_tool_item(tool=tool, write="read", handler=handler)\n'
            'other = Item.create_tool_item(tool=unknown_var, write="read", handler=h2)\n'
        )
        assert self._map(src) == {}


# ── gen_manifest: family grouping + CLAUDE.md splice ──────────────────────────

class TestGenManifestFamilies:
    def test_first_matching_prefix_wins_and_leftovers_group_as_other(self):
        tools = [{"name": "model_extrude"}, {"name": "cam_get"}, {"name": "zzz_thing"}]
        fam = gen_manifest.families(tools)
        assert any(t["name"] == "model_extrude" for t in fam.get("model", []))
        assert any(t["name"] == "cam_get" for t in fam.get("cam", []))
        assert any(t["name"] == "zzz_thing" for t in fam.get("other", []))

    def test_catalog_escapes_pipes_in_kind_hints(self):
        data = {"kinds": [{"kind": "UnitField", "hint": "mm | cm | in selector", "summary": ""}],
                "tools": [{"name": "model_extrude"}], "helpers": []}
        out = gen_manifest.render_catalog(data)
        assert "mm \\| cm \\| in selector" in out          # an unescaped pipe would break the table


class TestSplice:
    # _splice is generic (path + begin/end markers); the same seam splices the root families census
    # and the tools/CLAUDE.md catalog. Exercised here on a temp file with the catalog markers.
    def _doc(self, tmp_path, body):
        p = tmp_path / "DOC.md"
        p.write_text(body, encoding="utf-8")
        return str(p)

    def test_splice_replaces_only_between_markers(self, tmp_path):
        b, e = gen_manifest._CAT_BEGIN, gen_manifest._CAT_END
        path = self._doc(tmp_path, "before\n" + b + "\nstale\n" + e + "\nafter\n")
        block = b + "\nfresh\n" + e
        assert gen_manifest._splice(path, b, e, block) is False       # it changed something
        text = open(path, encoding="utf-8").read()
        assert "fresh" in text and "stale" not in text
        assert text.startswith("before\n") and text.endswith("after\n")

    def test_check_mode_reports_stale_without_writing(self, tmp_path):
        b, e = gen_manifest._CAT_BEGIN, gen_manifest._CAT_END
        path = self._doc(tmp_path, b + "\nstale\n" + e)
        block = b + "\nfresh\n" + e
        assert gen_manifest._splice(path, b, e, block, check=True) is False
        assert "stale" in open(path, encoding="utf-8").read()          # untouched

    def test_current_content_reports_true(self, tmp_path):
        b, e = gen_manifest._CAT_BEGIN, gen_manifest._CAT_END
        block = b + "\ncurrent\n" + e
        path = self._doc(tmp_path, block)
        assert gen_manifest._splice(path, b, e, block, check=True) is True

    def test_missing_markers_raise_systemexit(self, tmp_path):
        path = self._doc(tmp_path, "no markers here\n")
        with pytest.raises(SystemExit):
            gen_manifest._splice(path, gen_manifest._CAT_BEGIN, gen_manifest._CAT_END, "block")


# ── check_all: --live and --offline are refused together ─────────────────────

class TestCheckAllLiveGateFlags:
    """--live runs the live gate and --offline skips it. Accepting both would discard one silently,
    and the script's whole claim is that skipping live verification is a VISIBLE choice."""

    def test_live_and_offline_together_are_refused_naming_both(self, capsys):
        with pytest.raises(SystemExit) as exc:
            check_all.build_parser().parse_args(["--live", "--offline"])
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "--live" in err and "--offline" in err, err

    def test_each_gate_flag_alone_parses(self):
        assert check_all.build_parser().parse_args(["--live"]).live is True
        assert check_all.build_parser().parse_args(["--offline"]).offline is True

    def test_gate_flags_combine_with_the_scope_flags(self):
        # only the two GATE flags conflict - --fast/--gen still pair with either.
        args = check_all.build_parser().parse_args(["--offline", "--fast"])
        assert (args.offline, args.fast, args.live) == (True, True, False)


# ── gen_api_surface: --check cannot pass without the bindings it compares against ──

class TestApiSurfaceCheckNeedsBindings:
    """The surface table is the only thing that catches a misspelled input property. Without the
    installed bindings the table cannot be recomputed, so --check has established nothing - a
    machine with no Fusion must fail, not green over an arbitrarily stale committed file."""

    def test_check_fails_when_the_bindings_are_absent(self, monkeypatch, capsys):
        monkeypatch.setattr(gen_api_surface, "find_bindings", lambda: None)
        assert os.path.isfile(gen_api_surface.OUT_PATH), "the committed table must exist for this "\
            "test to prove its presence is NOT what makes --check pass"
        with pytest.raises(SystemExit) as exc:
            gen_api_surface.main(["--check"])
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "webdeploy" in err, f"the failure must name the bindings path it needed: {err}"
        assert "api_surface.py" in err

    def test_generate_without_bindings_also_fails(self, monkeypatch, capsys):
        monkeypatch.setattr(gen_api_surface, "find_bindings", lambda: None)
        with pytest.raises(SystemExit) as exc:
            gen_api_surface.main([])
        assert exc.value.code == 1
        assert "webdeploy" in capsys.readouterr().err


# ── name collisions ACROSS modules (each module looks clean in isolation) ─────

class _FakeItem:
    def __init__(self, name):
        self._name = name

    def get_name(self):
        return self._name

    def to_dict(self):
        return {"description": "A fake tool. Second sentence.",
                "annotations": {"readOnlyHint": True},
                "inputSchema": {"properties": {"target": {}}}}


class _FakeRegistry:
    """The seam both walks drive: reset per module, then read back what register_tool() added."""

    def __init__(self):
        self._tools = []

    def reset_registry(self):
        self._tools = []

    def get_tools(self):
        return list(self._tools)

    def add(self, name):
        self._tools.append(_FakeItem(name))


def _fake_modules(reg, spec):
    """{module name -> a stub whose register_tool() registers its tool names into `reg`}."""
    mods = {}
    for mod_name, names in spec.items():
        def _reg(names=names):
            for n in names:
                reg.add(n)
        mods[mod_name] = types.SimpleNamespace(register_tool=_reg)
    return mods


class TestManifestNameCollision:
    def _drive(self, monkeypatch, spec):
        reg = _FakeRegistry()
        mods = _fake_modules(reg, spec)
        monkeypatch.setattr(gen_manifest, "_tool_modules", lambda: sorted(mods))
        monkeypatch.setattr(gen_manifest, "load_tool", lambda n: mods[n])
        monkeypatch.setattr(gen_manifest, "_collect_kinds", lambda: [])
        monkeypatch.setattr(gen_manifest, "_collect_helpers", lambda: [])
        return gen_manifest._collect_unguarded(reg)

    def test_two_modules_registering_one_name_fail_naming_both_files(self, monkeypatch):
        with pytest.raises(SystemExit) as exc:
            self._drive(monkeypatch, {"mod_a": ["doc_save"], "mod_b": ["doc_save"]})
        msg = str(exc.value)
        assert "doc_save" in msg
        assert "tools/mod_a.py" in msg and "tools/mod_b.py" in msg, msg

    def test_distinct_names_across_modules_collect_normally(self, monkeypatch):
        data = self._drive(monkeypatch, {"mod_a": ["doc_save"], "mod_b": ["doc_open"]})
        assert [t["name"] for t in data["tools"]] == ["doc_open", "doc_save"]

    def test_one_module_registering_two_tools_is_not_a_collision(self, monkeypatch):
        data = self._drive(monkeypatch, {"mod_a": ["mesh_export", "save_as_mesh"]})
        assert [t["name"] for t in data["tools"]] == ["mesh_export", "save_as_mesh"]


class TestClaimName:
    def test_reclaiming_by_the_same_module_is_allowed(self):
        owner = {}
        gen_manifest.claim_name(owner, "cam_get", "cam_get")
        gen_manifest.claim_name(owner, "cam_get", "cam_get")
        assert owner == {"cam_get": "cam_get"}

    def test_a_second_module_raises(self):
        owner = {"cam_get": "cam_get"}
        with pytest.raises(SystemExit):
            gen_manifest.claim_name(owner, "cam_get", "cam_read")


# ── gen_wiring: collision + the one-hop note harvest ──────────────────────────

_COLLIDING_MODULE = '''"""Fake tool module."""


def handler(**kwargs):
    return {"note": "a note longer than the twenty-character floor."}


tool = Tool.create_simple(name="doc_save", description=D)
item = Item.create_tool_item(tool=tool, write="write", handler=handler)
'''


class TestWiringNameCollision:
    def test_a_second_module_registering_the_name_fails_instead_of_overwriting(
            self, monkeypatch, tmp_path):
        for mod_name in ("mod_a", "mod_b"):
            (tmp_path / f"{mod_name}.py").write_text(_COLLIDING_MODULE, encoding="utf-8")
        reg = _FakeRegistry()
        mods = _fake_modules(reg, {"mod_a": ["doc_save"], "mod_b": ["doc_save"]})
        monkeypatch.setattr(gen_wiring, "TOOLS_DIR", str(tmp_path))
        monkeypatch.setattr(gen_wiring, "_tool_modules", lambda: sorted(mods))
        monkeypatch.setattr(gen_wiring, "load_tool", lambda n: mods[n])
        with pytest.raises(SystemExit) as exc:
            gen_wiring.collect(registry=reg)
        msg = str(exc.value)
        assert "doc_save" in msg
        assert "tools/mod_a.py" in msg and "tools/mod_b.py" in msg, msg

    def test_distinct_names_record_both_tools(self, monkeypatch, tmp_path):
        for mod_name in ("mod_a", "mod_b"):
            (tmp_path / f"{mod_name}.py").write_text(_COLLIDING_MODULE, encoding="utf-8")
        reg = _FakeRegistry()
        mods = _fake_modules(reg, {"mod_a": ["doc_save"], "mod_b": ["doc_open"]})
        monkeypatch.setattr(gen_wiring, "TOOLS_DIR", str(tmp_path))
        monkeypatch.setattr(gen_wiring, "_tool_modules", lambda: sorted(mods))
        monkeypatch.setattr(gen_wiring, "load_tool", lambda n: mods[n])
        data = gen_wiring.collect(registry=reg)
        assert sorted(data["records"]) == ["doc_open", "doc_save"]


_HOP_MODULE = '''"""Fake rich-read module: the router holds no guidance, the slices hold it all."""


def _slice_geometry(out):
    return {"note": "geometry slice note - call find_geometry for a handle."}


def _deep_helper():
    return {"note": "two hops from the handler and NOT this tool's guidance."}


def _slice_units(out):
    _deep_helper()
    return {"note": "units slice note - lengths are centimetres internally."}


def _unreached_slice(out):
    return {"note": "no call reaches this slice from the handler."}


def handler(**kwargs):
    out = {"note": "router note - pass include= for more."}
    _slice_geometry(out)
    _slice_units(out)
    return out


tool = Tool.create_simple(name="demo_get", description=D)
item = Item.create_tool_item(tool=tool, write="read", handler=handler)
'''

_RECURSIVE_MODULE = '''"""Fake module whose handler calls itself."""


def handler(depth=1, **kwargs):
    if depth:
        handler(depth - 1)
    return {"note": "recursive handler note, long enough to count."}


tool = Tool.create_simple(name="demo_probe", description=D)
item = Item.create_tool_item(tool=tool, write="read", handler=handler)
'''


class TestNoteHarvestFollowsOneCallHop:
    """A rich read's handler is a router: its notes are built in the _slice_* helpers it calls, so
    harvesting only the handler body reports the tool as having no guidance surface at all."""

    def _notes(self, monkeypatch, tmp_path, source, mod_name, tool_name):
        (tmp_path / f"{mod_name}.py").write_text(source, encoding="utf-8")
        monkeypatch.setattr(gen_wiring, "TOOLS_DIR", str(tmp_path))
        notes, _ = gen_wiring._attribute(mod_name, tool_name)
        return notes

    def test_helper_notes_one_hop_from_the_handler_are_harvested(self, monkeypatch, tmp_path):
        notes = self._notes(monkeypatch, tmp_path, _HOP_MODULE, "demo_get_mod", "demo_get")
        assert "router note - pass include= for more." in notes
        assert "geometry slice note - call find_geometry for a handle." in notes
        assert "units slice note - lengths are centimetres internally." in notes

    def test_the_hop_stops_at_one_level(self, monkeypatch, tmp_path):
        # the exact depth boundary: _slice_units is followed (hop 1), the _deep_helper it calls is
        # not (hop 2) - otherwise a shared low-level utility's strings land on every tool above it.
        notes = self._notes(monkeypatch, tmp_path, _HOP_MODULE, "demo_get_mod", "demo_get")
        assert "two hops from the handler and NOT this tool's guidance." not in notes

    def test_a_helper_the_handler_never_calls_is_not_attributed(self, monkeypatch, tmp_path):
        notes = self._notes(monkeypatch, tmp_path, _HOP_MODULE, "demo_get_mod", "demo_get")
        assert "no call reaches this slice from the handler." not in notes

    def test_a_recursive_handler_yields_its_note_once(self, monkeypatch, tmp_path):
        notes = self._notes(monkeypatch, tmp_path, _RECURSIVE_MODULE, "demo_probe_mod", "demo_probe")
        assert notes.count("recursive handler note, long enough to count.") == 1


# ── gen_posture: a write whose EFFECT leaves the document is not a local model write ──

class TestPostureLeavesDocument:
    """The modeling posture promises auto-allow for LOCAL model writes - work an operator sees in
    the timeline and can undo. A write that puts bytes on disk, into a shared library, or restarts
    the add-in is not that, whatever family it sits in."""

    def _presets(self):
        return gen_posture.build_presets(gen_manifest.collect()["tools"])

    def _bare(self, wire_names):
        p = gen_posture.WIRE_PREFIX
        return {w[len(p):] for w in wire_names if w.startswith(p)}

    def test_every_leaves_document_tool_asks_under_modeling(self):
        modeling = self._presets()["modeling"]
        allow, ask = self._bare(modeling["allow"]), self._bare(modeling["ask"])
        leaked = sorted(n for n in gen_posture.LEAVES_DOCUMENT if n in allow)
        assert not leaked, ("the modeling posture auto-allows writes whose effect leaves the "
                            "document: " + ", ".join(leaked))
        missing = sorted(n for n in gen_posture.LEAVES_DOCUMENT if n not in ask)
        assert not missing, "not routed to ask under modeling: " + ", ".join(missing)

    def test_the_named_outward_writes_ask_under_modeling(self):
        # pinned independently of LEAVES_DOCUMENT: dropping an entry from the classifier must go
        # red here, not quietly satisfy a test that iterates the classifier's own keys.
        ask = self._bare(self._presets()["modeling"]["ask"])
        for name in ("cam_post", "cam_edit_tools", "cam_create_machine", "cam_save_template",
                     "cam_generate_setup_sheet", "design_export", "mesh_export", "drawing_export",
                     "sys_reload_addin"):
            assert name in ask, f"{name} writes outside the document and must ask under modeling"

    def test_local_model_writes_stay_auto_allowed(self):
        allow = self._bare(self._presets()["modeling"]["allow"])
        for name in ("model_extrude", "sketch_create", "joint_create", "cam_generate",
                     "view_screenshot", "save_as_mesh"):
            assert name in allow, f"{name} is a local write and must stay auto-allowed under modeling"

    def test_every_named_tool_is_a_registered_write_kind_tool(self):
        # a renamed or read-kind entry would be a silent no-op in the classifier.
        tools = gen_manifest.collect()["tools"]
        kinds = {t["name"]: t["write"] for t in tools}
        wrong = sorted(f"{n} ({kinds.get(n, 'not registered')})"
                       for n in gen_posture.LEAVES_DOCUMENT if kinds.get(n) != "write")
        assert not wrong, ("LEAVES_DOCUMENT names something that is not a registered write-kind "
                           "tool: " + ", ".join(wrong))

    def test_every_named_tool_carries_a_reason(self):
        blank = sorted(n for n, why in gen_posture.LEAVES_DOCUMENT.items() if not (why or "").strip())
        assert not blank, "LEAVES_DOCUMENT entries need the effect stated: " + ", ".join(blank)

    def test_leaves_document_covers_family_and_named_effects(self):
        assert gen_posture.leaves_document("doc_save") is True       # cloud/lifecycle family
        assert gen_posture.leaves_document("cam_post") is True        # named effect
        assert gen_posture.leaves_document("cam_generate") is False   # local toolpath compute
        assert gen_posture.leaves_document("model_extrude") is False

    def test_the_conservative_preset_is_unchanged_by_the_effect_class(self):
        # conservative asks for EVERY write; the effect class only splits the modeling posture.
        tools = gen_manifest.collect()["tools"]
        conservative = gen_posture.build_presets(tools)["conservative"]
        assert self._bare(conservative["ask"]) == {t["name"] for t in tools if t["write"] == "write"}
