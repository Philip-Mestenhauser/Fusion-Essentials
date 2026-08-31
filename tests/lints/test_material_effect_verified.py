# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: a tool that can REMOVE MATERIAL proves it did, with a material reading.

A write tool whose 'operation' enum offers cut or intersect can take material out of an existing
body - and Fusion reports a healthy feature with result bodies for a cut that swept through empty
air, so nothing the feature object carries can tell the two apart. The only evidence that separates
them is a MATERIAL reading taken across the mutation: a volume diff, a signed volume, a face-count
diff, a lump count, a body census, a physical-body token set.

So for every such tool this requires, in the handler (or a same-module helper it directly calls):
a ``features.<x>.add(`` mutation with one of those material tokens AFTER it, plus an ``error(``
gate on the surface. The target set is keyed off the SCHEMA - any tool whose 'operation' enum
carries cut/intersect is in it - so a new cut-capable tool is covered the day it registers, with no
hand-maintained list to forget.

Deliberately NOT material evidence: ``safe(``, ``.name``, ``.count``, ``.healthState``. Those are
what a rung-1/2 read-back looks like (the feature exists, it has a name, it computed) and every one
of them reads exactly the same for a cut that removed nothing - which is the bug this lint exists
to catch. A tool that genuinely cannot take a material reading goes in ``_MATERIAL_EXEMPT`` with a
reason naming why; that table only shrinks.
"""

import re

from conftest import is_write_tool, register_all_tools
from test_postconditions_declared import _handler_parts

# The mutation this lint anchors on: a features collection's add(). A tool that reaches its
# features collection through a local variable does not match, and needs an exemption saying so.
_MUTATION = re.compile(r"features\.[A-Za-z_][A-Za-z0-9_]*\.add\(")

# MATERIAL evidence - a reading of what the model is MADE OF, taken after the mutation. Word-bounded
# on purpose: a payload key like `input_body_count` echoes a request length and is not a census.
_MATERIAL = re.compile(
    r"\b(?:volume_delta|volumes\(|signed_volume|face_count_delta|lump_count|body_count|"
    r"census_host|native_token|native_identity)\b")

_ERROR_CALL = re.compile(r"\berror\(")

# Tools in the target set that cannot take a material reading. Format: name -> the audited reason,
# prefixed by its class:
#   evidence: - the tool DOES verify its effect, with a reading this lint's material vocabulary
#               does not cover (a mesh carries no volume), named here so the substitute is auditable.
#   shape:    - the tool's cut/intersect operation cannot remove material at all, so there is no
#               material delta to read.
# Shrink-only: an entry goes away when the tool gains a real material reading, and a new entry is a
# deliberate, visible diff - never a quiet exit from the detector.
_MATERIAL_EXEMPT = {
    'mesh_combine': 'evidence: a MeshBody carries no volume, and the meshCombineFeatures collection '
                    'is held in a local before its add() - the target mesh TRIANGLE count is read '
                    'before and after instead, and an unchanged count on a cut/intersect is an '
                    'error(...) naming the non-overlapping tool',
    'model_stitch': 'shape: a stitch only sews surface bodies together - its operation selects how a '
                    'result that CLOSES into a solid joins the model and no stitch removes material '
                    'from anything, so there is no material delta to read; the result bodies isSolid '
                    'flags are the effect and became_solid reports the observed truth',
}


def _cut_capable(item):
    """True when this tool's wire 'operation' enum offers cut or intersect - the schema-keyed
    membership test, so no hand list can go stale."""
    props = (item.to_dict().get("inputSchema") or {}).get("properties", {}) or {}
    enum = (props.get("operation") or {}).get("enum") or []
    return bool({"cut", "intersect"} & set(enum))


def _verifies_material(parts):
    """True when some part performs a features.<x>.add(...) mutation with material evidence AFTER it
    in that same part, and an error(...) gate exists on the surface. Parts are kept separate the way
    test_postconditions_declared keeps them: source order across two different functions says
    nothing about what ran first."""
    if not parts:
        return False
    if not any(_ERROR_CALL.search(p) for p in parts):
        return False
    for part in parts:
        for m in _MUTATION.finditer(part):
            if _MATERIAL.search(part[m.end():]):
                return True
    return False


def _cut_capable_write_tools():
    out = {}
    for it in register_all_tools():
        if not is_write_tool(it):
            continue                        # a read removes no material
        if _cut_capable(it):
            out[it.get_name()] = it
    return out


class TestMaterialEffectVerified:
    def test_every_cut_capable_tool_reads_material_back(self):
        unverified = []
        for name, item in sorted(_cut_capable_write_tools().items()):
            if name in _MATERIAL_EXEMPT:
                continue
            if not _verifies_material(_handler_parts(item)):
                unverified.append(name)
        assert not unverified, (
            "these tools offer a cut/intersect operation but take no MATERIAL reading after their "
            "features.<x>.add(...): a volume/face-count/lump/census/token read, plus an error(...) "
            "gate that fails the call when nothing moved. A feature object, its name, its count and "
            "its healthState all read identically for a cut that removed nothing, so none of them "
            "is evidence. Add the reading, or add a _MATERIAL_EXEMPT entry naming why one cannot be "
            "taken - do NOT weaken this detector:\n  " + "\n  ".join(unverified))

    def test_the_target_set_is_not_empty(self):
        # The membership test reads a wire schema; a rename of the 'operation' input or of the enum
        # values would silently empty the target set and make every assertion above vacuous.
        names = sorted(_cut_capable_write_tools())
        assert len(names) >= 8, (
            f"only {len(names)} cut-capable write tools found ({', '.join(names)}) - the schema key "
            "this lint selects on has moved.")

    def test_exemptions_only_name_real_cut_capable_tools(self):
        targets = _cut_capable_write_tools()
        stale = []
        for name in _MATERIAL_EXEMPT:
            item = targets.get(name)
            if item is None:
                stale.append(f"{name} (not a cut-capable write tool)")
            elif _verifies_material(_handler_parts(item)):
                stale.append(f"{name} (now reads material back - drop the exemption)")
        assert not stale, ("stale _MATERIAL_EXEMPT entries - the table only shrinks:\n  "
                           + "\n  ".join(stale))

    def test_every_exemption_states_its_class(self):
        bad = sorted(n for n, r in _MATERIAL_EXEMPT.items()
                     if not r.startswith(("evidence:", "shape:")))
        assert not bad, ("every _MATERIAL_EXEMPT reason needs a class prefix ('evidence:' - it "
                         "verifies with a reading outside this vocabulary; 'shape:' - its "
                         "cut/intersect cannot remove material):\n  " + "\n  ".join(bad))

    def test_the_detector_bites(self):
        # (a) the shape that must PASS: a material read after the mutation, with an error() gate.
        good = ("def handler():\n"
                "    before = _geom.volumes(bodies)\n"
                "    feature = comp.features.sweepFeatures.add(inp)\n"
                "    delta, readable = _geom.volume_delta(bodies, before)\n"
                "    if readable and abs(delta) < _common.NO_VOLUME_CHANGE_CM3:\n"
                "        return error('this cut changed nothing')\n")
        assert _verifies_material([good])
        # (b) POSITION is load-bearing: the same read taken only BEFORE the add proves nothing.
        before_only = ("def handler():\n"
                       "    before = _geom.volumes(bodies)\n"
                       "    if not before:\n"
                       "        return error('no body to cut')\n"
                       "    feature = comp.features.sweepFeatures.add(inp)\n"
                       "    return {'swept': True}\n")
        assert not _verifies_material([before_only])
        # (c) the rung-1/2 read-backs are deliberately NOT material evidence - each of these reads
        # exactly the same for a cut that removed nothing, which is the whole bug.
        for weak in ("    names = safe(lambda: feature.name)\n",
                     "    n = safe(lambda: feature.bodies.count)\n",
                     "    hs = safe(lambda: feature.healthState)\n"):
            weak_src = ("def handler():\n"
                        "    feature = comp.features.sweepFeatures.add(inp)\n"
                        + weak +
                        "    if not feature:\n"
                        "        return error('no feature')\n")
            assert not _verifies_material([weak_src]), weak
        # (d) an error() gate is still required, and a payload key that merely CONTAINS a token name
        # is not a census.
        no_gate = ("def handler():\n"
                   "    feature = comp.features.sweepFeatures.add(inp)\n"
                   "    delta, readable = _geom.volume_delta(bodies, before)\n"
                   "    return {'volume_delta_cm3': delta}\n")
        assert not _verifies_material([no_gate])
        echoed_key = ("def handler():\n"
                      "    feature = comp.features.stitchFeatures.add(inp)\n"
                      "    payload = {'input_body_count': len(surf_bodies)}\n"
                      "    if not payload:\n"
                      "        return error('nothing')\n")
        assert not _verifies_material([echoed_key])
        # (e) missing source is never confirmed.
        assert not _verifies_material(None)
