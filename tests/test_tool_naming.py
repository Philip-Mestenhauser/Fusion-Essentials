"""Lint/contract for the TOOL NAMING SCHEMA (CLAUDE.md "Read vs Edit").

Every tool name is ``<domain>_<verb>[_<noun>]`` where ``<verb>`` is from a CLOSED set, and the verb's
KIND must agree with the tool's write-status:

  Orient  (orient)                                       -> read
  Read    (get)                                          -> read
  Acquire (find/measure/probe/inspect/select/screenshot/ -> read
           section/compare/list/status)
  Edit    (create/edit/delete/set/add/... the verbs that -> write | destructive
           mutate or run an op)

So a name declares its KIND, and readOnlyHint (from write=) must match: a read-verb tool must be
read-only; an edit-verb tool must not be. This is what makes the name an honest type, not a label.
"""

import re

from conftest import load_tool, TOOLS_DIR
import os

# ── the closed verb vocabulary, grouped by kind ─────────────────────────────────────────────────────

_ORIENT = {"orient"}
_READ = {"get"}
# Acquire: returns a handle/value/image to feed an Edit (no mutation). Expressive verbs kept.
# 'request'/'find' = ask-for / locate (no doc mutation); 'inspect'/'measure'/'probe'/'screenshot'/
# 'section' = derive a value/image; 'compare' = diff; 'list'/'status' = enumerate/poll.
# NB 'select' is NOT here: cam_select_geometry SETS an op's machining geometry (an Edit). The user-pick
# acquisition uses 'request'/'get' (sys_request_selection / sys_get_selection).
_ACQUIRE = {"find", "measure", "probe", "inspect", "screenshot", "section", "compare",
            "list", "status", "capability", "interference", "request", "physical", "compute"}
# Edit: mutates state or runs an async op. The open-ended action set.
_EDIT = {"create", "edit", "delete", "set", "add", "remove", "move", "apply", "generate", "export",
         "convert", "recompute", "activate", "show", "hide", "constrain", "ground", "drive", "arrange",
         "extrude", "revolve", "loft", "chamfer", "fillet", "mirror", "combine", "stitch", "unstitch",
         "hole", "pattern", "base", "construction", "reorder", "save", "open", "close", "new", "copy",
         "insert", "update", "upload", "configure", "reload", "execute", "capture", "rigid", "motion",
         "at", "extend", "offset", "patch", "thicken", "trim", "plane", "reduce", "remesh", "to",
         "dimension", "switch", "select"}

_READ_KIND_VERBS = _ORIENT | _READ | _ACQUIRE       # these MUST be read-only
_EDIT_KIND_VERBS = _EDIT                             # these MUST NOT be read-only

# Tools intentionally exempt from the domain_verb shape (single-token discovery/meta tools).
_SHAPE_EXEMPT = {"workspace_orient"}


def _all_items():
    names = [fn[:-3] for fn in sorted(os.listdir(TOOLS_DIR))
             if fn.endswith(".py") and not fn.startswith("_") and fn != "__init__.py"]
    load_tool(names[0])                       # bootstrap sys.path + the mcpServer.tools stub FIRST
    from mcpServer.mcp_primitives import registry
    registry.reset_registry()
    for n in names:
        mod = load_tool(n)
        reg = getattr(mod, "register_tool", None)
        if callable(reg):
            reg()
    return registry.get_tools()


def _name_and_readonly(item):
    d = item.to_dict()
    name = d.get("name")
    ann = d.get("annotations") or {}
    return name, bool(ann.get("readOnlyHint", False))


_ALL_VERBS = _READ_KIND_VERBS | _EDIT_KIND_VERBS


def _verb_of(name):
    """The verb token of a name. Normally <domain>_<verb>[_noun] (2nd token), but a few tools lead with
    the verb (find_geometry, workspace_orient) - so return the first token that IS a known verb, else
    the 2nd token (so an unknown name still reports its intended slot)."""
    parts = name.split("_")
    for p in parts:
        if p in _ALL_VERBS:
            return p
    return parts[1] if len(parts) >= 2 else parts[0]


class TestToolNaming:
    def test_every_name_is_domain_verb(self):
        bad = []
        for it in _all_items():
            name, _ = _name_and_readonly(it)
            if name in _SHAPE_EXEMPT:
                continue
            if len(name.split("_")) < 2:
                bad.append(name)
        assert not bad, (f"tool names must be <domain>_<verb>[_<noun>] (add to _SHAPE_EXEMPT only for a "
                         f"genuine single-token meta tool): {bad}")

    def test_verb_is_in_the_closed_set(self):
        unknown = {}
        for it in _all_items():
            name, _ = _name_and_readonly(it)
            if name in _SHAPE_EXEMPT:
                continue
            v = _verb_of(name)
            if v not in (_READ_KIND_VERBS | _EDIT_KIND_VERBS):
                unknown[name] = v
        assert not unknown, (f"verbs not in the closed vocabulary (extend the set in test_tool_naming.py "
                             f"if a new verb is genuinely needed): {unknown}")

    def test_verb_kind_matches_write_status(self):
        # the honesty check: a read-kind verb (get/find/probe/...) MUST be read-only; an edit-kind verb
        # MUST NOT be. A mismatch is a mislabeled tool (wrong write= OR a name that lies about what it does).
        mismatches = []
        for it in _all_items():
            name, readonly = _name_and_readonly(it)
            if name in _SHAPE_EXEMPT:
                continue
            v = _verb_of(name)
            if v in _READ_KIND_VERBS and not readonly:
                mismatches.append(f"{name}: read-verb '{v}' but write!=read")
            if v in _EDIT_KIND_VERBS and readonly:
                mismatches.append(f"{name}: edit-verb '{v}' but write=read (mislabeled read?)")
        assert not mismatches, "name/write= disagreements:\n  " + "\n  ".join(mismatches)
