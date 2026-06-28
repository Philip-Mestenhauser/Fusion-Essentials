# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: search the LIVE Fusion API documentation from inside the session.

  sys_get_api_doc -> regex-search the Fusion Python API (classes, members, enum values) and return
                 names, signatures, and docstrings. Read-only.

Why this exists: writing Fusion automation means constantly checking exact method signatures and
behavioural notes (e.g. "getOrientedBoundingBox auto-determines the height direction"). Rather than
hosting or bundling a doc database that drifts from the user's installed version, this introspects
the `adsk.*` Python wrapper modules that ship with — and are already imported into — the running
Fusion process. So the docs ALWAYS match the installed Fusion version, need no maintenance, and add
no disk footprint. (This is the same source the docstrings come from: adsk.core/fusion/cam/... .py
wrappers expose __doc__ and signatures via inspect.)

Grounded in: the adsk package modules (adsk.core, adsk.fusion, adsk.cam, adsk.drawing, adsk.sim)
each expose classes whose methods/properties carry __doc__ strings and (for functions) signatures.
Handler is read-only and touches no document, but runs on the main thread for consistency with the
rest of the server.
"""

import importlib
import inspect
import re

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import _ok, _error


# The adsk submodules that hold the public API surface. Imported lazily/best-effort: a given Fusion
# install may not have every one loadable, so we skip any that fail to import.
_API_MODULES = ("adsk.core", "adsk.fusion", "adsk.cam", "adsk.drawing", "adsk.sim")

# Hard caps so a broad regex can't return a megabyte of docs or scan unboundedly.
_MAX_RESULTS = 40
_DOC_CHARS = 1200      # per-item docstring trim
_MAX_CLASS_MEMBERS = 200


def _load_modules(namespace_filter):
    """Return [(dotted_name, module)] for the API modules in scope.

    namespace_filter (e.g. "adsk.cam" or "adsk.fusion.Extrude") narrows the modules walked: only
    modules whose dotted name is a prefix of, or prefixed by, the filter's module part are kept.
    """
    want_mod = None
    if namespace_filter:
        # The filter may be a namespace ("adsk.cam") or namespace.Class ("adsk.fusion.Extrude").
        parts = namespace_filter.split(".")
        # Keep only the leading namespace segments (lowercase) as the module path; a Class segment
        # is TitleCase and handled later as a class-name filter.
        mod_parts = []
        for p in parts:
            if p[:1].isupper():
                break
            mod_parts.append(p)
        want_mod = ".".join(mod_parts) if mod_parts else None

    out = []
    for name in _API_MODULES:
        if want_mod and not (name == want_mod or name.startswith(want_mod + ".")
                             or want_mod.startswith(name + ".") or want_mod == name):
            continue
        try:
            out.append((name, importlib.import_module(name)))
        except Exception:
            continue
    return out


def _class_filter_from(namespace_filter):
    """Extract a TitleCase class-name component from a filter like 'adsk.fusion.Extrude' -> 'Extrude'."""
    if not namespace_filter:
        return None
    for p in namespace_filter.split("."):
        if p[:1].isupper():
            return p
    return None


def _trim(doc):
    doc = (doc or "").strip()
    if len(doc) > _DOC_CHARS:
        doc = doc[:_DOC_CHARS].rstrip() + " …"
    return doc


def _signature(member):
    try:
        return str(inspect.signature(member))
    except (TypeError, ValueError):
        return None


def handler(searchPattern: str = "", apiCategory: str = "all",
            filter: str = "", max_results: int = _MAX_RESULTS) -> dict:
    """Search the live Fusion API docs.

    searchPattern: regex matched (case-insensitive) against names — and, for apiCategory in
    {description, all}, against docstrings too. apiCategory: 'class' (class names) | 'member'
    (property/function/enum names) | 'description' (docstring text) | 'all'. filter: optional
    'adsk.<ns>' or 'adsk.<ns>.<Class>' to scope the search. max_results caps the hits.
    """
    if not searchPattern:
        return _error("Provide 'searchPattern' (a regex matched against API names/docs).")
    try:
        rx = re.compile(searchPattern, re.IGNORECASE)
    except re.error as e:
        return _error(f"Invalid regex 'searchPattern': {e}")

    cat = (apiCategory or "all").strip().lower()
    if cat not in ("class", "member", "description", "all"):
        return _error("apiCategory must be one of: class, member, description, all.")
    try:
        cap = max(1, min(int(max_results), _MAX_RESULTS))
    except Exception:
        cap = _MAX_RESULTS

    modules = _load_modules(filter)
    if not modules:
        return _error(f"No API modules in scope for filter '{filter}'. Try 'adsk.core', "
                      "'adsk.fusion', 'adsk.cam', 'adsk.drawing', or 'adsk.sim'.")
    class_name_filter = _class_filter_from(filter)

    want_class = cat in ("class", "all")
    want_member = cat in ("member", "all")
    want_desc = cat in ("description", "all")

    classes_out = []
    members_out = []
    seen_classes = set()

    for ns, mod in modules:
        for cls_name, cls in inspect.getmembers(mod, inspect.isclass):
            # Only adsk's own classes (skip re-exported builtins / SWIG internals).
            if not getattr(cls, "__module__", "").startswith("adsk"):
                continue
            if class_name_filter and cls_name != class_name_filter:
                continue
            key = (ns, cls_name)

            # --- class-level match ---
            if want_class and key not in seen_classes:
                cdoc = _trim(cls.__doc__)
                if rx.search(cls_name) or (want_desc and cdoc and rx.search(cdoc)):
                    classes_out.append({
                        "type": "class",
                        "name": cls_name,
                        "namespace": ns,
                        "doc": cdoc,
                    })
                    seen_classes.add(key)
                    if len(classes_out) >= cap:
                        want_class = False  # stop collecting classes; members may still match

            # --- member-level match ---
            if want_member or want_desc:
                count = 0
                for m_name, m in inspect.getmembers(cls):
                    if m_name.startswith("_"):
                        continue
                    count += 1
                    if count > _MAX_CLASS_MEMBERS:
                        break
                    mdoc = _trim(getattr(m, "__doc__", "") if not isinstance(m, (int, float, str)) else "")
                    name_hit = want_member and rx.search(m_name)
                    desc_hit = want_desc and mdoc and rx.search(mdoc)
                    if not (name_hit or desc_hit):
                        continue
                    kind = ("function" if (inspect.isfunction(m) or inspect.ismethod(m)
                                           or inspect.isbuiltin(m) or callable(m) and not inspect.isclass(m))
                            else "property")
                    entry = {
                        "type": kind,
                        "name": m_name,
                        "class": cls_name,
                        "namespace": ns,
                        "doc": mdoc,
                    }
                    sig = _signature(m)
                    if sig:
                        entry["signature"] = sig
                    members_out.append(entry)
                    if len(members_out) >= cap:
                        break
            if len(members_out) >= cap:
                break
        if len(members_out) >= cap and (not want_class or len(classes_out) >= cap):
            break

    payload = {
        "search": searchPattern,
        "category": cat,
        "filter": filter or None,
        "classes": classes_out[:cap],
        "members": members_out[:cap],
        "counts": {"classes": len(classes_out[:cap]), "members": len(members_out[:cap])},
        "truncated": (len(classes_out) >= cap or len(members_out) >= cap),
        "note": ("Live introspection of the installed Fusion API (adsk.* docstrings/signatures) — "
                 "always matches this Fusion version. Narrow with 'filter' (e.g. 'adsk.cam' or "
                 "'adsk.fusion.Extrude') and pick 'apiCategory' to focus on class/member/description."),
    }
    return _ok(payload)


TOOL_DESCRIPTION = (
    "Search the LIVE Fusion API documentation (classes, methods, properties, enum values) by regex, "
    "returning names, signatures, and docstrings. This introspects the adsk.* Python modules in the "
    "running Fusion process, so the docs ALWAYS match the installed version — nothing is bundled or "
    "hosted. Use it BEFORE writing an sys_execute_script to confirm exact signatures and behaviour "
    "(e.g. how getOrientedBoundingBox orients its box, what generateAllToolpaths returns). "
    "'searchPattern' is a case-insensitive regex over names (and over docstrings when apiCategory is "
    "'description' or 'all'). 'apiCategory': class | member | description | all (default all). "
    "'filter': optional 'adsk.<namespace>' or 'adsk.<namespace>.<Class>' to scope the search "
    "(e.g. 'adsk.cam', 'adsk.fusion.Extrude'). Read-only."
)

api_doc_tool = (
    Tool.create_simple(name="sys_get_api_doc", description=TOOL_DESCRIPTION)
    .add_input_property("searchPattern", {"type": "string",
                                          "description": "Case-insensitive regex matched against API names (and docstrings when apiCategory is description/all)."})
    .add_input_property("apiCategory", {"type": "string", "enum": ["class", "member", "description", "all"],
                                        "description": "What to search: class names, member names, docstring text, or all (default)."})
    .add_input_property("filter", {"type": "string",
                                   "description": "Optional scope: 'adsk.<namespace>' or 'adsk.<namespace>.<Class>' (e.g. 'adsk.cam', 'adsk.fusion.Extrude')."})
    .add_input_property("max_results", {"type": "integer",
                                        "description": f"Cap on classes and on members returned (default/max {_MAX_RESULTS})."})
    .strict_schema()
)
api_doc_item = Item.create_tool_item(tool=api_doc_tool, handler=handler, run_on_main_thread=True)


def register_tool():
    register(api_doc_item)
