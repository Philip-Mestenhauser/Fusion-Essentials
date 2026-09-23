# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared CAM substrate: resolves the active document's CAM product and judges job health, for
cam_get and the CAM action/poll tools (cam_get_status, cam_activate_setup, ...) to reuse."""

import collections
import json
import math
import time

import adsk.core
import adsk.cam

from ._common import (counted, measured, named_with_remainder, native_identity, iter_collection,
                      read_flag, safe, scale, told_apart)
from ._write_guard import _active_identity, document_key, on_key_renamed
from . import _inputs
from . import _view_common

MAP_BLURB = (
    "the CAM substrate every CAM tool starts from: get_cam (the document's CAM product); the ONE "
    "tree walk and its EXACT by-name resolvers (walk_cam_tree, resolve_cam_node, find_setup, "
    "find_operation - a duplicate name is refused); the parameter helpers; the per-op state "
    "classifiers and readiness verdicts (op_state_facts, ready_verdict, live_readiness); the "
    "machine catalog; the bounded library walk")

app = adsk.core.Application.get()


def expression_error(p):
    """(error, warning) off a CAM parameter - a broken expression is stored verbatim and its value
    reads back 0.0, so only .error reveals it, while .warning fires on valid expressions too."""
    err = (safe(lambda: p.error) or "").strip()
    warn = (safe(lambda: p.warning) or "").strip()
    # A .warning string can arrive with its template tokens uninterpolated ('${self.title}').
    if "${" in warn:
        warn += " [the ${...} token is an uninterpolated platform template - cosmetic]"
    return (err or None), (warn or None)


def unquote_expression(expr):
    """The VALUE inside a CAM parameter's stored expression - a string parameter stores it
    single-quoted; None stays None and text with no matching outer pair is returned as read."""
    if expr is None:
        return None
    s = str(expr)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def quote_expression(text):
    """The single-quoted expression a CAM string parameter is written as, with each apostrophe in
    the text escaped so the expression stays closed. unquote_expression takes the wrapper back off."""
    return "'" + str(text).replace("'", "\\'") + "'"


# The platform's own refusal for a value outside a CAM enumeration parameter's set, matched
# case-insensitively: Fusion words it '3 : Invalid enumeration value.'
_ENUM_REFUSAL = "invalid enumeration value"


def matched_quoting(current, request):
    """(the expression to WRITE, whether this call added the quotes) - a parameter whose CURRENT
    expression is quoted holds a string, so bare request text is wrapped to match what it stores."""
    text = str(request)
    if current is None or unquote_expression(current) == str(current):
        return text, False                 # nothing quoted to match
    if unquote_expression(text) != text:
        return text, False                 # already quoted as sent
    return quote_expression(text), True


def choice_expressions(p):
    """The values a CHOICE parameter takes, off ChoiceParameterValue.getChoices() - which answers
    (ok, titles, values) - or None where this parameter's value carries no choice set."""
    got = safe(lambda: p.value.getChoices())
    if not got or len(got) < 3 or not got[0]:
        return None
    values = safe(lambda: [str(v) for v in got[2]])
    return values or None


def choice_quoting(param, current, request):
    """(the expression to WRITE, whether this call quoted it, refusal_or_None) - a request matching
    one of PARAM's own CHOICE values is quoted against THAT set whatever `current` looks like; an
    unmatched request is refused before any write, naming the set; a parameter with no choice set
    falls back to matched_quoting."""
    choices = choice_expressions(param)
    if not choices:
        text, quoted = matched_quoting(current, request)
        return text, quoted, None
    want = unquote_expression(str(request))
    for choice in choices:
        if unquote_expression(choice) == want:
            written = quote_expression(want)
            return written, written != str(request), None
    return None, False, (f"is not one of this parameter's own values: "
                          f"{named_with_remainder(choices)}.")


def enumeration_remedy(message, written, read_call, param=None):
    """The clause a platform refusal naming an INVALID ENUMERATION VALUE carries - the expression
    this call actually wrote, then `param`'s own values where getChoices answers them, else the
    `read_call` that shows what it holds. '' for every other message."""
    if _ENUM_REFUSAL not in (message or "").lower():
        return ""
    lead = (f" Fusion's message names an ENUMERATION value; the expression written was {written}. ")
    choices = choice_expressions(param) if param is not None else None
    if choices:
        return lead + f"This parameter's own values: {named_with_remainder(choices)}."
    return lead + f"{read_call} reads the expression this parameter holds - pass one of its own values."


# The operation's own 'strategy' CAM parameter, whose value is the platform's INTERNAL id
# ('parallel_new') - a different vocabulary from Operation.strategy ('parallel'), which is the name
# createInput takes. createInput('contour') raises: 'contour' is not a name in either.
_STRATEGY_PARAM = "strategy"

STRATEGY_PAIR_NOTE = (
    "'strategy' is the internal strategy id; 'strategy_name' is Operation.strategy - "
    "cam_create_operation and cam_get(include=['strategies']) take strategy_name, not the id.")


def strategy_pair(op) -> dict:
    """{strategy, strategy_name} for ONE operation: the internal id its 'strategy' parameter holds,
    and the createInput name Operation.strategy reads. Either is null where it did not read."""
    params = safe(lambda: op.parameters)
    p = safe(lambda: params.itemByName(_STRATEGY_PARAM)) if params is not None else None
    return {"strategy": unquote_expression(safe(lambda: p.expression)) if p is not None else None,
            "strategy_name": safe(lambda: op.strategy)}


def clamp_rows(max_results, default: int, ceiling: int) -> int:
    """The row cap a capped CAM read runs under: a non-numeric 'max_results' falls back to
    `default`, and the result is held inside 1..`ceiling`."""
    try:
        n = int(max_results or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, ceiling))


def _face_handle(face):
    """A find_geometry-style handle for one recognized face - the address cam_select_geometry
    takes."""
    c = safe(lambda: face.centroid)
    pos = safe(lambda: (c.x, c.y, c.z)) if c is not None else None
    return _inputs.make_handle(face, "face", pos)


def solid_census(design, bodies):
    """(the solid BRep bodies a CAM recognizer runs over, {skip bucket: count}) - the caller's list,
    or every body in the design through the shared census."""
    census = list(bodies) if bodies else _view_common.all_bodies(design)
    solids = []
    skipped = {"surface_bodies_skipped": 0, "mesh_bodies_skipped": 0,
               "unreadable_bodies_skipped": 0}
    for b in census:
        if _inputs._is_mesh(b):
            skipped["mesh_bodies_skipped"] += 1
        elif not _inputs._is_brep(b):
            skipped["unreadable_bodies_skipped"] += 1
        else:
            solid = read_flag(lambda b=b: b.isSolid)
            if solid is True:
                solids.append(b)
            elif solid is False:
                skipped["surface_bodies_skipped"] += 1
            else:
                skipped["unreadable_bodies_skipped"] += 1
    return solids, skipped


def parse_parameters(parameters):
    """(dict {name: expression}, error) for a 'parameters' request given as a dict or a
    'name=value, ...' string."""
    if isinstance(parameters, dict):
        out = {str(k).strip(): str(v) for k, v in parameters.items() if str(k).strip()}
        return out, None
    if isinstance(parameters, str):
        out = {}
        for chunk in parameters.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                return None, f"'{chunk}' is not 'name=value'. Use name=value pairs, or a JSON object."
            k, _, v = chunk.partition("=")
            if k.strip():
                out[k.strip()] = v.strip()
        return out, None
    return None, "Provide 'parameters' as an object {name: value} or a 'name=value, ...' string."


# The last synced get_cam() call's reading, taken by the readiness verdicts built in that call.
_VALIDITY_SYNCED = [True]

# checkValidity's own return is not what says it ran - only that the call did not raise.
_SYNC_RAISED = object()

_SYNC_MISS_CLAUSE = " Validity not synced this call."

_SYNC_MISS = (
    "checkValidity raised on this call, so operation validity was not synced against the current "
    "model and an operation a model edit staled can still read valid. A view_switch_workspace "
    "round trip out to 'design' and back performs the same sync.")


def sync_validity(cam) -> bool:
    """Whether CAM.checkValidity() ran, re-checking every operation against the CURRENT model."""
    # MEASURED: a model edit alone leaves operationState valid while a drill's face selection has
    # decayed to bodies; this call marks the affected operations out of date and re-resolves that
    # selection, and on a SAVED document it leaves isModified False. Safe mid-generation.
    ran = safe(lambda: cam.checkValidity(), _SYNC_RAISED) is not _SYNC_RAISED
    _VALIDITY_SYNCED[0] = ran
    return ran


def validity_sync_clause() -> str:
    """The clause a readiness verdict carries when this call's validity sync raised, else ''."""
    return "" if _VALIDITY_SYNCED[0] else _SYNC_MISS_CLAUSE


def validity_sync_miss() -> str:
    """What a raised sync leaves the verdict beside it standing on, for its own payload key - '' when
    the sync ran."""
    return "" if _VALIDITY_SYNCED[0] else _SYNC_MISS


def with_validity_clause(verdict: str) -> str:
    """`verdict` plus this call's unsynced-validity clause, held inside the wire budget: the clause
    is the half an agent must not miss, so a verdict past the budget is the half that gives way."""
    clause = validity_sync_clause()
    if not clause:
        return verdict
    room = _MISS_BUDGET - len(clause)
    return (verdict if len(verdict) <= room else verdict[:room - 3].rstrip() + "...") + clause


def get_cam(sync: bool = False):
    """The active document's CAM product, or (None, reason) - readable in any workspace. sync=True
    re-checks every operation's validity against the current model first, for a caller about to
    publish a validity verdict, launch a generation or post."""
    doc = safe(lambda: app.activeDocument)
    if not doc:
        return None, "No active document."
    products = safe(lambda: doc.products)
    if products is None:
        return None, "Could not access document products."
    cam = safe(lambda: adsk.cam.CAM.cast(products.itemByProductType('CAMProductType')))
    if not cam:
        return None, ("This document has no CAM (Manufacture) product yet - a fresh design gains "
                      "one on first entry: call view_switch_workspace('manufacture') once, then "
                      "retry this call.")
    if sync:
        sync_validity(cam)
    return cam, None


# The turning holder JSON's own short field codes (measured on a 'turning general' sample), read
# only when none of the milling keys above answer - a milling and a turning holder never share a
# JSON shape.
_TURNING_HOLDER_KEYS = (("THSC", "type"), ("LH", "head_length"), ("OAL", "overall_length"),
                       ("W", "shank_width"), ("H", "shank_height"), ("HAND", "hand"),
                       ("MTP", "clamping"))


def tool_holder(t):
    """A CAM Tool's holder identity: milling {name, product_id, vendor, segment_count}, or - when
    those are absent - turning {type, head_length, overall_length, shank_width, shank_height,
    hand, clamping}; None only when the JSON carries no holder dict at all."""
    raw = safe(lambda: t.toJson())
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except Exception:
        return None
    h = d.get("holder") if isinstance(d, dict) else None
    if not isinstance(h, dict):
        return None
    out = {}
    if h.get("description"):
        out["name"] = h["description"]
    if h.get("product-id"):
        out["product_id"] = h["product-id"]
    if h.get("vendor"):
        out["vendor"] = h["vendor"]
    segs = h.get("segments")
    if isinstance(segs, list) and segs:
        out["segment_count"] = len(segs)
    if not out:
        for key, field in _TURNING_HOLDER_KEYS:
            if h.get(key) is not None:
                out[field] = h[key]
    return out or None


# key -> the CAM parameter carrying it on an adsk.cam.Tool, each reading a LENGTH in cm. The first
# four are present on a bundled milling tool, tool_cornerRadius reading 0 on a square end
# (measure_api cam-tool-dimension-parameter-names); the rest read off a document-library end mill.
_TOOL_DIMENSION_PARAMS = (("diameter", "tool_diameter"),
                          ("flute_length", "tool_fluteLength"),
                          ("corner_radius", "tool_cornerRadius"),
                          ("overall_length", "tool_overallLength"),
                          ("shoulder_length", "tool_shoulderLength"),
                          ("shoulder_diameter", "tool_shoulderDiameter"),
                          ("shaft_diameter", "tool_shaftDiameter"),
                          ("body_length", "tool_bodyLength"),
                          ("holder_gauge_length", "tool_holderGaugeLength"),
                          ("assembly_gauge_length", "tool_assemblyGaugeLength"))

# The one COUNT among them: a flute count is not a length, so it is published unscaled.
_TOOL_FLUTES_PARAM = "tool_numberOfFlutes"

# The family flag tool_dimensions() reads to pick the turning shape (CAM tool sample columns rule:
# a family flag selects the size field, never presence or a positive value).
_TURNING_FLAG_PARAM = "tool_isTurning"

# insert_size/cutting_width are LENGTHS (scaled); the rest are the insert's own text/enum values -
# measured on a 'turning general' sample via cam_edit_tools(action='parameters').
_TURNING_LENGTH_PARAMS = (("insert_size", "tool_insertSize"), ("cutting_width", "tool_cuttingWidth"))
_TURNING_RAW_PARAMS = (("insert_type", "tool_insertType"), ("hand", "tool_hand"),
                       ("clamping", "tool_clamping"))


def tool_dimension_value(p, factor=None):
    """One TOOL parameter's number, scaled by `factor` (raw when None) or None when it did not read
    or its expression failed to evaluate - a failed CAM expression stores verbatim and reads a
    finite 0.0, so .error is the only channel separating a real zero from an unusable one."""
    if p is None or expression_error(p)[0]:
        return None
    if factor is None:
        return safe(lambda: p.value.value)
    return measured(lambda: p.value.value, factor)


def tool_dimensions(t, factor, unit):
    """A CAM Tool's own geometry, shaped by the tool_isTurning family flag: milling/drill/jet reads
    cutter/shoulder/shaft/gauge lengths plus flutes, turning reads insert type/size, cutting width,
    hand and clamping - each length scaled from cm by 'factor', null where a parameter is absent,
    does not read, or holds an expression that will not evaluate."""
    params = safe(lambda: t.parameters)

    def _param(pname):
        return safe(lambda: params.itemByName(pname)) if params is not None else None

    if tool_dimension_value(_param(_TURNING_FLAG_PARAM)) is True:
        out = {key: tool_dimension_value(_param(pname), factor)
               for key, pname in _TURNING_LENGTH_PARAMS}
        for key, pname in _TURNING_RAW_PARAMS:
            out[key] = tool_dimension_value(_param(pname))
        out["units"] = unit
        return out
    out = {key: tool_dimension_value(_param(pname), factor)
           for key, pname in _TOOL_DIMENSION_PARAMS}
    # the COUNT carries the same false-zero channel as the lengths: a probe's flute count reads 0
    # with .error 'Number of Flutes must be positive and non-zero!'.
    flutes = _param(_TOOL_FLUTES_PARAM)
    out["flutes"] = (counted(lambda: flutes.value.value)
                     if flutes is not None and not expression_error(flutes)[0] else None)
    out["units"] = unit
    return out


def setups(cam):
    """Every Setup in the document, as a list - the basis for the tree walk below."""
    return list(iter_collection(safe(lambda: cam.setups)))


def setup_names(cam):
    """Every setup's name, for a 'not found, available: ...' message - built one way everywhere."""
    return [safe(lambda s=s: s.name) for s in setups(cam)]


# One node of the CAM tree: kind is the cast the child answered to, path the 'Setup / Folder / Op'
# breadcrumb, parent the node that contained it (a name carrying ' / ' splits the path wrong).
CamNode = collections.namedtuple("CamNode", ["obj", "kind", "name", "setup", "path", "parent"],
                                 defaults=(None,))


# The segment a breadcrumb carries for a level whose own name did not read: a None joined into an
# f-string would print the literal 'None' and read as a complete address.
_UNREAD_SEGMENT = "(name unread)"


def _segment(name):
    """One breadcrumb segment: the name, or _UNREAD_SEGMENT when the read answered None."""
    return _UNREAD_SEGMENT if name is None else name


# The per-KIND ordered collection one parent keeps each class of child in - the row a reorder moves
# inside, and the membership a folder listing reads. The tree walk below reads `children` instead.
CHILD_COLLECTIONS = {"operation": "operations", "folder": "folders", "pattern": "patterns"}

# A child is classified by CAST, never by the collection that yielded it: `children` is ONE ordered
# list holding the operations, folders and patterns PLUS the additive containers and the bare nodes
# those three collections never list.
_CHILD_CASTS = (("folder", "CAMFolder"), ("pattern", "CAMPattern"),
                ("container", "CAMAdditiveContainer"))

# The kinds that HOLD other nodes; every other kind is a leaf this walk does not descend into.
_PARENT_KINDS = ("folder", "pattern", "container")


def _child_kind(child):
    """The CamNode kind ONE child reads as: folder/pattern/container by cast, 'operation' for an
    Operation, and 'base' for a child that casts as none of them - the hole-recognition and
    individual-strategies nodes, which are an OperationBase and nothing else."""
    for kind, type_name in _CHILD_CASTS:
        family = getattr(adsk.cam, type_name, None)
        if family is not None and safe(lambda family=family: family.cast(child)) is not None:
            return kind
    return "operation" if safe(lambda: adsk.cam.Operation.cast(child)) is not None else "base"


def _walk_children(parent, setup_name, path, out, parent_node=None):
    """Collect CamNodes for everything nested under `parent` (a Setup, or a container below one),
    off the ONE `children` list Fusion keeps them in - a parent whose `children` does not read holds
    nothing this walk can reach, which is what both base nodes answer."""
    for child in iter_collection(safe(lambda: parent.children)):
        kind = _child_kind(child)
        nm = safe(lambda child=child: child.name)
        child_path = f"{path} / {_segment(nm)}"
        node = CamNode(child, kind, nm, setup_name, child_path, parent_node)
        out.append(node)
        if kind in _PARENT_KINDS:
            _walk_children(child, setup_name, child_path, out, node)


def _setup_node(s):
    # The setup is the ROOT segment of every path under it, so it is disclosed the same way: a setup
    # whose name did not read leaves the marker rather than an empty leading segment, which would
    # make ' / Op' read as an operation with no container at all.
    nm = safe(lambda: s.name)
    return CamNode(s, "setup", nm, nm, _segment(nm), None)


def setup_nodes(cam):
    """A CamNode per Setup in the document, in cam.setups order - the rootless pool every setup
    resolve runs over, and the order a position-sensitive caller reads."""
    return [_setup_node(s) for s in setups(cam)]


def tree_nodes(setup_obj):
    """CamNodes for ONE setup subtree: the setup itself, then every operation, folder, pattern,
    container and base node under it - the setup-scoped slice of walk_cam_tree. A caller wanting
    operations alone filters kind == 'operation'."""
    node = _setup_node(setup_obj)
    nodes = [node]
    _walk_children(setup_obj, node.name, node.path, nodes, node)
    return nodes


def walk_cam_tree(cam):
    """Every node of the CAM tree as CamNode(obj, kind, name, setup, path, parent): each Setup plus
    every operation, folder, pattern, container and base node under it. The ONE traversal every CAM
    tool walks and resolves names over; kind is what narrows it."""
    nodes = []
    for s in setups(cam):
        nodes.extend(tree_nodes(s))
    return nodes


def operation_nodes(cam):
    """Every OPERATION node of the CAM tree - walk_cam_tree filtered to kind == 'operation'."""
    return [n for n in walk_cam_tree(cam) if n.kind == "operation"]


def nc_program_nodes(cam):
    """Every NC program as a CamNode of kind 'nc_program' - they hang off cam.ncPrograms, outside
    the setup tree walk_cam_tree covers, and carry no setup or parent."""
    out = []
    for p in iter_collection(safe(lambda: cam.ncPrograms)):
        nm = safe(lambda p=p: p.name)
        out.append(CamNode(p, "nc_program", nm, None, _segment(nm), None))
    return out


def op_labels(nodes):
    """What each operation row is NAMED by: its own name, or - where several rows in this list share
    that name - its 'Setup / ... / op' path, plus the row's POSITION where that path repeats too.
    The substitution is _common.told_apart: a name that already identifies one row is left alone."""
    per_path = {}
    for n in nodes:
        per_path[n.path] = per_path.get(n.path, 0) + 1
    rows = []
    for position, n in enumerate(nodes, 1):
        disc = n.path
        if disc and per_path[disc] > 1:
            disc = f"{disc} (operation {position})"
        rows.append((n.name, disc))
    return told_apart(rows)


# The address that picks ONE of several nodes sharing a name: '<name>#<n>', n counting from 1 over
# the same-named nodes in the walk's own order - the order the ambiguity refusal lists them in.
_ORDINAL_SEP = "#"


def _ordinal_address(want):
    """('<base name>', <1-based ordinal>) when `want` is spelled '<name>#<n>', else (want, None) -
    split on the LAST separator, so a node whose own name carries one is still addressable."""
    base, sep, tail = want.rpartition(_ORDINAL_SEP)
    if sep and base.strip() and tail.strip().isdigit():
        return base.strip(), int(tail.strip())
    return want, None


def _ordinal_reading(pool, asked):
    """How `asked` reads as a '<name>#<n>' address over `pool`: (the node it addresses or None, the
    base name, the ordinal or None, every node named `base`)."""
    base, ordinal = _ordinal_address(asked)
    if ordinal is None:
        return None, base, None, []
    same = [n for n in pool if (n.name or "").lower() == base.lower()]
    hit = same[ordinal - 1] if 1 <= ordinal <= len(same) else None
    return hit, base, ordinal, same


def _candidate_label(node, ordinal):
    """One row of an ambiguity refusal: this node's '<name>#<n>' address plus its path, or its
    operation count where the node is a setup (whose path is its own bare name)."""
    addr = f"{node.name}{_ORDINAL_SEP}{ordinal}"
    if node.kind != "setup":
        return f"{addr} (at {node.path})"
    n = len(operations_under(node.obj))
    return f"{addr} ({n} operation{'' if n == 1 else 's'})"


def _free_sibling_addresses(pool, same):
    """The '<name>#<n>' addresses over `same` that no node in `pool` CARRIES as a literal name -
    a literal name wins here, so those addresses are no handle on the sibling they count to."""
    carried = {(n.name or "").lower() for n in pool}
    return [addr for addr in (f"{n.name}{_ORDINAL_SEP}{i}" for i, n in enumerate(same, 1))
            if addr.lower() not in carried]


_PATH_ADDRESS_HINT = " A 'Setup / operation' path (or 'Setup / Folder / operation') is also accepted."


def resolve_cam_node(cam, name, kinds=("operation",), setup=None, label=None, nodes=None):
    """(CamNode, None) for the one node of a kind in `kinds` matching `name` case-insensitively and
    exactly - or matching its own PATH first when `name` carries ' / ' (two nodes can share a bare
    name a path tells apart); no hit lists the available names, and 2+ hits are REFUSED with each
    duplicate's '<name>#<n>' address. `setup` scopes the walk, `nodes` reuses a caller's own walk."""
    if setup is not None:
        nodes = tree_nodes(setup)
    elif nodes is not None:
        pass                                  # the caller's own walk, reused rather than repeated
    elif set(kinds) == {"setup"}:
        nodes = setup_nodes(cam)
    else:
        nodes = walk_cam_tree(cam)
    label = label or "/".join(kinds)
    asked = (name or "").strip()
    want = asked.lower()
    pool = [n for n in nodes if n.kind in kinds]
    # Path hint text only teaches an OPERATION resolve (a folder/setup path never resolves one).
    path_capable = "operation" in kinds
    matches = [n for n in pool if (n.name or "").lower() == want]
    if " / " in asked:
        path_hits = [n for n in pool if (n.path or "").lower() == want]
        if len(path_hits) > 1:
            # Two nodes share one PATH when same-named siblings sit under one parent - refused
            # rather than picked, same as two nodes sharing a bare name.
            rows = [_candidate_label(n, i) for i, n in enumerate(path_hits, 1)]
            return None, (f"'{asked}' addresses {len(path_hits)} {label} sharing that path: "
                          f"{named_with_remainder(rows)}. Retry with one of those '<name>#<n>' "
                          "addresses.")
        if len(path_hits) == 1:
            if matches and matches[0] is not path_hits[0]:
                # One spelling, two readings: the PATH of one node and the literal NAME of a
                # different one both equal this string - the same collision the ordinal address
                # is refused for below, so this is refused rather than guessed too.
                return None, (f"'{asked}' reads two ways here: the {label} AT that path "
                              f"({_candidate_label(path_hits[0], 1)}), and the {label} literally "
                              f"NAMED '{asked}'. Both exist, so the address does not identify "
                              "one. cam_get(include=['operations']) lists every one with its own "
                              "name and path.")
            return path_hits[0], None
            # No path hit at all: the literal name wins, unchanged below.
    addressed, base, ordinal, same = _ordinal_reading(pool, asked)
    if not matches:
        # A literal name wins over the ordinal address: the plain match above runs first, so a node
        # actually NAMED 'Face#2' resolves by its own name, and '#n' is read only where no node
        # carries that spelling.
        if addressed is not None:
            return addressed, None
        if ordinal is not None and same:
            rows = [_candidate_label(n, i) for i, n in enumerate(same, 1)]
            return None, (f"'{asked}' addresses item {ordinal} of the {len(same)} named "
                          f"'{base}', which are numbered 1 to {len(same)}. Address one of: "
                          f"{named_with_remainder(rows)}.")
        available = [n.name for n in pool if n.name]
        # Capped by NAME COUNT, with the remainder COUNTED - never by character, which can end the
        # list mid-name and print a spelling no caller can pass back.
        hint = _PATH_ADDRESS_HINT if path_capable else ""
        return None, (f"No {label} named '{name}'. Available: "
                      f"{named_with_remainder(available) or '(none)'}." + hint)
    if len(matches) == 1 and addressed is not None and addressed is not matches[0]:
        # Both readings resolve to DIFFERENT nodes and no spelling on this input separates them, so
        # nothing is picked. The ordinal reading dies once fewer than `ordinal` nodes carry `base`,
        # and the renames are ordered highest address first so each one leaves the rest addressable.
        free = _free_sibling_addresses(pool, same)
        need = len(same) - ordinal + 1
        chosen = list(reversed(free[-need:])) if len(free) >= need else []
        remedy = ""
        if need == 1 and chosen:
            remedy = (f" Rename any ONE of the items named '{base}', addressed here as "
                      f"{named_with_remainder(free)}, so fewer than {ordinal} carry '{base}' - "
                      f"'{asked}' then reads only as the {label} carrying it.")
        elif chosen:
            remedy = (f" Rename {need} of the items named '{base}' - {named_with_remainder(chosen)}"
                      f" - in the order given, so fewer than {ordinal} carry '{base}' and '{asked}' "
                      f"then reads only as the {label} carrying it. Take them in that order: each "
                      f"rename leaves one FEWER item named '{base}', and the highest number first "
                      "keeps every address still to come inside the set that remains.")
        return None, (f"'{asked}' reads two ways here: the {label} CARRYING that name, and item "
                      f"{ordinal} of the {len(same)} named '{base}' "
                      f"({_candidate_label(addressed, ordinal)}). Both exist, so the address does "
                      "not identify one." + remedy)
    if len(matches) > 1:
        rows = [_candidate_label(n, i) for i, n in enumerate(matches, 1)]
        # The path each row's '(at ...)' already carries resolves directly too - named here so the
        # fragile '<name>#<n>' address is not the only remedy on offer.
        also = " The path in parentheses is also accepted." if path_capable else ""
        return None, (f"'{name}' is ambiguous - {len(matches)} CAM items share that name: "
                      f"{named_with_remainder(rows)}. Retry with one of those '<name>#<n>' "
                      "addresses; the number counts the items of that name in the order listed "
                      "here." + also)
    return matches[0], None


def operation_nodes_under(node):
    """Every operation CamNode nested under one setup/folder/pattern/container NODE - the walk
    filtered to kind 'operation' - each keeping the 'Setup / ... / op' breadcrumb that separates
    two operations of one name."""
    nodes = []
    _walk_children(node.obj, node.setup, node.path, nodes, node)
    return [n for n in nodes if n.kind == "operation"]


def operations_under(parent):
    """Every real Operation nested anywhere under one setup/folder/pattern/container OBJECT - the
    walk filtered to kind 'operation', so no folder, container or base node rides along; a caller
    naming the results takes operation_nodes_under instead."""
    nodes = []
    _walk_children(parent, None, "", nodes)
    return [n.obj for n in nodes if n.kind == "operation"]


# The walk builds the parent chain, so it is acyclic by construction; the hop cap is only there so
# a hand-built node can never spin the climb below forever.
_MAX_PARENT_HOPS = 64


def owning_setup(node):
    """The Setup OBJECT a CamNode sits under, off the walk's parent links; None where the chain
    does not reach one."""
    seen = 0
    while node is not None and seen <= _MAX_PARENT_HOPS:
        if node.kind == "setup":
            return node.obj
        node = node.parent
        seen += 1
    return None


# What each write path would LAND instead, and so the scope its clash census reads. MEASURED: the
# create goes through operations.add, which dedupes DOCUMENT-WIDE to '<name> (2)'; the rename sets
# Operation.name, which dedupes inside one setup and takes a twin in another setup exactly.
CREATE_DEDUPE = ("a create dedupes across the whole document to '{want} (2)', a name nothing asked "
                 "for; pick a name no operation carries")
RENAME_DEDUPE = ("Operation.name dedupes rather than refusing, so it would land as something like "
                 "'{want}1' - a name nothing asked for. Pick one no operation in this setup "
                 "carries; a twin in another setup is accepted")


def operation_name_clash(pool, want, current="", dedupe=RENAME_DEDUPE):
    """The refusal for a name an operation in `pool` already answers to, else None. `pool` is the
    scope the CALLING path dedupes over - operation_nodes(cam) for a create, tree_nodes(setup) for
    a rename - and `dedupe` says what that path would land instead; `current` never clashes."""
    want = (want or "").strip()
    if not want or want.lower() == (current or "").strip().lower():
        return None
    taken = [n for n in pool
             if n.kind == "operation" and (n.name or "").lower() == want.lower()]
    if not taken:
        return None
    where = named_with_remainder(sorted({n.setup for n in taken if n.setup}))
    return (f"{len(taken)} operation(s) already answer to '{want}' (in setup '{where}'): "
            + dedupe.format(want=want) + ". cam_get(include=['operations']) lists them.")


def find_setup(cam, name):
    """(setup, available_names, error) for the unique Setup named `name` - the error is
    resolve_cam_node's own refusal text, which callers return verbatim."""
    node, err = resolve_cam_node(cam, name, kinds=("setup",), label="setup")
    return (node.obj if node else None), setup_names(cam), err


def walk_operations(cam):
    """Every real Operation across every setup, folder/pattern-nested INCLUDED."""
    return [n.obj for n in operation_nodes(cam)]


def _operation_listing(pool, name):
    """The available list an operation miss carries: each duplicate's 'Setup / op' breadcrumb where
    several share `name`, every operation name otherwise."""
    want = (name or "").strip().lower()
    dupes = [n for n in pool if (n.name or "").lower() == want]
    return [n.path for n in dupes] if len(dupes) > 1 else [n.name for n in pool]


def find_operation(cam, name):
    """(operation, available_names) for the unique Operation named `name` anywhere in the CAM tree;
    a DUPLICATED name is refused and the list carries each duplicate's 'Setup / op' path."""
    pool = operation_nodes(cam)
    want = (name or "").strip().lower()
    matches = [n for n in pool if (n.name or "").lower() == want]
    return (matches[0].obj if len(matches) == 1 else None), _operation_listing(pool, name)


def resolve_operation(cam, name, label="operation"):
    """(node, error, available) - the unscoped operation resolve plus the available list, both off
    ONE walk, for a caller wording its own remedy."""
    pool = operation_nodes(cam)
    node, err = resolve_cam_node(cam, name, kinds=("operation",), label=label, nodes=pool)
    return node, err, _operation_listing(pool, name)


def first_line(text) -> str:
    """The first line of a fault message, '' when there is none."""
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""


def first_error_line(obj):
    """First line of an object's .error, '' if none."""
    return first_line(safe(lambda: obj.error))


def first_warning_line(obj):
    """First line of an object's .warning, '' if none."""
    return first_line(safe(lambda: obj.warning))


# The rail-PAIR drive parameter: cam_select_geometry feeds two contours into it, and cam_get_status
# keys its rail triage on carrying it. One name, or a rename leaves the triage pointing at nothing.
SWARF_CONTOURS_PARAM = "swarfContours"


def is_rail_driven(op) -> bool:
    """Whether an operation carries the rail-PAIR drive parameter - what a rail promise is keyed on."""
    return safe(lambda: op.parameters.itemByName(SWARF_CONTOURS_PARAM)) is not None


def op_is_suppressed(facts: dict) -> bool:
    """Whether an operation is SUPPRESSED - answered from EITHER the isSuppressed flag or
    operation_state Suppressed (2), since a raised flag arrives as False."""
    return bool(facts.get("is_suppressed") or facts.get("operation_state") == 2)


def op_settled(facts: dict) -> bool:
    """Whether an operation has nothing left to generate: error/suppressed, or IsValid (0) with
    either a toolpath to show for it or isGenerating reading not-true - the FLAG alone never
    settles a poll by itself."""
    if facts.get("has_error") or op_is_suppressed(facts):
        return True
    # MEASURED on a REgeneration: the state leaves 0 the instant a launch lands (0 -> 3 -> 1) and
    # returns to 0 only once the work is done, while isGenerating stayed true 1.1 s past the
    # Future's own completion.
    if facts.get("operation_state") != 0:
        return False
    # MEASURED on a FIRST generation (no prior toolpath): the state reads 0 AT ONCE and stays there
    # the whole run - isGenerating true is the only signal a still-landing path leaves at state 0.
    return facts.get("has_toolpath") is True or facts.get("is_generating") is not True


def unsettled_count(tally: dict) -> int:
    """How many operations a tally is still waiting on: the isGenerating count less the ones whose
    own state has already answered."""
    return max(0, (tally.get("generating", 0) or 0) - (tally.get("generating_settled", 0) or 0))


def settled_clause(tally: dict) -> str:
    """The disclosure for a scope holding operations that read isGenerating true over a state that
    already answered - '' where the two counts agree."""
    n = tally.get("generating_settled", 0) or 0
    if not n:
        return ""
    return (f" {n} operation(s) read isGenerating true while their own state reads valid, errored "
            "or suppressed; this read settles completion on those states, not on the flag.")


def counts_as_warning(facts: dict) -> bool:
    """Whether an op's warning counts toward the readiness overlay - an errored op's warning adds
    nothing to its error, and suppression discards the toolpath, so neither demotes a verdict."""
    return (bool(facts.get("has_warning")) and not facts.get("has_error")
            and not op_is_suppressed(facts))


# The three knobs cam.getMachiningTime takes: feed scale percent, rapid feed cm/s, tool change s.
_TIME_PROBE_ARGS = (100.0, 10.58, 1.5)

# machiningTime is an int64 MICROSECOND counter in seconds, and a path whose motion did not compute
# reads it SATURATED: 2**63 / 1e6 = 9223372036854.775 s, a FINITE float an isfinite gate misses.
_SATURATED_TIME_S = 2 ** 63 / 1e6


def _read_nonfinite(getter) -> bool:
    """Whether a number READ as NaN or an infinity - one that did not read at all is no measurement
    and is not one either."""
    value = safe(getter)
    return isinstance(value, float) and not math.isfinite(value)


def saturated_time(seconds) -> bool:
    """Whether a machining time that READ is the saturated counter rather than a duration - a second
    under it, so the comparison does not rest on the last bit of a float."""
    return seconds is not None and seconds >= _SATURATED_TIME_S - 1.0


def time_reading(mt):
    """(seconds, nonfinite) for ONE MachiningTime: the seconds as a MEASUREMENT - None where they
    did not read, read non-finite or read the saturated counter - and whether this reading says the
    motion is not a number. MEASURED together: the counter saturates while both distances read
    NaN."""
    seconds = measured(lambda: mt.machiningTime)
    saturated = saturated_time(seconds)
    nonfinite = bool(saturated or _read_nonfinite(lambda: mt.machiningTime)
                     or _read_nonfinite(lambda: mt.feedDistance)
                     or _read_nonfinite(lambda: mt.rapidDistance))
    return (None if saturated else seconds), nonfinite

# A Manual NC operation carries no toolpath by construction, reading state IsValid (0) with
# hasToolpath False - the empty class's own flag shape. Its op.strategy reads exactly 'manual'.
_MANUAL_NC_STRATEGY = "manual"


def is_additive_setup(setup) -> bool:
    """Whether a Setup's operationType reads AdditiveOperation - what takes no cutting tool and no
    spindle by design, the same way a manual NC operation takes no toolpath."""
    return safe(lambda: setup.operationType) == adsk.cam.OperationTypes.AdditiveOperation


def _model_identities(setup):
    """The native identities of one Setup's `models`, or None where the collection did not read -
    Setup.models raises on some setups, so a caller comparing setups treats None as unknown."""
    collection = safe(lambda: setup.models)
    if collection is None:
        return None
    ids = set()
    try:
        for m in collection:
            ident = native_identity(m)
            if ident is not None:
                ids.add(ident)
    except Exception:
        return None
    return ids


def setups_sharing_models(cam, setup):
    """Names of every OTHER non-additive Setup sharing a model body with `setup` (native
    identity) - [] if none share one, or `setup`'s own models did not read."""
    mine = _model_identities(setup)
    if not mine:
        return []
    # Excluded BY NAME, never `is`/`==` on the wrapper: an unread or misclassified operationType
    # must not report `setup` as sharing a model with itself.
    my_name = safe(lambda: setup.name)
    shared = []
    for s in setups(cam):
        if is_additive_setup(s) or safe(lambda s=s: s.name) == my_name:
            continue
        theirs = _model_identities(s)
        if theirs and mine & theirs:
            shared.append(safe(lambda s=s: s.name))
    return shared


def _machining_time(cam, op, state, has_toolpath):
    """(seconds, nonfinite) for ONE operation - the seconds None where they were not read or read
    the band, and nonfinite True where this reading says the motion is not a number."""
    # getMachiningTime raises on an operation holding no toolpath, and costs a platform
    # computation, so it runs only where the flags already say one generated.
    if cam is None or state != 0 or has_toolpath is not True:
        return None, False
    mt = safe(lambda: cam.getMachiningTime(op, *_TIME_PROBE_ARGS))
    return (None, False) if mt is None else time_reading(mt)


def op_state_facts(op, cam=None) -> dict:
    """ONE safe read of an operation's raw lifecycle state, every classifier here reads from.
    operation_state carries an adsk.cam.OperationStates member: IsValid 0, IsInvalid 1,
    Suppressed 2, NoToolpath 3. `cam` adds machining_time; omitted, it reads None."""
    state = safe(lambda: op.operationState)
    # read_flag keeps True/False/None apart: a coerced False on an unreadable flag would invent
    # the generated-but-empty state is_empty_toolpath reads off this pair.
    has_toolpath = read_flag(lambda: op.hasToolpath)
    seconds, nonfinite = _machining_time(cam, op, state, has_toolpath)
    return {
        "name": safe(lambda: op.name),
        "strategy": safe(lambda: op.strategy),
        "has_error": bool(safe(lambda: op.hasError, False)),
        "has_warning": bool(safe(lambda: op.hasWarning, False)),
        "is_suppressed": bool(safe(lambda: op.isSuppressed, False)),
        "is_generating": bool(safe(lambda: op.isGenerating, False)),
        "operation_state": state,
        "generating_progress": safe(lambda: op.generatingProgress),
        "has_toolpath": has_toolpath,
        "is_toolpath_valid": read_flag(lambda: op.isToolpathValid),
        "machining_time": seconds,
        "nonfinite_toolpath": nonfinite,
    }


def is_empty_toolpath(facts: dict, additive: bool = False) -> bool:
    """True for the EMPTY class: an operation that generated, is not suppressed, and cuts nothing.
    `additive` joins the manual-NC exclusion - an additive build op carries no toolpath by
    construction too, the caller's own read of is_additive_setup on its owning setup."""
    # 'valid' is answered off operationState IsValid (0) only, so the empty claim rests on a state
    # that was READ; a state that raised buckets _UNREAD_STATE and stops here.
    if (facts.get("strategy") == _MANUAL_NC_STRATEGY
            or additive
            or op_primary_state(facts) != "valid"
            or facts.get("is_toolpath_valid") is not True):
        return False
    if facts.get("has_toolpath") is False:
        # A path still landing (isGenerating true) is IN FLIGHT, not empty.
        return facts.get("is_generating") is not True
    # Only a time that READ 0.0 says the operation cut nothing; an unread time is no measurement.
    return facts.get("has_toolpath") is True and facts.get("machining_time") == 0.0


def toolpath_present_tally(ops):
    """(rows reading hasToolpath True and not errored, rows whose flag did not read) - an errored
    row reads hasToolpath True but a post will not write it."""
    present = unread = 0
    for row in (ops or []):
        flag = read_flag(lambda row=row: row.hasToolpath)
        if flag is None:
            unread += 1
        elif flag and not safe(lambda row=row: row.hasError, False):
            present += 1
    return present, unread


def op_state_tally(ops, cam=None) -> dict:
    """The operation lifecycle tally, with unread states and generating/warning overlays. `cam`
    buys the nonfinite bucket - the reading that separates a valid toolpath from one whose motion
    is not a number; without it those ops count valid, as every other flag reads them."""
    valid = ood = errored = generating = suppressed = unread = warnings = total = 0
    generating_settled = 0
    active = None
    op_sample = None
    warning_sample = None
    nonfinite_names = []
    for raw in (ops or []):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        facts = op_state_facts(op, cam)
        total += 1
        if counts_as_warning(facts):
            warnings += 1                            # OVERLAY: the op still lands in a bucket below
            if warning_sample is None:
                warning_sample = {"name": facts["name"], "warning": first_warning_line(op)}
        if facts["has_error"]:
            errored += 1                             # FAILED, not pending - its own bucket
            if op_sample is None:
                op_sample = {"name": facts["name"], "error": first_error_line(op)}
            continue
        state = facts["operation_state"]
        if state == 0:
            if facts["nonfinite_toolpath"]:
                nonfinite_names.append(facts["name"] or _UNREAD_SEGMENT)
            else:
                valid += 1
        elif state == 2:
            suppressed += 1
        elif state in (1, 3):
            ood += 1
        else:
            unread += 1
        if facts["is_generating"]:
            generating += 1
            if op_settled(facts):
                generating_settled += 1
            prog = facts["generating_progress"]
            if active is None or (prog and prog not in ("Pending", "0.0%")):
                active = {"op": facts["name"], "progress": prog}
    return {"valid": valid, "out_of_date": ood, "errored": errored, "generating": generating,
            "generating_settled": generating_settled,
            "suppressed": suppressed, "unread": unread, "warnings": warnings, "total": total,
            "nonfinite": len(nonfinite_names), "nonfinite_names": nonfinite_names,
            "active": active, "op_sample": op_sample, "warning_sample": warning_sample}


def _warning_phrase(sample) -> str:
    """The 'name - first warning line' clause a readiness verdict names its first warning by."""
    if not sample or not sample.get("name"):
        return "cam_get(include=['operations']) lists which."
    text = (sample.get("warning") or "").strip()
    return f"'{sample['name']}'" + (f" - {text}" if text else " (warning text unreadable).")


# The setup's stock mode: the wire key beside the SetupStockModes member that assigns it.
# 'previous_setup' takes the stock a PRECEDING setup left behind, which is the mill-turn rest flow.
STOCK_MODES = {
    "fixed_box": "FixedBoxStock",
    "relative_box": "RelativeBoxStock",
    "fixed_cylinder": "FixedCylinderStock",
    "relative_cylinder": "RelativeCylinderStock",
    "fixed_tube": "FixedTubeStock",
    "relative_tube": "RelativeTubeStock",
    "from_solid": "SolidStock",
    "previous_setup": "PreviousSetupStock",
}


def stock_mode_member(key):
    """The SetupStockModes value one STOCK_MODES key assigns, or None where this build lacks it."""
    return getattr(adsk.cam.SetupStockModes, STOCK_MODES[key], None)


def stock_mode_name(value):
    """The STOCK_MODES key a Setup.stockMode value reads as, or None when it matches no member."""
    if value is None:
        return None
    return next((key for key in STOCK_MODES if stock_mode_member(key) == value), None)


# The setup-level blocker vocabulary: each code beside the tool that clears it.
_SETUP_BLOCKER_REMEDY = {"no_machine_selected": "cam_edit_setup assigns a machine"}


def setup_blockers(setup) -> list:
    """The verified setup-level blocker codes for ONE setup, present-and-empty when none."""
    return [] if machine_label(safe(lambda: setup.machine)) else ["no_machine_selected"]


def blocked_setup_records(setup_objs) -> list:
    """[{name, blocked_by}] for the setups that carry a blocker - the shape a readiness signal
    publishes and ready_verdict words its clause from. Setups with none are simply absent."""
    rows = []
    for s in setup_objs or []:
        codes = setup_blockers(s)
        if codes:
            rows.append({"name": safe(lambda s=s: s.name), "blocked_by": codes})
    return rows


# How many blocked setups a verdict NAMES; the rest ride as a count, so the sentence stays one line.
_BLOCKED_ROWS_NAMED = 1


def _blocked_phrase(named, total: int) -> str:
    """The 'name (codes)' clause a verdict names its blocked setups by, plus a count of the `total`
    it left out."""
    first = named[0] if named else {}
    name = (first.get("name") or "").strip()
    shown = f"'{name}'" if name else "a setup whose name did not read"
    codes = ", ".join(c for c in (first.get("blocked_by") or []) if c) or "code unreported"
    more = total - len(named)
    return f"{shown} ({codes})" + (f" and {more} more" if more > 0 else "")


def _blocked_remedies(blocked) -> str:
    """The remedy clause for the codes actually PRINTED, deduped in first-seen order; a code with
    no known remedy contributes none."""
    codes = [c for row in (blocked or []) for c in (row.get("blocked_by") or [])]
    remedies = list(dict.fromkeys(_SETUP_BLOCKER_REMEDY[c] for c in codes
                                  if c in _SETUP_BLOCKER_REMEDY))
    return (" " + "; ".join(remedies) + ".") if remedies else ""


def ready_verdict(measure: str, warned: int, warning_sample, blocked=None) -> str:
    """The ONE 'this scope is postable' sentence over the caller's own count clause (`measure`),
    the warned-op count and `blocked` (blocked_setup_records for the caller's setups): a blocker
    withholds the claim, and warnings are stated and named rather than blocking."""
    if blocked:
        named = list(blocked)[:_BLOCKED_ROWS_NAMED]     # the rows the sentence actually prints
        also = f" {warned} active op(s) also carry warnings." if warned else ""
        return (f"{measure}, but {len(blocked)} setup(s) carry blocked_by: "
                f"{_blocked_phrase(named, len(blocked))}"
                f" - 'ready to post' is NOT established.{also}" + _blocked_remedies(named))
    if not warned:
        return f"{measure} - ready to post."
    return (f"{measure}, {warned} with WARNINGS - postable, but read the warnings first: "
            f"{_warning_phrase(warning_sample)}")


# ── strategy entitlement: whether this INSTALLATION will generate a strategy at all ──────────────


def _create_strategy(name):
    """Factored to one line so tests patch this seam."""
    return adsk.cam.OperationStrategy.createFromString(name)


def strategy_generation_allowed(name):
    """True / False / None for ONE strategy NAME's isGenerationAllowed, off the OperationStrategy
    factory - which needs no document, CAM product or setup. None where the name did not build
    (createFromString RAISES on an unknown one) or the flag itself did not read."""
    if not name:
        return None
    strat = safe(lambda: _create_strategy(name))
    if strat is None:
        return None
    return read_flag(lambda: strat.isGenerationAllowed)


# The strategies whose isGenerationAllowed verdicts this install's Manufacturing Extension
# entitlement - needs no document or setup.
CAPABILITY_SENTINELS = ("steep_and_shallow", "multiaxis_finishing", "swarf", "probe_geometry")


def entitled_over(flags) -> object:
    """True/False/None over an iterable of isGenerationAllowed flags - True only where every one
    reads true, None where any did not read, so an unread flag never folds into a confident False."""
    values = list(flags)
    if any(v is None for v in values):
        return None
    return all(values)


def capability_entitled():
    """This install's Manufacturing Extension verdict - entitled_over the CAPABILITY_SENTINELS'
    own isGenerationAllowed flags, probed fresh."""
    return entitled_over(strategy_generation_allowed(name) for name in CAPABILITY_SENTINELS)


def entitlement_flags(ops) -> list:
    """One True / False / None per operation - its own strategy's isGenerationAllowed - probed ONCE
    per distinct strategy name. False is what a generation-blocked operation reads; None is a flag
    that did not read, which is no entitlement verdict at all."""
    seen, out = {}, []
    for op in ops or []:
        name = safe(lambda op=op: op.strategy)
        if name not in seen:
            seen[name] = strategy_generation_allowed(name)
        out.append(seen[name])
    return out


# The bucket for an operation whose operationState never answered: not valid, not finished, and no
# claim about the lifecycle - the honest place for a read that raised.
_UNREAD_STATE = "unread"

# The buckets a blocked operation is NOT waiting on generation in - naming one of these points past
# the operations a scope is actually stuck on. _UNREAD_STATE is not one of them: nothing read says
# that operation is done.
_FINISHED_STATES = ("valid", "suppressed")


def entitlement_blocked_names(ops) -> list:
    """The names of the operations that read isGenerationAllowed False AND still have generating
    left to do - one already reading valid, or suppressed, is not what the scope is waiting on."""
    rows = []
    for op, flag in zip(ops or [], entitlement_flags(ops)):
        if flag is not False or op_primary_state(op_state_facts(op)) in _FINISHED_STATES:
            continue
        rows.append(safe(lambda op=op: op.name) or _UNREAD_SEGMENT)
    return rows


def unread_verdict(measure: str, unread: int, unsettled: int, stale: int = 0) -> str:
    """A readiness verdict for operationState reads that did not answer."""
    state = f"{unread} operation(s) have unread operationState"
    if unsettled:
        return (f"{measure}; {state} while isGenerating reads true - poll cam_get_status before "
                "launching generation again.")
    reread = (f"{measure}; {state} - re-read with cam_get in the Manufacture workspace before "
              "choosing a next step.")
    if not stale:
        return reread
    return (reread + f" Separately, {stale} operation(s) read out_of_date; run cam_generate for "
            "those after the unread state is resolved.")


def unfinished_verdict(measure: str, ops, unsettled: int = 0) -> str:
    """The readiness verdict for unfinished ops, separating active generation from idle work."""
    names = entitlement_blocked_names(ops)
    if unsettled:
        active = (f"{measure} - {unsettled} operation(s) still generating; poll cam_get_status "
                  "before launching generation again.")
        if not names:
            return active
        return (active + f" {len(names)} operation(s) in scope also read isGenerationAllowed "
                "false and cam_generate EXCLUDES those from a launch: "
                f"{named_with_remainder(names)}.")
    if not names:
        return f"{measure} - run cam_generate to finish the rest."
    return (f"{measure}; {len(names)} operation(s) in scope read isGenerationAllowed false and "
            f"cam_generate EXCLUDES those from a launch: {named_with_remainder(names)}. Run "
            "cam_generate for the rest.")


# What an errored item of each kind blocks, in the order errored_verdict counts them. MEASURED: a
# setup carrying an errored OPERATION reads hasError itself ("One or more items have errors") and
# still POSTS its other valid toolpaths - only an errored NC program blocks its own whole re-post.
_ERROR_BLOCKS = (("setup", "each still posts its valid toolpaths and skips the errored item"),
                 ("NC program", "those programs will not re-post; other posts land"),
                 ("operation", "will not post; the rest of their setup does"))


def errored_verdict(setups: int, programs: int, operations: int) -> str:
    """The readiness verdict for the errored items in scope, naming what each KIND blocks - only
    the kinds actually present."""
    return "BLOCKER: " + " ".join(
        f"{n} {kind}(s) have errors - {blocks}."
        for n, (kind, blocks) in zip((setups, programs, operations), _ERROR_BLOCKS) if n)


# MEASURED on a turning path whose feed and rapid distances read NaN: the post stopped with this.
# The one home - every surface that names a nonfinite path ends on this sentence.
NONFINITE_POST = "The post failed 'Number to be formatted is not a number (NaN)' on such a path."

# The one home of the empty-toolpath remedy - every surface naming an empty toolpath ends on this.
EMPTY_TOOLPATH_REMEDY = ("cam_generate(target=<op>) relaunches it; if it is still empty, check "
                         "the heights and the selection.")


def nonfinite_verdict(measure: str, names) -> str:
    """The readiness verdict for operations whose toolpath motion is not a number - they read
    IsValid with a toolpath, so nothing else in the tally demotes them."""
    lead = (f"BLOCKER: {measure}; {len(names)} operation(s) read a toolpath whose motion is NOT A "
            "NUMBER: ")
    tail = ". " + NONFINITE_POST + " Change what they cut and regenerate before posting."
    return lead + named_with_remainder(names, cap=_length_capped(names, lead, tail)) + tail


def readiness_verdict(tally: dict, ops=(), warning_sample=None, blocked=None,
                       setups_errored: int = 0, programs_errored: int = 0) -> str:
    """The ONE readiness ladder live_readiness and cam_get_status's scoped poll both read an
    op_state_tally-shaped `tally` through: errored > nonfinite > unread > UNSETTLED generation
    (ahead of ready - a state-0 op counts 'valid' in the tally while its own generation is still
    landing, so it must not read ready) > ready > unfinished > no active operations."""
    valid, ood, errored = tally["valid"], tally["out_of_date"], tally["errored"]
    nonfinite = tally.get("nonfinite", 0)
    active_total = valid + ood + errored + nonfinite   # active = everything not suppressed
    unsettled = unsettled_count(tally)
    unread = tally.get("unread", 0)
    measure = (f"{valid} of {active_total} active ops valid" if active_total else
               f"{tally['total']} operation(s) in scope")
    if errored or setups_errored or programs_errored:
        return errored_verdict(setups_errored, programs_errored, errored)
    if nonfinite:
        return nonfinite_verdict(measure, tally.get("nonfinite_names") or [])
    if unread:
        return unread_verdict(measure, unread, unsettled, ood)
    if active_total and unsettled:
        return unfinished_verdict(measure, ops, unsettled)
    if active_total and valid == active_total:
        return ready_verdict(measure, tally.get("warnings", 0), warning_sample, blocked)
    if active_total:
        return unfinished_verdict(measure, ops, unsettled)
    return "no active operations to assess."


def live_readiness():
    """(signal, None) or (None, reason) - the CAM readiness signal for the active document: the op
    tally, the setup- and NC-program-level errors, setups_blocked, one sample per level, and the
    readiness verdict over them."""
    cam, err = get_cam(sync=True)
    if err:
        return None, err
    samples = {"op": None, "setup": None, "program": None, "warning": None}
    try:
        ops = walk_operations(cam)
        tally = op_state_tally(ops, cam)
        samples["op"] = tally["op_sample"]
        samples["warning"] = tally["warning_sample"]
        setups_errored = 0
        setup_objs = setups(cam)
        for s in setup_objs:
            if safe(lambda s=s: s.hasError, False):
                setups_errored += 1
                if samples["setup"] is None:
                    samples["setup"] = {"name": safe(lambda s=s: s.name), "error": first_error_line(s)}
        blocked = blocked_setup_records(setup_objs)
        programs_errored = 0
        progs = safe(lambda: cam.ncPrograms)
        for i in range(safe(lambda: progs.count, 0) if progs else 0):
            p = safe(lambda i=i: progs.item(i))
            if p is not None and safe(lambda p=p: p.hasError, False):
                programs_errored += 1
                if samples["program"] is None:
                    samples["program"] = {"name": safe(lambda p=p: p.name), "error": first_error_line(p)}
    except Exception as e:
        return None, str(e)
    readiness = readiness_verdict(tally, ops, samples["warning"], blocked,
                                  setups_errored, programs_errored)
    signal = {"valid": tally["valid"], "out_of_date": tally["out_of_date"], "errored": tally["errored"],
              "generating": tally["generating"],
              "generating_settled": tally["generating_settled"],
              "suppressed": tally["suppressed"], "unread": tally["unread"],
              "nonfinite": tally["nonfinite"],
              "warnings": tally["warnings"], "total": tally["total"],
              "active": tally["active"],
              "setups_errored": setups_errored, "programs_errored": programs_errored,
              "setups_blocked": blocked,
              "readiness": with_validity_clause(readiness), "samples": samples}
    miss = validity_sync_miss()
    if miss:
        signal["validity_not_synced"] = miss     # absent = this call's sync ran
    return signal, None


def op_primary_state(facts: dict) -> str:
    """The ONE lifecycle bucket an op falls in, priority-ordered so each op counts once: suppressed
    (flag) > error > generating > suppressed (state) > no_toolpath > out_of_date > nonfinite >
    valid, and 'unread' where operationState answered nothing. A warning is an OVERLAY."""
    if facts["is_suppressed"]:
        return "suppressed"
    if facts["has_error"]:
        return "error"
    # The FLAG outranks the state EXCEPT over an operation reading IsValid with a toolpath to show:
    # it stayed true for 1.1 s past the Future's completion (measured), and 'generating' there is
    # read as unfinished work. Spelled out rather than via op_settled, which reaches back here.
    if facts["is_generating"] and not (facts.get("operation_state") == 0
                                       and facts.get("has_toolpath") is True):
        return "generating"
    if op_is_suppressed(facts):     # the STATE half; the flag half already answered above
        return "suppressed"
    state = facts["operation_state"]
    if state == 3:
        return "no_toolpath"
    if state == 1:
        return "out_of_date"
    if state == 0:
        # A path whose own motion is not a number reads IsValid with a toolpath to show. The signal
        # only exists where the facts were taken WITH the CAM product, so this cannot widen.
        return "nonfinite" if facts.get("nonfinite_toolpath") else "valid"
    # operationState reads None where the property RAISED, and every value it answers IS a state, so
    # anything else is no lifecycle answer at all.
    return _UNREAD_STATE


def validity_basis():
    """'manufacture_verified' iff the Manufacture workspace is active - op validity is only
    trustworthy there - else 'unverified_design_workspace'."""
    try:
        ws = app.userInterface.activeWorkspace
        if ws and ws.id == "CAMEnvironment":
            return "manufacture_verified"
    except Exception:
        pass
    return "unverified_design_workspace"


# ── async generation registry - where every launch path parks its live GenerateToolpathFuture ─────

# Live generations by handle, held for the life of the add-in session. Fusion ABANDONS an
# in-progress generation once its GenerateToolpathFuture is garbage-collected, so an entry is
# popped only after that generation completed.
_GENERATIONS = {}
_HANDLE_SEQ = [0]


def _carry_generation_keys(old_key, new_key):
    """Re-stamp every live generation launched under a document key that just changed - a save
    re-keys an open document."""
    for entry in _GENERATIONS.values():
        if entry.get("doc_key") == old_key:
            entry["doc_key"] = new_key


on_key_renamed(_carry_generation_keys)


def futures_count(futures, member):
    """The int `member` ('numberOfOperations' / 'numberOfCompleted') summed over these Futures, or
    None when any ONE of them did not read - both raise until generation spins up, and a partial
    sum would publish a smaller job than the handle covers."""
    total = 0
    for f in futures or []:
        n = counted(lambda f=f: getattr(f, member))
        if n is None:
            return None
        total += n
    return total


def register_future(future, target, scope, skip_valid, target_name="", also=()):
    """(handle, total) - mint a handle for a live generation Future and register it with the
    document it was launched from and, for a scoped launch, the raw `target_name` a status read
    settles this handle's completion on. `also` holds the SIBLING futures of a launch split across
    operations: every one is kept referenced, and the status read settles on all of them."""
    _HANDLE_SEQ[0] += 1
    handle = f"gen{_HANDLE_SEQ[0]}"
    futures = [future] + list(also)
    total = futures_count(futures, "numberOfOperations")
    doc_name, doc_urn = _active_identity()
    _GENERATIONS[handle] = {
        "future": future,
        "futures": futures,
        "target": target,
        "scope": scope,
        "target_name": (target_name or "").strip(),
        "skip_valid": bool(skip_valid),
        "started_at": time.time(),
        "total": total,
        "doc_name": doc_name,
        "doc_urn": doc_urn,
        "doc_key": document_key(),
        "doc": safe(lambda: app.activeDocument),
    }
    return handle, total


# ── machine library: the locations, the catalog, and the by-name resolver ───────────────────────

# The non-network locations every CAM library walk here searches - machines and print settings both
# (Fusion360 = the bundled assets; Local = the user's saved ones). The cloud/network locations are
# skipped so a headless read never blocks on a fetch.
_LIBRARY_LOCATIONS = ("LocalLibraryLocation", "Fusion360LibraryLocation")

# Machine.capabilities flags -> the 'kind' vocabulary (the bundled library is DOMINATED by
# additive printers, so an unfiltered read floods - machine_type narrows to the relevant kind).
_MACHINE_KINDS = {"milling": "isMillingSupported", "turning": "isTurningSupported",
                  "cutting": "isCuttingSupported", "additive": "isAdditiveSupported"}


def machine_library():
    """The shared MachineLibrary - it hangs off CAMManager.get().libraryManager, not the document's
    CAM product, so no open CAM job is needed. Returns (library, None) or (None, error)."""
    lib = safe(lambda: adsk.cam.CAMManager.get().libraryManager.machineLibrary)
    if lib is None:
        return None, "Could not access the machine library (CAMManager.libraryManager.machineLibrary)."
    return lib, None


def machine_location(lib, machine):
    """'local' when a Local (vendor, model) query answers this machine's id, 'fusion360' by
    elimination, and 'local or fusion360' when that query RAISED. Machine.id is the description, so
    either copy of a shared name reads 'local' - the copy an assignment by that name reaches."""
    vendor, model = (safe(lambda: machine.vendor) or ""), (safe(lambda: machine.model) or "")
    fid = safe(lambda: machine.id)
    try:
        loc = adsk.cam.LibraryLocations.LocalLibraryLocation
        for m in (lib.createQuery(loc, vendor, model).execute() or []):
            if safe(lambda m=m: m.id) == fid:
                return "local"
    except Exception:
        return "local or fusion360"
    return "fusion360"


def machine_kinds(m):
    """ONE machine's 'kind' labels off its capabilities flags - the expensive part of a machine
    row, so a caller needing one machine's kinds calls this instead of walking the catalog."""
    caps = safe(lambda: m.capabilities)
    return [k for k, attr in sorted(_MACHINE_KINDS.items())
            if bool(safe(lambda caps=caps, attr=attr: getattr(caps, attr), False))]


# MEASURED: the shipped 'Tormach 1500MX' description carries a trailing newline, which would reach
# the wire raw in the machine catalog's 'name' and in cam_edit_setup's machine_set.
def machine_label(m):
    """Readable machine label: .description stripped, else 'vendor model'. adsk.cam.Machine has no .name."""
    if not m:
        return None
    desc = safe(lambda: m.description)
    desc = desc.strip() if desc else desc
    if desc:
        return desc
    label = ((safe(lambda: m.vendor) or "") + " " + (safe(lambda: m.model) or "")).strip()
    return label or "(unnamed machine)"


def machine_ident(m):
    """(label, vendor, model) for a Machine - label is the readable name (description or 'vendor model')."""
    return machine_label(m), (safe(lambda: m.vendor) or ""), (safe(lambda: m.model) or "")


# ── the machine's own limits: spindle speed + axis travels, off its kinematics tree ───────────────
# The route is Machine.elements -> the KinematicsMachineElement -> .parts, a TREE whose parts carry
# .children plus an optional .axis / .spindle / .toolStation.
_MACHINE_PART_DEPTH = 8       # the kinematics tree nests one part per axis; bound the recursion
_MACHINE_PART_CAP = 200

# MachineAxis.physicalRange is documented in CM for a linear axis and RADIANS for a rotary one, so
# the axis TYPE decides the unit a travel can be reported in. A build carrying neither member leaves
# the kind None and the range is published unconverted rather than in a guessed unit.
_AXIS_KINDS = (("linear", "LinearMachineAxisType"), ("rotary", "RotaryMachineAxisType"))


def _axis_kind(axis_type):
    """'linear' / 'rotary' for a MachineAxis.axisType value, or None when it matches neither."""
    if axis_type is None:
        return None
    for label, member in _AXIS_KINDS:
        if axis_type == safe(lambda member=member: getattr(adsk.cam.MachineAxisTypes, member)):
            return label
    return None


def _finite(value):
    """A number only when it is FINITE. An unbounded axis range reads -inf/+inf, and infinity is not
    a travel (nor valid JSON for a strict client), so it is dropped in favour of is_infinite."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return value if math.isfinite(value) else None


def kinematics_parts(machine):
    """Every MachinePart under the machine's kinematics element, flattened, or None when the machine
    exposes no kinematics element at all (which is a different answer from a machine whose tree is
    empty). Bounded on depth and part count."""
    elements = safe(lambda: machine.elements)
    if elements is None:
        return None
    type_id = safe(lambda: adsk.cam.KinematicsMachineElement.staticTypeId())
    if not type_id:
        return None
    element = safe(lambda: elements.defaultItemByType(type_id))
    if element is None:
        # defaultItemByType answers only for the element whose id is the default one; a machine that
        # carries the type under another id is still reachable through the filtered list.
        items = safe(lambda: elements.itemsByType(type_id)) or []
        element = items[0] if items else None
    if element is None:
        return None
    out = []
    _walk_machine_parts(safe(lambda: element.parts), 0, out)
    return out


def _walk_machine_parts(parts, depth, out):
    for p in iter_collection(parts):
        if len(out) >= _MACHINE_PART_CAP:
            return
        out.append(p)
        if depth < _MACHINE_PART_DEPTH:
            _walk_machine_parts(safe(lambda p=p: p.children), depth + 1, out)


def _spindle_records(parts) -> list:
    """{max_rpm, min_rpm} for every spindle in the parts list, highest maxSpeed first; a maxSpeed
    of 0 reads None and sorts last."""
    rows = []
    for part in (parts or []):
        sp = safe(lambda part=part: part.spindle)
        if sp is None:
            continue
        rpm = measured(lambda sp=sp: sp.maxSpeed)
        rows.append({"max_rpm": (rpm if rpm else None),
                     "min_rpm": measured(lambda sp=sp: sp.minSpeed)})
    rows.sort(key=lambda r: (r["max_rpm"] is None, -(r["max_rpm"] or 0.0)))
    return rows


def machine_spindle_max(machine):
    """The machine's highest readable spindle maxSpeed in rpm, or None when no spindle answers
    one - the number every per-operation over-max comparison is made against."""
    if machine is None:
        return None
    rows = _spindle_records(kinematics_parts(machine))
    return rows[0]["max_rpm"] if rows else None


def _axis_record(axis, factor, unit):
    """One axis row: its name, kind, and travel in `unit` when the kind says the range is a length."""
    kind = _axis_kind(safe(lambda: axis.axisType))
    rec = {"name": safe(lambda: axis.name), "kind": kind,
           "has_limits": read_flag(lambda: axis.hasLimits)}
    rng = safe(lambda: axis.physicalRange)
    if rng is None:
        return rec
    infinite = read_flag(lambda: rng.isInfinite)
    rec["is_infinite"] = infinite
    # read at full precision and round ONCE, at the end: rounding radians to 6 places first turns
    # a half-turn into 180.00002 deg.
    lo, hi = _finite(measured(lambda: rng.min, 1.0, 12)), _finite(measured(lambda: rng.max, 1.0, 12))
    if lo is None or hi is None:
        return rec
    if kind == "linear":
        rec["travel"] = round((hi - lo) * factor, 6)
        rec["range"] = [round(lo * factor, 6), round(hi * factor, 6)]
        rec["units"] = unit
    elif kind == "rotary":
        rec["travel_deg"] = round(math.degrees(hi - lo), 6)
        rec["range_deg"] = [round(math.degrees(lo), 6), round(math.degrees(hi), 6)]
    else:
        # No decodable axis type: the range is published as the API returned it, with no unit
        # claimed (see _AXIS_KINDS).
        rec["range_raw"] = [lo, hi]
    return rec


def machine_limits(machine, factor, unit) -> dict:
    """{spindle, axes, tool_stations, kinematics_readable} for ONE machine - the spindle speed range
    and per-axis travels its kinematics tree carries."""
    parts = kinematics_parts(machine)
    if parts is None:
        return {"kinematics_readable": False, "spindle": None, "axes": []}
    spindles = _spindle_records(parts)
    out = {"kinematics_readable": True, "spindle": (spindles[0] if spindles else None), "axes": []}
    if len(spindles) > 1:
        out["spindle_count"] = len(spindles)     # 'spindle' is the fastest of them
    stations = []
    for part in parts:
        ax = safe(lambda part=part: part.axis)
        if ax is not None:
            out["axes"].append(_axis_record(ax, factor, unit))
        st = safe(lambda part=part: part.toolStation)
        if st is not None:
            row = {}
            # An UNSET tool-station field reads 0.0, so a zero is never published as a limit. The
            # values are scaled as CM, the unit the bindings document for physicalRange; no machine
            # reading a NON-zero station has been found to confirm it (PROBE NEEDED).
            for key, getter in (("max_tool_diameter", lambda st=st: st.maxToolDiameter),
                                ("max_tool_length", lambda st=st: st.maxToolLength)):
                value = measured(getter, factor)
                if value:
                    row[key] = value
            if row:
                row["units"] = unit
                stations.append(row)
    if stations:
        out["tool_stations"] = stations
    return out


# ── the op-asks-for vs machine-allows comparison the machine slice makes possible ─────────────────

_OP_SPINDLE_PARAM = "tool_spindleSpeed"


def op_spindle_speed(op):
    """The spindle speed ONE operation asks for, off its own tool_spindleSpeed CAM parameter (where
    its tool preset's speed lands), or None when that parameter is absent or does not read."""
    params = safe(lambda: op.parameters)
    if params is None:
        return None
    p = safe(lambda: params.itemByName(_OP_SPINDLE_PARAM))
    if p is None:
        return None
    return measured(lambda: p.value.value)


def spindle_check(op, machine_max):
    """(over, requested, marker) for one operation against its machine's spindle maximum - over is
    True only ABOVE the maximum, and an unreadable side answers None with a marker naming it."""
    if machine_max is None:
        return None, None, "machine_max_unavailable"
    requested = op_spindle_speed(op)
    if requested is None:
        return None, None, "op_spindle_speed_unreadable"
    return requested > machine_max, requested, None


def _walk_machine_locations(lib, vendor, model, visit):
    """Run the (vendor, model) query in each non-network location in turn, handing
    `visit(location_label, matches)` its matches; `visit` returns True to stop the walk."""
    for loc_name in _LIBRARY_LOCATIONS:
        loc = getattr(adsk.cam.LibraryLocations, loc_name, None)
        if loc is None:
            continue
        try:
            matches = lib.createQuery(loc, vendor or "", model or "").execute() or []
        except Exception:
            continue
        if visit(loc_name.replace("LibraryLocation", "").lower(), matches):
            return


def query_machines(lib, vendor, model):
    """Run the machine-library query for (vendor, model) across the Local + bundled Fusion360
    locations, deduped by label. Returns a list of (machine, label, vendor, model); the FIRST
    location that yields any match wins (Local before Fusion360)."""
    found, labels = [], set()

    def visit(_loc_label, matches):
        for m in matches:
            label, v, mo = machine_ident(m)
            if label in labels:      # dedupe identical machines that appear in more than one location
                continue
            labels.add(label)
            found.append((m, label, v, mo))
        return bool(found)           # prefer the first location that yields any match

    _walk_machine_locations(lib, vendor, model, visit)
    return found


def machine_catalog(vendor: str = "", machine_type: str = "", max_results: int = 100):
    """(rows, truncated, error) - the machine CATALOG the 'machine' input resolves from: the Local
    and Fusion360 locations, filtered by vendor and/or machine_type."""
    mt = (machine_type or "").strip().lower()
    if mt and mt not in _MACHINE_KINDS:
        return None, False, (f"Unknown machine_type '{machine_type}'. Valid: "
                             f"{', '.join(sorted(_MACHINE_KINDS))}.")
    try:
        lib = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    except Exception as e:
        return None, False, f"Could not access the machine library: {e}"
    rows, total = [], [0]

    def visit(loc_label, matches):
        for m in matches:
            kinds = machine_kinds(m)
            if mt and mt not in kinds:
                continue
            total[0] += 1
            if len(rows) >= max_results:
                continue
            label, v, mo = machine_ident(m)
            rows.append({"name": label, "vendor": v, "model": mo, "location": loc_label,
                         "kind": kinds,
                         "simulation_ready": bool(safe(lambda m=m: m.hasSimulationModel, False))})
        return False                  # every location is listed, so the walk never stops early

    _walk_machine_locations(lib, vendor, "", visit)
    mark_shared_names(rows, "name_in_both_locations")
    # Two DISTINCT machines can read one description, which is the row's 'name'. Keyed on
    # vendor|model, so ONE machine listed in both libraries stays out of it - the location flag
    # above is that row's answer, and vendor|model would not tell those two apart.
    mark_shared_names(rows, "description_shared", by="identity")
    return rows, total[0] > len(rows), None


# How mark_shared_names counts a name's carriers: distinct LOCATION (one machine in two libraries),
# any second ROW (the print settings), or a second vendor|model IDENTITY (two different machines).
_SHARED_NAME_KEYS = {
    "location": lambda i, r: r["location"],
    "row": lambda i, r: i,
    "identity": lambda i, r: ((r.get("vendor") or "").lower(), (r.get("model") or "").lower()),
}


def mark_shared_names(rows, flag, by="location"):
    """Flag every listed row under `flag` whose NAME another listed row carries, counted by the
    _SHARED_NAME_KEYS discriminator `by` names. Read over the LISTED rows, so a twin past a cap is
    unmarked."""
    key = _SHARED_NAME_KEYS[by]
    seen = {}
    for i, r in enumerate(rows):
        seen.setdefault((r["name"] or "").lower(), set()).add(key(i, r))
    for r in rows:
        if len(seen[(r["name"] or "").lower()]) > 1:
            r[flag] = True                       # absent = one listed row carries this name


# WALL-CLOCK budget for the by-description walk, which enumerates both locations UNFILTERED. It is
# checked BETWEEN machines only - an in-flight query cannot be interrupted - and a trip is its own
# refusal, since a catalog not read to the end proves nothing about what it holds.
_MACHINE_WALK_BUDGET_S = 5.0


def _machines_labelled(lib, targets):
    """(machines, timed_out) - the machines whose LABEL is one of `targets`, each a (lower-cased
    label, required lower-cased vendor or None) pair. The library query keys on the MODEL field, so
    a name carried only by a description is reachable only by reading the catalog."""
    found, answered = [], set()
    deadline = time.monotonic() + _MACHINE_WALK_BUDGET_S
    timed_out = [False]

    def visit(_loc_label, matches):
        here = set()
        for m in matches:
            if time.monotonic() > deadline:
                timed_out[0] = True
                return True
            label, v, mo = machine_ident(m)
            key = (label or "").lower()
            hit = [t for t, req_vendor in targets
                   if t == key and (req_vendor is None or req_vendor == (v or "").lower())]
            if not hit or key in answered:
                continue
            here.update(hit)
            found.append((m, label, v, mo))
        answered.update(here)
        return answered == {t for t, _req in targets}

    _walk_machine_locations(lib, "", "", visit)
    return found, timed_out[0]


def _split_ident_line(request):
    """(description, vendor, model) when `request` is a listing LINE handed back as written -
    'description [vendor|model]' - else None. Machines share descriptions and share vendor|model
    pairs, so only the triple addresses one."""
    if not request.endswith("]"):
        return None
    opened = request.rfind(" [")
    if opened <= 0:
        return None
    vendor, sep, model = request[opened + 2:-1].partition("|")
    if not sep:
        return None
    return request[:opened].strip(), vendor.strip(), model.strip()


def _machines_identified(lib, label, vendor, model):
    """The machines whose whole (description, vendor, model) identity is exactly this one, over the
    (vendor, model) query - lower-cased arguments, undeduped so a tie reaches the caller."""
    found = []

    def visit(_loc_label, matches):
        for m in matches:
            lab, v, mo = machine_ident(m)
            if ((lab or "").lower() == label and (v or "").lower() == vendor
                    and (mo or "").lower() == model):
                found.append((m, lab, v, mo))
        return bool(found)           # Local before Fusion360, as every machine read prefers

    _walk_machine_locations(lib, vendor, model, visit)
    return found


def _exact_machine(cands, machine, vendor, model):
    """The single candidate at the first priority yielding exactly one hit - full LABEL, then
    'vendor model', then model - else None. Same-model variants differ only by description, so the
    label is tried first."""
    ml = (model or "").strip().lower()
    ven = (vendor or "").strip().lower()
    full = (machine or "").strip().lower()

    def _unique(pred):
        # Deduped on the whole IDENTITY: two machines can carry one description.
        hits, seen = [], set()
        for tup in cands:
            _m, label, v, mo = tup
            if pred(label, v, mo):
                key = ((label or "").lower(), (v or "").lower(), (mo or "").lower())
                if key not in seen:
                    seen.add(key)
                    hits.append(tup)
        return hits[0] if len(hits) == 1 else None

    return (_unique(lambda label, v, mo: (label or "").lower() == full)                       # label
            or _unique(lambda label, v, mo: ((v or "") + " " + (mo or "")).strip().lower() == full)  # vendor model
            or _unique(lambda label, v, mo: bool(ml) and (mo or "").lower() == ml             # model (+vendor)
                       and (not ven or (v or "").lower() == ven)))


# Headroom reserved for named_with_remainder's own "... (+N more not listed)" suffix, whose exact
# length depends on the remainder count and is not known before the cap below is chosen.
_REMAINDER_MARGIN = 30
_MISS_BUDGET = 400          # test_prose_budget.NOTE_BUDGET_CHARS


def _length_capped(pairs, lead, tail):
    """How many leading `pairs` named_with_remainder can list before `lead` + the list + `tail`
    crosses the wire budget - a listing whose item WIDTH varies too much for a fixed count (a
    shipped vendor's model names are not all short)."""
    available = _MISS_BUDGET - len(lead) - len(tail) - _REMAINDER_MARGIN
    used = 0
    n = 0
    for p in pairs:
        add = len(p) + (2 if n else 0)
        if used + add > available:
            break
        used += add
        n += 1
    return max(n, 1)


def resolve_machine(machine):
    """(machine, label, None) for a 'machine' string - vendor|model, vendor/model, a bare model, a
    full description, or a listing line handed back - else (None, None, error): nothing matched, or
    the match was ambiguous and this resolver does not guess."""
    machine = (machine or "").strip()
    # The separator split below also runs over a listing LINE and reads half the description as a
    # vendor, so a miss on a parsed line reports what it PARSED.
    ident = _split_ident_line(machine)
    sep = "|" if "|" in machine else ("/" if "/" in machine else "")
    if sep:
        vendor, model = (p.strip() for p in machine.split(sep, 1))
    else:
        vendor, model = "", machine
    try:
        lib = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    except Exception as e:
        return None, None, f"Could not access the machine library: {e}"

    # FIRST: the refusal's own line, handed back - a description and a vendor|model are each
    # shared, so the whole identity is what resolves.
    if ident:
        by_ident = _machines_identified(lib, ident[0].lower(), ident[1].lower(), ident[2].lower())
        if len(by_ident) == 1:
            return by_ident[0][0], by_ident[0][1], None
        if len(by_ident) > 1:
            return None, None, (f"{len(by_ident)} machines carry that exact description, vendor and "
                                f"model ('{machine}'). Nothing read tells them apart, so this "
                                "resolver will not pick one.")

    cands = query_machines(lib, vendor, model)
    # WIDEN when the model as given matches nothing: the query prefix-matches the MODEL field, but a
    # variant's distinguishing text lives in its DESCRIPTION, which is the label callers pass. The
    # broad token fetches the candidate pool that is LABEL-matched below.
    if not cands:
        v2, broad = vendor, model
        if vendor and model.lower().startswith(vendor.lower() + " "):
            broad = model[len(vendor):].strip()               # 'Haas|Haas VF-2' -> model 'VF-2'
        elif not vendor and " " in machine:
            v2, broad = machine.split(" ", 1)                 # bare 'Haas VF-2...' -> vendor 'Haas'
        broad = broad.split(" ", 1)[0].strip() if broad else broad   # first model token ('VF-2')
        v2 = v2.strip()
        if (v2, broad) != (vendor, model) and (v2 or broad):
            widened = query_machines(lib, v2, broad)
            if widened:
                cands, vendor, model = widened, v2, broad
    # LAST: match the DESCRIPTION over one catalog walk, since the query keys on the MODEL field.
    # Two targets: the whole request against the label, and the half after a 'vendor|description'
    # separator against the label of a machine whose vendor is the other half.
    timed_out = False
    if not cands:
        targets = [(machine.lower(), None)] if machine else []
        if sep and model:
            targets.append((model.lower(), vendor.lower()))
        if targets:
            cands, timed_out = _machines_labelled(lib, targets)

    if timed_out:
        # A catalog read that stopped early says nothing about what the catalog holds, so this is
        # never worded as a miss: cam_create_machine reads a miss as proof that a name is free.
        return None, None, (f"Timed out after {_MACHINE_WALK_BUDGET_S:g}s reading the machine catalog "
                            f"for '{machine}', which the Local and Fusion360 queries did not match. "
                            "Whether a machine carries that name is UNKNOWN - retry, or pass "
                            "'vendor|model', which is queried without the catalog read.")
    if not cands:
        if ident:
            return None, None, (f"No machine matches description '{ident[0]}' with vendor|model "
                                f"'{ident[1]}|{ident[2]}' in the Local or Fusion360 machine "
                                "libraries. Pass a line as the refusal that listed it prints it.")
        read = "cam_get(include=['machines'], vendor=...) lists a vendor's machines."
        # An agent has no UI to browse: re-check the same catalog cam_get reads, so a request the
        # catalog answers offers what it holds instead of a bare miss.
        vendor_rows, _truncated, _cerr = machine_catalog(machine, "", 100)
        if not _cerr and vendor_rows:
            pairs = [f"{r['vendor']}|{r['model']}" for r in vendor_rows]
            # "names a VENDOR" is claimed only where every returned row's own vendor field backs
            # it case-insensitively - the query's match semantics on that field are not otherwise
            # established here, and a prefix match would make this claim false on the caller's string.
            is_vendor = all((r.get("vendor") or "").strip().lower() == machine.lower()
                            for r in vendor_rows)
            lead = (f"'{machine}' names a vendor carrying {len(pairs)} machine(s): " if is_vendor
                    else f"'{machine}' matches {len(pairs)} machine(s): ")
            tail = f" - pass one. {read}"
            cap = _length_capped(pairs, lead, tail)
            return None, None, lead + named_with_remainder(pairs, cap=cap) + tail
        return None, None, (f"No machine matches '{machine}' (vendor='{vendor}', model='{model}') "
                            f"in the Local or Fusion360 machine libraries. {read}")
    # EXACT match wins BEFORE refusing ambiguity (the house rule).
    exact = _exact_machine(cands, machine, vendor, model)
    if exact is not None:
        return exact[0], exact[1], None
    if len(cands) > 1:
        # Print the whole IDENTITY per row: descriptions and vendor|model pairs are each shared, so
        # either half alone lists a name that refuses again when passed back.
        names, seen = [], set()
        for (_m, lab, v, mo) in cands:
            if not lab:
                continue
            line = f"{lab} [{v}|{mo}]" if (v and mo) else lab
            if line.lower() not in seen:
                seen.add(line.lower())
                names.append(line)
        return None, None, (f"Ambiguous machine '{machine}' - {len(cands)} matches: "
                            f"{', '.join(names[:8])}. Pass the full line as shown.")
    return cands[0][0], cands[0][1], None


# ── the print setting catalog: what an ADDITIVE setup prints with ────────────────────────────────
# PrintSettingLibrary hangs off the same libraryManager as the machine library. Measured on the
# shipped library: several settings answer to ONE .id, so the NAME is what addresses a single one.


def print_setting_library():
    """The shared PrintSettingLibrary - CAMManager.get().libraryManager.printSettingLibrary, no open
    CAM job needed. Returns (library, None) or (None, error)."""
    lib = safe(lambda: adsk.cam.CAMManager.get().libraryManager.printSettingLibrary)
    if lib is None:
        return None, ("Could not access the print setting library "
                      "(CAMManager.libraryManager.printSettingLibrary).")
    return lib, None


def _print_settings_at(lib, loc_name, name="", vendor="", material=""):
    """Every print setting one non-network location answers, narrowed by the query's own
    name/vendor/material filters where each is given - [] when the location or the query does not
    answer. No 'machine' facet: measured live, 'M 290'/'EOS M 290'/'M291' each read 0 rows while
    vendor and material narrow non-empty, and nothing backs a legal spelling for it."""
    loc = getattr(adsk.cam.LibraryLocations, loc_name, None)
    if loc is None:
        return []
    try:
        query = lib.createQuery(loc)
        if name:
            query.name = name
        if vendor:
            query.vendor = vendor
        if material:
            query.material = material
        return list(query.execute() or [])
    except Exception:
        return []


def print_setting_ident(s):
    """(name, technology, id, description) for ONE PrintSetting - the row a catalog listing and a
    refusal share. The DESCRIPTION is in it because two shipped settings are identical on every
    other member; adsk.cam.PrintSetting carries no vendor or material member at all."""
    return (safe(lambda: s.name) or "", safe(lambda: s.technology) or "",
            safe(lambda: s.id) or "", safe(lambda: s.description) or "")


def print_setting_catalog(technology: str = "", max_results: int = 100, vendor: str = "",
                          material: str = ""):
    """(rows, truncated, error) - the print settings the 'print_setting' input resolves from, over
    the Local and Fusion360 locations, each row {name, technology, id, location}. vendor/material
    narrow the QUERY before execute(); technology narrows the RESULT in Python. The DESCRIPTION
    rides only on a row marked name_shared, the rows it tells apart."""
    lib, lerr = print_setting_library()
    if lerr:
        return None, False, lerr
    want_tech = (technology or "").strip().lower()
    rows, total = [], 0
    for loc_name in _LIBRARY_LOCATIONS:
        label = loc_name.replace("LibraryLocation", "").lower()
        for s in _print_settings_at(lib, loc_name, vendor=vendor, material=material):
            nm, tech, sid, desc = print_setting_ident(s)
            if want_tech and str(tech).lower() != want_tech:
                continue
            total += 1
            if len(rows) >= max_results:
                continue
            rows.append({"name": nm, "technology": tech, "id": sid, "location": label,
                         "description": desc})
    mark_shared_names(rows, "name_shared", by="row")
    # The description is read for EVERY row, because marking needs the whole listing, and then
    # dropped from the rows it discriminates nothing on - measured, one shipped pair shares a name
    # and the other 409 descriptions would be 45 KB of listing no caller can act on.
    for r in rows:
        if not r.get("name_shared"):
            r.pop("description", None)
    return rows, total > len(rows), None


def print_setting_technologies():
    """Every DISTINCT technology the two libraries' settings read back, sorted - what a 'technology'
    that matched nothing is answered with, since the vocabulary is the platform's and no list here
    can be checked against it."""
    lib, lerr = print_setting_library()
    if lerr:
        return []
    seen = set()
    for loc_name in _LIBRARY_LOCATIONS:
        for s in _print_settings_at(lib, loc_name):
            tech = print_setting_ident(s)[1]
            if tech:
                seen.add(str(tech))
    return sorted(seen)


# MEASURED: two shipped 'Formlabs SLS' settings share name, technology, id AND location, and only
# the DESCRIPTION separates them; a Local copy of a shipped setting matches on the other four. Both
# members are in the key or one of those pairs collapses to a silently-picked single hit.
def _setting_key(nm, tech, sid, loc_name, desc):
    return (nm.lower(), tech, sid, loc_name, desc)


def _exact_print_settings(lib, want, filtered):
    """Every DISTINCT setting whose stored name equals `want` case-insensitively, over the query's
    own name filter when `filtered`, else over the whole listing."""
    hits, keys = [], set()
    for loc_name in _LIBRARY_LOCATIONS:
        for s in _print_settings_at(lib, loc_name, want if filtered else ""):
            nm, tech, sid, desc = print_setting_ident(s)
            key = _setting_key(nm, tech, sid, loc_name, desc)
            if nm.lower() != want.lower() or key in keys:
                continue
            keys.add(key)
            hits.append((s, nm, tech, loc_name.replace("LibraryLocation", "").lower(), desc))
    return hits


# How much of a description a refusal prints per hit - enough to carry the discriminating words,
# which sit mid-sentence ('Fuse 1+ 30 W machine' against 'Fuse 1 machines'), and short enough that
# the shipped pair's refusal composes inside the wire budget.
_SETTING_DESC_CHARS = 60


def _setting_rows(hits, described=True):
    """One '<name> [<tech>] in the <loc> library' line per hit, the description appended only where
    it DISCRIMINATES - repeating one identical description across every row separates nothing and
    is the bulk of the message."""
    rows = [f"{nm} [{tech}] in the {loc} library" for _s, nm, tech, loc, _d in hits]
    if not described:
        return rows
    return [f"{row}, described '{(desc or '(no description)')[:_SETTING_DESC_CHARS]}'"
            for row, (_s, _n, _t, _l, desc) in zip(rows, hits)]


def _ambiguous_setting(want, hits):
    """The refusal for a name several settings answer to - the clause naming what separates them and
    the remedy that reaches one, keyed on what actually DIFFERS between the hits."""
    if len({desc for _s, _n, _t, _l, desc in hits}) > 1:
        return (f"'{want}' names {len(hits)} print settings; only their description tells them "
                f"apart: {named_with_remainder(_setting_rows(hits))}. Pass "
                "'print_setting_description' with a substring of one.")
    listed = named_with_remainder(_setting_rows(hits, described=False))
    # The LIBRARIES must differ for this clause, not merely include a local one: two LOCAL rows of
    # one name and one description sit in a single library, and there is no shipped one to reach.
    if len({loc for _s, _n, _t, loc, _d in hits}) > 1:
        return (f"'{want}' names {len(hits)} print settings reading the SAME description, so only "
                f"the library they sit in tells them apart: {listed}. Delete the local copy to "
                "reach the shipped one by name, or rename it.")
    return (f"'{want}' names {len(hits)} print settings alike on name, technology, description and "
            f"library: {listed}. They differ only on members no input here addresses, so "
            "'print_setting_description' cannot separate them either. "
            "cam_get(include=['print_settings']) shows what each one carries.")


def _matching_description(hits, want_desc):
    """(the hits whose description CONTAINS `want_desc`, case-insensitively) - the qualifier that
    picks one of a shared name's settings."""
    needle = want_desc.strip().lower()
    return [h for h in hits if needle in (h[4] or "").lower()]


def resolve_print_setting(name, description=""):
    """(PrintSetting, its own name, None) for the ONE setting whose stored name equals `name`
    case-insensitively, else (None, None, refusal). `description` is a SUBSTRING qualifier that must
    match exactly one of that name's settings - the only route to a shipped twin."""
    want = (name or "").strip()
    if not want:
        return None, None, "Provide 'print_setting' - a print setting name."
    lib, lerr = print_setting_library()
    if lerr:
        return None, None, lerr
    hits = _exact_print_settings(lib, want, True) or _exact_print_settings(lib, want, False)
    if not hits:
        return None, None, (f"No print setting named '{want}' in the Local or Fusion360 print "
                            "setting libraries. cam_get(include=['print_settings']) lists them; "
                            "'technology' narrows that listing.")
    qualifier = (description or "").strip()
    if qualifier:
        # Applied even where the NAME already resolves: a qualifier that matches nothing means the
        # caller is addressing a setting this one is not, and picking anyway is the silent wrong pick
        # the whole qualifier exists to prevent.
        narrowed = _matching_description(hits, qualifier)
        if len(narrowed) != 1:
            which = "none" if not narrowed else str(len(narrowed))
            return None, None, (
                f"'print_setting_description' {qualifier!r} matches {which} of the {len(hits)} "
                f"print setting(s) named '{want}': "
                f"{named_with_remainder(_setting_rows(hits))}. "
                "Pass a substring of exactly one.")
        hits = narrowed
    if len(hits) > 1:
        return None, None, _ambiguous_setting(want, hits)
    return hits[0][0], hits[0][1], None


# ── the SURFACE GROUPS on an operation - the reader its writer and the compare share ─────────────

# The read an operation's own parameter list is pulled with - what a refusal names a parameter to.
PARAM_READ = "cam_get(include=['parameters'], operation=<name>)"

# A CadMachineAvoidGroupsParameterValue holds one group per set of faces the toolpath treats alike.
AVOID_GROUPS_PARAM = "checkSurfaceSelectionSets"

# How the toolpath treats a group's faces. adsk.cam.MachiningMode spells its members
# '<Key>_MachiningMode'; the mode a FRESH group reads varies by strategy (corner and three_plus_two
# answered Machine, blend answered Gouge), so it is read back and never assumed.
MACHINE_MODE_MEMBERS = {"avoid": "Avoid_MachiningMode", "machine": "Machine_MachiningMode",
                        "gouge": "Gouge_MachiningMode", "fixture": "Fixture_MachiningMode",
                        "none": "None_MachiningMode"}


def machine_mode_value(key):
    """The MachiningMode member one friendly key names, or None where this build carries neither
    the family nor that member (getattr on the enum, never a hand-coded int)."""
    family = getattr(adsk.cam, "MachiningMode", None)
    return getattr(family, MACHINE_MODE_MEMBERS[key], None) if family is not None else None


def machine_mode_key(value):
    """The friendly key one MachiningMode value spells, or the value itself where no member of this
    build answers it - never a fabricated name."""
    if value is None:
        return None
    return next((k for k in MACHINE_MODE_MEMBERS if machine_mode_value(k) == value), value)


def group_record(group, units="mm"):
    """{entities, machine_over_holes, machine_mode, radial_offset, axial_offset, combined_offset}
    off ONE surface group in 'units' - the three offsets are stored in mm regardless of display
    units, converted back on read. None where the group did not read back."""
    if group is None:
        return None
    mm_per_unit = (scale(units) or 0.1) * 10

    def _off(prop):
        raw = safe(lambda: getattr(group, prop))
        return None if raw is None else round(raw / mm_per_unit, 6)

    return {"entities": safe(lambda: len(list(group.value))),
            "machine_over_holes": read_flag(lambda: group.machineOverHoles),
            "machine_mode": machine_mode_key(safe(lambda: group.machineMode)),
            "radial_offset": _off("radialOffset"),
            "axial_offset": _off("axialOffset"),
            "combined_offset": _off("combinedOffset")}


def avoid_groups(op, writable=True):
    """(parameter value, groups collection, None) for the operation's surface groups, or
    (None, None, error) - an operation that carries no such parameter, or whose own copy does not
    read isEditable true, is refused rather than handed a group nothing would read. A READ passes
    writable=False: a set that will not take a write still answers what it holds."""
    name = safe(lambda: op.name)
    p = safe(lambda: op.parameters.itemByName(AVOID_GROUPS_PARAM))
    if p is None:
        return None, None, (f"Operation '{name}' has no '{AVOID_GROUPS_PARAM}' parameter, so it "
                            f"takes no surface groups. {PARAM_READ} lists what it does carry.")
    if writable and safe(lambda: p.isEditable) is not True:
        return None, None, (f"Operation '{name}' carries '{AVOID_GROUPS_PARAM}' but it did not "
                            "read isEditable true, so no surface group is offered on it. Group "
                            "these faces in the Fusion UI instead.")
    pv = safe(lambda: p.value)
    groups = safe(lambda: pv.getMachineAvoidGroups()) if pv is not None else None
    if groups is None:
        return None, None, (f"Operation '{name}' would not hand back its surface groups "
                            "(getMachineAvoidGroups did not read), so none was added.")
    return pv, groups, None


# ── CAM LIBRARY folder walks - the ONE traversal the tool / post / template libraries share ──────
# All three expose one shape off a LibraryLocations root url: childFolderURLs nests, and the leaves
# read per kind (childAssetURLs, childTemplates). An unbounded cloud-tree walk hangs the add-in.

_LIBRARY_MAX_DEPTH = 6
_LIBRARY_MAX_FOLDERS = 1500


def library_children(lib, url, kind):
    """One library folder's children of `kind` ('childFolderURLs' / 'childAssetURLs' /
    'childTemplates') as a list - [] when the accessor is absent on this library or the call raises.
    The raise-tolerant read every library walk and leaf op goes through."""
    return list(safe(lambda: list(getattr(lib, kind)(url)), []) or [])


def walk_library_folders(lib, root, visit, max_depth=_LIBRARY_MAX_DEPTH,
                         max_folders=_LIBRARY_MAX_FOLDERS):
    """Depth-first walk of a CAM library's FOLDER tree from `root`, calling `visit(folder_url)` once
    per folder INCLUDING the root; `visit` returns True to stop the walk. Bounded by `max_depth`
    and `max_folders`, and returns True when a cap stopped it early - the read is INCOMPLETE."""
    truncated = [False]
    visited = [0]

    def walk(url, depth):
        if url is None:
            return False
        if depth > max_depth:
            truncated[0] = True
            return False                      # this branch is too deep; siblings may still fit
        if visited[0] >= max_folders:
            truncated[0] = True
            return True                       # the folder budget is spent everywhere, not just here
        visited[0] += 1
        if visit(url):
            truncated[0] = True
            return True
        for f in library_children(lib, url, "childFolderURLs"):
            if walk(f, depth + 1):
                return True
        return False

    walk(root, 0)
    return truncated[0]


def library_assets(lib, root, max_depth=_LIBRARY_MAX_DEPTH, max_folders=_LIBRARY_MAX_FOLDERS,
                   max_assets=None):
    """(assets, truncated) - every child ASSET url under a library location root, folders recursed.
    An asset url is the addressable identity: .leafName is its name, .toString() its url.
    `max_assets` caps the collection; truncated is True when any cap stopped the walk."""
    assets = []

    def visit(url):
        for a in library_children(lib, url, "childAssetURLs"):
            if max_assets is not None and len(assets) >= max_assets:
                return True                   # full - stop the walk, report truncated
            assets.append(a)
        return False

    truncated = walk_library_folders(lib, root, visit, max_depth=max_depth,
                                     max_folders=max_folders)
    return assets, truncated


# ── addressing ONE asset in a library by name - the substrate every library DELETE resolves on ────


def asset_key(url):
    """One asset url's identity for de-duplication - its string form, or the object itself when the
    url does not stringify (two urls for one asset must not read as two candidates)."""
    return safe(lambda: url.toString()) or url


def asset_leaf(url):
    """One asset url's leafName as stored, stripped - '' when it does not read."""
    return (safe(lambda: url.leafName) or "").strip()


def asset_leaf_keys(url):
    """The names ONE asset answers to, lowercased: its leafName as stored, and its STEM - the part
    before the LAST dot, since a stored leafName carries a file extension the asset's own name
    does not."""
    leaf = asset_leaf(url).lower()
    keys = {leaf}
    stem = leaf.rpartition(".")[0]
    if stem:
        keys.add(stem)
    return keys


def unique_by_url(items, seen=None):
    """`items` with each asset URL ONCE - the ONE identity decision every library read de-dupes on,
    since a library lists one asset under two folder paths. `seen` carries that identity ACROSS
    calls (a folder-by-folder walk); an item is a url object or the string a listing stringified."""
    seen = set() if seen is None else seen
    out = []
    for item in items:
        key = str(asset_key(item))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def assets_named(assets, wanted):
    """The assets one of whose names - leafName as stored, or its stem - EXACTLY matches
    (case-insensitively) one of `wanted`, deduped by url."""
    return unique_by_url([a for a in assets if asset_leaf_keys(a) & wanted])
