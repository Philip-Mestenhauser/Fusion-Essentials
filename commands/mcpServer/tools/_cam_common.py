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

from ._common import (measured, named_with_remainder, iter_collection, read_flag, safe)
from ._write_guard import (_active_identity, document_key,   # the one active-document identity read,
                           on_key_renamed)                   # the one key a cross-call store
                                                             # remembers a document by, and the
                                                             # announcement when that key changes

# The "what to reuse from here" catalog line for the generated CLAUDE.md helper map (see
# tests/gen_manifest.py): each symbol with the one clause that says WHEN to reach for it. The
# mechanism behind a clause lives at the symbol itself, in its test, or in VERIFIED_API_FACTS.md.
MAP_BLURB = (
    "get_cam - the active document's CAM product, the read every CAM tool opens with; "
    "walk_cam_tree + resolve_cam_node - the ONE CAM tree walk and the by-name resolver every CAM "
    "tool targets a setup/operation/folder/pattern through: case-insensitive EXACT, a miss lists "
    "the available names, a DUPLICATED name is REFUSED naming each hit's '<name>#<n>' address to "
    "retry with (kinds= / setup= scope it); tree_nodes + operation_nodes + operation_nodes_under - "
    "the setup-scoped, operation-only and under-one-node slices of that walk, for a caller that "
    "NAMES its results on the wire: a node carries the 'Setup / ... / op' breadcrumb two "
    "same-named operations are told apart by; operations_under + walk_operations - the bare-Operation "
    "projections of those slices, where no breadcrumb is published; owning_setup - the Setup a node "
    "sits under, for a caller holding the node; find_setup + find_operation + resolve_operation - "
    "the by-name wrappers that hand back an available-name list too (find_setup and "
    "resolve_operation add the resolver's refusal verbatim), for a caller wording a narrower remedy "
    "of its own; expression_error - whether a just-set CAM parameter "
    "EVALUATED, the read-back every CAM param editor gates on; parse_parameters - the ONE "
    "{name: expression} / 'name=value, ...' request parser both param editors validate through; "
    "op_state_facts + op_primary_state + op_state_tally + counts_as_warning + is_empty_toolpath - "
    "the per-op lifecycle read and the classifiers over it, for any surface tallying op state: one "
    "mutually-exclusive bucket per op, the warning OVERLAY on top of that bucket, and the "
    "generated-but-cut-nothing test; validity_basis - the Manufacture-workspace trust gate every "
    "op-state rollup reads, since op validity is only trustworthy there; setup_blockers + "
    "blocked_setup_records - a setup's OWN post prerequisites and their [{name, blocked_by}] "
    "projection, the one read every 'is this postable' answer takes them from; ready_verdict + "
    "first_line - the ONE postable-verdict sentence every readiness surface emits (it demotes "
    "'ready to post' over a warned op and WITHHOLDS it over a blocked setup) and the one-line trim "
    "a disclosure SAMPLE takes; live_readiness - the whole active document's job-health signal, "
    "for a health read or a generation poller; clamp_rows - the ONE 'max_results' clamp a capped "
    "read holds its row count in; register_future - the ONE async-generation registration every "
    "launch path mints its handle through: it keeps the Future referenced and stamps the launch "
    "document, so a status read taken while another document is active still answers for that "
    "generation; machine_library + machine_location + machine_catalog + query_machines + "
    "resolve_machine + machine_label + machine_ident + machine_kinds - the library handle, the "
    "which-location read, the catalog the 'machine' input resolves from and the by-name resolver "
    "an assignment or a machine create runs through (exact LABEL match first, ambiguity REFUSED), "
    "plus the label / ident / kinds projections a catalog row is built from; kinematics_parts + "
    "machine_limits + machine_spindle_max - what the MACHINE allows: its spindle maximum and axis "
    "travels, off its kinematics tree; op_spindle_speed + spindle_check - what ONE operation asks "
    "for and the comparison against that maximum; walk_library_folders + library_assets + "
    "library_children - the ONE bounded CAM library folder-tree walk (tool, post and template "
    "libraries alike) and its collect-the-asset-urls projection, each caller passing its own leaf "
    "op; asset_leaf + asset_key + asset_leaf_keys + assets_named - the 'which asset answers to "
    "this name' matcher every library DELETE resolves its target on")

app = adsk.core.Application.get()


def expression_error(p):
    """Read a just-set CAM parameter BACK to confirm its expression EVALUATED - the CAM param store is
    NOT the CAD one. A CAMParameter exposes .error / .warning message strings; a broken expression
    ('NoSuchParamXyz * 2') is STORED verbatim (.expression echoes it) and its .value.value even reads
    back a finite 0.0, so ONLY .error reveals it - live it reads 'Failed to evaluate expression.'.
    A .warning ('stock less than the model width') fires on VALID expressions too, so it never gates.
    Returns (error_message_or_None, warning_message_or_None)."""
    err = (safe(lambda: p.error) or "").strip()
    warn = (safe(lambda: p.warning) or "").strip()
    # Platform quirk (live): a .warning string can arrive with its template tokens uninterpolated
    # ('${self.title}'). Tag it once here so every consumer's wire shows it as the cosmetic
    # artifact it is, not a broken parameter reference to chase.
    if "${" in warn:
        warn += " [the ${...} token is an uninterpolated platform template - cosmetic]"
    return (err or None), (warn or None)


def clamp_rows(max_results, default: int, ceiling: int) -> int:
    """The row cap a capped CAM read runs under - the ONE clamp every 'max_results' goes through.
    `max_results` arrives off the wire, so a value that is not a number falls back to `default`
    rather than raising, and the result is held inside 1..`ceiling`: a caller cannot lift a cap that
    exists because every row crosses the wire. Each read keeps its OWN default/ceiling pair."""
    try:
        n = int(max_results or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, ceiling))


def parse_parameters(parameters):
    """Normalize a 'parameters' request into a dict {name: expression}. Accepts a dict or a
    'name=value, name=value' string. Returns (dict, error). The ONE parser both CAM parameter
    editors (operation and setup) validate their request through, so the two wire forms cannot be
    accepted by one and refused by the other."""
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


def get_cam():
    """The active document's CAM product, or (None, reason). Works in ANY workspace (CAM data is
    readable without entering Manufacture; op VALIDITY is only trustworthy there - that caveat lives
    on the reads). One resolver shared by every CAM tool."""
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
    return cam, None


def tool_holder(t):
    """A CAM Tool's assigned HOLDER identity, or None when it carries no holder. adsk.cam.Tool exposes
    NO holder accessor (verified via sys_get_api_doc), so the holder is read from the tool's JSON, where
    it lives as a 'holder' sub-doc ({description, product-id, vendor, segments}). Returns only the fields
    that are actually present ({name, product_id, vendor, segment_count}) - claim only what reads back.
    Shared by cam_get(include=['tool']) and the cam_edit_tools library listing so a holder reads back
    the same way everywhere."""
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
    return out or None


def setups(cam):
    """Every Setup in the document, as a list - the basis for the tree walk below."""
    return list(iter_collection(safe(lambda: cam.setups)))


def setup_names(cam):
    """Every setup's name, for a 'not found, available: ...' message - built one way everywhere."""
    return [safe(lambda s=s: s.name) for s in setups(cam)]


# One node of the CAM tree. kind is STRUCTURAL - which collection yielded the node ('setup' /
# 'operation' / 'folder' / 'pattern') - so no type-name sniffing is needed. path is the
# 'Setup / Folder / Op' breadcrumb an ambiguity refusal names its candidates by, and `parent` is the
# node that CONTAINED this one: the structural answer to "which folder is this op in", which the
# joined path cannot give back (a setup or folder whose own name contains ' / ' splits wrong).
CamNode = collections.namedtuple("CamNode", ["obj", "kind", "name", "setup", "path", "parent"],
                                 defaults=(None,))


# The segment a breadcrumb carries for a level whose own name did NOT read. A None joined into an
# f-string prints the literal 'None', which reads as a complete address to a container actually
# NAMED that; this marker discloses the unread level instead.
#
# The decision is made on the READ answering None - never on a string match against the joined path -
# so a container whose real name is the string 'None' keeps its own segment untouched.
_UNREAD_SEGMENT = "(name unread)"


def _segment(name):
    """One breadcrumb segment for a level whose name read `name`: that name, or _UNREAD_SEGMENT when
    the read answered None (it raised, or the property itself answered None). Every level of every
    path the walk builds goes through this, so no surface can publish an address whose missing
    segment is invisible."""
    return _UNREAD_SEGMENT if name is None else name


def _walk_children(parent, setup_name, path, out, parent_node=None):
    """Collect CamNodes for everything nested under `parent` (a Setup/CAMFolder/CAMPattern).
    `.operations` lists only the DIRECT children, and setup.allOperations flattens folder children
    while DROPPING the folder/pattern containers (verified live) - so containers are reachable only
    by recursing `.folders`/`.patterns` explicitly, which nest. A parent exposing no `.operations`
    collection degrades to its allOperations flatten (operations only).

    Each level's own segment goes through _segment, so a name that did not read is DISCLOSED in the
    path instead of printing as the literal 'None'. `name` still carries the raw read (None when it
    did not answer), which is what a consumer deciding per LEVEL - workspace_orient._op_breadcrumb -
    climbs the parent links for."""
    ops = safe(lambda: parent.operations)
    if ops is not None:
        for o in iter_collection(ops):
            nm = safe(lambda o=o: o.name)
            out.append(CamNode(o, "operation", nm, setup_name,
                               f"{path} / {_segment(nm)}", parent_node))
        for kind, getter in (("folder", lambda: parent.folders),
                             ("pattern", lambda: parent.patterns)):
            for c in iter_collection(safe(getter)):
                nm = safe(lambda c=c: c.name)
                child_path = f"{path} / {_segment(nm)}"
                child_node = CamNode(c, kind, nm, setup_name, child_path, parent_node)
                out.append(child_node)
                _walk_children(c, setup_name, child_path, out, child_node)
        return
    for o in iter_collection(safe(lambda: parent.allOperations)):
        op = adsk.cam.Operation.cast(o)
        if op is not None:
            nm = safe(lambda op=op: op.name)
            out.append(CamNode(op, "operation", nm, setup_name,
                               f"{path} / {_segment(nm)}", parent_node))


def _setup_node(s):
    # The setup is the ROOT segment of every path under it, so it is disclosed the same way: a setup
    # whose name did not read leaves the marker rather than an empty leading segment, which would
    # make ' / Op' read as an operation with no container at all.
    nm = safe(lambda: s.name)
    return CamNode(s, "setup", nm, nm, _segment(nm), None)


def tree_nodes(setup_obj):
    """CamNodes for ONE setup subtree: the setup itself, then every operation/folder/pattern nested
    anywhere under it - the setup-scoped slice of walk_cam_tree."""
    node = _setup_node(setup_obj)
    nodes = [node]
    _walk_children(setup_obj, node.name, node.path, nodes, node)
    return nodes


def walk_cam_tree(cam):
    """Every node of the CAM tree as CamNode(obj, kind, name, setup, path, parent): each Setup plus
    all operations/folders/patterns nested anywhere under it. The ONE traversal every CAM tool walks
    and resolves names over."""
    nodes = []
    for s in setups(cam):
        nodes.extend(tree_nodes(s))
    return nodes


def operation_nodes(cam):
    """Every OPERATION node of the CAM tree - the ONE operation pool, walk_cam_tree filtered to
    kind == 'operation'. Every by-name operation resolve, every available-name listing and every
    duplicate breadcrumb is read off this one filter, so two of them cannot answer from different
    censuses of the same tree."""
    return [n for n in walk_cam_tree(cam) if n.kind == "operation"]


# The address that picks ONE of several nodes sharing a name: '<name>#<n>', n counting from 1 over
# the same-named nodes in the walk's own order - the order the ambiguity refusal lists them in. It
# exists because a SETUP's path is its own bare name (_setup_node), so two same-named setups are
# told apart by nothing the tree publishes, and the only other way out of the refusal would be
# renaming one, which needs the address the caller does not have.
_ORDINAL_SEP = "#"


def _ordinal_address(want):
    """('<base name>', <1-based ordinal>) when `want` is spelled '<name>#<n>', else (want, None).

    Split on the LAST separator, so a node whose own name carries one ('Op#3') is still addressable
    as 'Op#3#2' - splitting on the first would read the base as 'Op' and the ordinal as '3#2', which
    is not a number, and the address would resolve nothing. Same rule as _inputs._split_text_ref."""
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
    """One row of an ambiguity refusal: the '<name>#<n>' address that resolves THIS node, plus the
    fact that tells it from its namesakes. An operation/folder/pattern is told apart by its
    'Setup / item' path; a setup's path is its own bare name, so the number of operations under it
    is named instead."""
    addr = f"{node.name}{_ORDINAL_SEP}{ordinal}"
    if node.kind != "setup":
        return f"{addr} (at {node.path})"
    n = len(operations_under(node.obj))
    return f"{addr} ({n} operation{'' if n == 1 else 's'})"


def _free_sibling_addresses(pool, same):
    """The '<name>#<n>' addresses over `same` that no node in `pool` CARRIES as a literal name.

    A literal name wins over the ordinal address in this resolver, so an address some node is
    actually NAMED reaches that node instead of the sibling it counts to - it is no handle on the
    sibling, and is left out."""
    carried = {(n.name or "").lower() for n in pool}
    return [addr for addr in (f"{n.name}{_ORDINAL_SEP}{i}" for i, n in enumerate(same, 1))
            if addr.lower() not in carried]


def resolve_cam_node(cam, name, kinds=("operation",), setup=None, label=None, nodes=None):
    """The ONE by-name resolver over the CAM tree: case-insensitive EXACT match on nodes whose kind
    is in `kinds`, optionally scoped to one setup object (`setup`, in which case `cam` is unused).
    Returns (CamNode, None) for the unique hit. 0 hits -> (None, error listing the available names).
    2+ hits -> REFUSED: (None, error naming the count and each duplicate's '<name>#<n>' address,
    which is what the caller retries with) - operation names legitimately collide across setups, so
    a first (or last) match silently targets the wrong entity. `label` is the noun the error uses
    (defaults to the kinds joined with '/').

    `nodes` lets a caller that ALREADY walked the tree hand its own node list in (unscoped only, and
    `cam` then goes unused): the resolve and whatever else that caller builds off its pool then
    describe ONE census of the tree, and the walk runs once."""
    if setup is not None:
        nodes = tree_nodes(setup)
    elif nodes is not None:
        pass                                  # the caller's own walk, reused rather than repeated
    elif set(kinds) == {"setup"}:
        nodes = [_setup_node(s) for s in setups(cam)]
    else:
        nodes = walk_cam_tree(cam)
    label = label or "/".join(kinds)
    asked = (name or "").strip()
    want = asked.lower()
    pool = [n for n in nodes if n.kind in kinds]
    matches = [n for n in pool if (n.name or "").lower() == want]
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
        return None, (f"No {label} named '{name}'. Available: "
                      f"{named_with_remainder(available) or '(none)'}.")
    if len(matches) == 1 and addressed is not None and addressed is not matches[0]:
        # Both readings resolve, to DIFFERENT nodes: one node CARRIES this exact name while another
        # is item <n> of a same-named set. The ambiguity refusal below hands back '<name>#<n>'
        # addresses, so a design also holding a node named one of them can be asked a question with
        # two true answers - and answering with either is the first-match this resolver refuses
        # everywhere else. No spelling on this input separates them, so nothing is picked.
        #
        # The way out: the ordinal reading dies once fewer than `ordinal` nodes carry `base`, after
        # which the literal match below returns. That takes `need` renames, and each one leaves the
        # same-named set one item SHORTER. Performed HIGHEST-ordinal-first, every address still to
        # come is a number the shrunken set still holds - by construction, since each later address
        # is smaller than the one just used and the set lost exactly one item - so the list stays
        # performable to its end. Lowest-first does not: renaming the FIRST of three leaves two,
        # and the '#3' printed beside it then addresses nothing. What dissolves the reading is the
        # COUNT falling below `ordinal`, so renaming ANY `need` of the same-named ones does it, whichever
        # of them each address reaches - which is why that is all the sentence promises.
        #
        # PROBE NEEDED (CAM-33): whether the walk hands the survivors back in the same relative
        # order after a rename is unmeasured, so no address here is offered as reaching the same
        # item it named when the list was printed - only as reaching one of the items named `base`.
        #
        # Only addresses no node carries as a literal name are offered - the others reach the node
        # wearing that spelling, not the sibling - so a remedy ships only where `need` are free.
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
        return None, (f"'{name}' is ambiguous - {len(matches)} CAM items share that name: "
                      f"{named_with_remainder(rows)}. Retry with one of those '<name>#<n>' "
                      "addresses; the number counts the items of that name in the order listed "
                      "here.")
    return matches[0], None


def operation_nodes_under(node):
    """Every operation CamNode nested anywhere under one setup/folder/pattern NODE - the scoped
    counterpart of operation_nodes, walked from that node's OWN setup name and path so each row
    keeps the 'Setup / ... / op' breadcrumb.

    That breadcrumb is the only thing separating two operations of one name, and the object-only
    projection below drops it: a scoped listing built from bare Operations prints the shared name
    twice."""
    nodes = []
    _walk_children(node.obj, node.setup, node.path, nodes, node)
    return [n for n in nodes if n.kind == "operation"]


def operations_under(parent):
    """Every real Operation nested anywhere under one setup/folder/pattern - the scoped leaf
    projection of the shared walk (a 'show this folder' / 'poll this setup' collects ops through
    it instead of re-walking). Takes the OBJECT, so it is reachable with no node in hand; a caller
    holding the node and naming the results takes operation_nodes_under."""
    nodes = []
    _walk_children(parent, None, "", nodes)
    return [n.obj for n in nodes if n.kind == "operation"]


# The walk builds the parent chain, so it is acyclic by construction; the hop cap is only there so
# a hand-built node can never spin the climb below forever.
_MAX_PARENT_HOPS = 64


def owning_setup(node):
    """The Setup OBJECT a CamNode sits under - the node itself when it IS a setup, else the setup
    its parent chain ends at, or None when the chain does not reach one. Read from the walk's own
    parent links, never by re-resolving node.setup by NAME: setup names can collide, so a by-name
    round trip can refuse (or answer with a different setup) for a node already in hand."""
    seen = 0
    while node is not None and seen <= _MAX_PARENT_HOPS:
        if node.kind == "setup":
            return node.obj
        node = node.parent
        seen += 1
    return None


def find_setup(cam, name):
    """The unique Setup named `name` (case-INSENSITIVE exact) as (setup, available_names, error).

    On a miss `setup` is None and `error` is resolve_cam_node's ready-to-return refusal, where a
    plain absence and a DUPLICATED name read differently. Callers return that text verbatim: the
    resolver is the one place that knows WHICH of the two happened, so a caller wrapping it in its
    own 'not found' prefix would assert absence about a name that was found twice.
    `available_names` is the plain name list, for callers that offer the choices elsewhere in their
    payload."""
    node, err = resolve_cam_node(cam, name, kinds=("setup",), label="setup")
    return (node.obj if node else None), setup_names(cam), err


def walk_operations(cam):
    """Every real Operation across every setup, folder/pattern-nested INCLUDED - the object
    projection of operation_nodes, so 'which operations exist' is answered by the same traversal
    everywhere."""
    return [n.obj for n in operation_nodes(cam)]


def _operation_listing(pool, name):
    """The available list an operation miss carries, read off an ALREADY-WALKED operation pool:
    each duplicate's 'Setup / op' breadcrumb when several operations share `name`, every operation
    name otherwise. The ONE rule, so find_operation and resolve_operation cannot list differently."""
    want = (name or "").strip().lower()
    dupes = [n for n in pool if (n.name or "").lower() == want]
    return [n.path for n in dupes] if len(dupes) > 1 else [n.name for n in pool]


def find_operation(cam, name):
    """The unique Operation named `name` (case-INSENSITIVE exact) anywhere in the CAM tree, or
    (None, available_names). A DUPLICATED name is REFUSED: (None, each duplicate's 'Setup / op'
    path) so even a caller's plain not-found error surfaces the collision; a true miss returns
    every operation name. resolve_cam_node is the same resolver with the full refusal message."""
    pool = operation_nodes(cam)
    want = (name or "").strip().lower()
    matches = [n for n in pool if (n.name or "").lower() == want]
    return (matches[0].obj if len(matches) == 1 else None), _operation_listing(pool, name)


def resolve_operation(cam, name, label="operation"):
    """(node, error, available) - the unscoped operation resolve for a caller that ALSO needs
    find_operation's available list, to word a narrower remedy in its own input vocabulary.

    ONE walk answers both: the resolve and the list are read off the SAME operation_nodes pool, so
    a remedy built from the list cannot describe a different census than the refusal it accompanies,
    and an unscoped miss walks the tree once instead of twice."""
    pool = operation_nodes(cam)
    node, err = resolve_cam_node(cam, name, kinds=("operation",), label=label, nodes=pool)
    return node, err, _operation_listing(pool, name)


def first_line(text) -> str:
    """The first line of a fault message, '' when there is none - the trim every disclosure SAMPLE
    takes, so a multi-line warning/error body never rides inside a one-sentence verdict."""
    lines = (text or "").strip().splitlines()
    return lines[0] if lines else ""


def first_error_line(obj):
    """First line of an object's .error (the disclosure signal; the full text is the per-item record's
    job). '' if none."""
    return first_line(safe(lambda: obj.error))


def first_warning_line(obj):
    """First line of an object's .warning - the same disclosure signal for the softer fault. '' if none."""
    return first_line(safe(lambda: obj.warning))


def counts_as_warning(facts: dict) -> bool:
    """Whether an op's warning counts toward the readiness overlay. A warning on an ERRORED op says
    nothing beyond its error (which already blocks), and a SUPPRESSED op is excluded from the post,
    so neither demotes a verdict. The ONE predicate every readiness surface counts AND samples
    through, so the count and the named sample can never describe different sets.

    Read by key so BOTH shapes carrying these facts answer it: op_state_facts (the live per-op read
    op_state_tally walks) and cam_get's per-op RECORD, which carries has_warning/has_error/
    is_suppressed but no raw operation_state."""
    return (bool(facts.get("has_warning")) and not facts.get("has_error")
            and not (facts.get("is_suppressed") or facts.get("operation_state") == 2))


def op_state_facts(op) -> dict:
    """ONE safe read of the raw per-op lifecycle state Fusion exposes, so every op-state tally
    (cam_get's per-setup op_states via op_primary_state, cam_get_status's live_states via
    op_state_tally) classifies from the SAME facts instead of each re-reading hasError/operationState/
    isSuppressed/isGenerating/hasWarning independently. operationState carries an
    adsk.cam.OperationStates member - IsValid 0, IsInvalid 1, Suppressed 2, NoToolpath 3 (measured;
    the ints live in tests/live_api_facts.py) - and op_primary_state is what buckets it, together
    with the flags above, into the one state name a payload publishes ('out_of_date' is that
    vocabulary's name for IsInvalid, 'no_toolpath' for NoToolpath)."""
    return {
        "name": safe(lambda: op.name),
        "has_error": bool(safe(lambda: op.hasError, False)),
        "has_warning": bool(safe(lambda: op.hasWarning, False)),
        "is_suppressed": bool(safe(lambda: op.isSuppressed, False)),
        "is_generating": bool(safe(lambda: op.isGenerating, False)),
        "operation_state": safe(lambda: op.operationState),
        "generating_progress": safe(lambda: op.generatingProgress),
        # The toolpath pair is read through read_flag (True/False/None): an op that GENERATED and
        # produced no toolpath is told from one that never generated by these two flags together
        # (is_empty_toolpath), and a coerced False on an unreadable flag would invent that state.
        "has_toolpath": read_flag(lambda: op.hasToolpath),
        "is_toolpath_valid": read_flag(lambda: op.isToolpathValid),
    }


def is_empty_toolpath(facts: dict) -> bool:
    """True for the EMPTY class: an operation that generated and produced no toolpath - it cuts
    nothing, yet it is not out of date and it is not suppressed.

    Measured as its own state bucket on a 99-operation job: 21 ops read (state IsValid,
    isToolpathValid True, hasToolpath True), 13 read (state IsValid, isToolpathValid True,
    hasToolpath False) and 65 read suppressed - no overlap. So an empty op is machine-readable
    from the flags alone; nothing has to match warning text. Classifies from op_state_facts (which
    reads both toolpath flags honestly), so an UNREADABLE flag answers False here rather than
    inventing the state."""
    return (op_primary_state(facts) == "valid"
            and facts.get("is_toolpath_valid") is True
            and facts.get("has_toolpath") is False)


def op_state_tally(ops) -> dict:
    """The valid/out_of_date/errored/generating/suppressed tally that live_readiness (the WHOLE
    document) and a scoped-target poll (cam_generate's poller, one setup/operation/folder) both need -
    the ONE per-op walk both share, classifying every op from the same op_state_facts. An ERRORED op
    is its OWN bucket: it has a parameter/geometry fault and will NEVER finish generating, so counting
    it as out_of_date/generating would make a poller wait forever. 'generating' is an independent
    OVERLAY bit (an op can be valid/out_of_date AND generating), and so is 'warnings': a WARNED op
    stays in its lifecycle bucket (measured: an unselected-geometry 2D Contour reports hasWarning
    with hasError False and operationState 0 - it reads 'valid' and has no toolpath at all), so a
    verdict built from the buckets alone overstates a job carrying warnings.

    Returns {valid, out_of_date, errored, generating, suppressed, warnings, total, active,
    op_sample, warning_sample} - op_sample is the first errored op's {name, error} and
    warning_sample the first counted-warning op's {name, warning}, both None when there is none
    (each is sampled through the same predicate as its count). Each caller layers its OWN payload
    shape on top (live_readiness adds setup-/program-level errors + a readiness verdict; the scoped
    poller adds setups_errored=0/programs_errored=0). This is a DIFFERENT tally from cam_get's
    op_states (a per-SETUP, mutually-exclusive-bucket rollup via op_primary_state) - same raw facts,
    different shape for a different question ("what's live right now" vs "this setup's state mix)."""
    valid = ood = errored = generating = suppressed = warnings = total = 0
    active = None
    op_sample = None
    warning_sample = None
    for raw in (ops or []):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        facts = op_state_facts(op)
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
            valid += 1
        elif state == 2:
            suppressed += 1
        elif state in (1, 3):
            ood += 1
        if facts["is_generating"]:
            generating += 1
            prog = facts["generating_progress"]
            if active is None or (prog and prog not in ("Pending", "0.0%")):
                active = {"op": facts["name"], "progress": prog}
    return {"valid": valid, "out_of_date": ood, "errored": errored, "generating": generating,
            "suppressed": suppressed, "warnings": warnings, "total": total, "active": active,
            "op_sample": op_sample, "warning_sample": warning_sample}


def _warning_phrase(sample) -> str:
    """The 'name - first warning line' clause a readiness verdict names its first warning by. A
    sample that could not be read says so rather than naming a blank operation."""
    if not sample or not sample.get("name"):
        return "cam_get(include=['operations']) lists which."
    text = (sample.get("warning") or "").strip()
    return f"'{sample['name']}'" + (f" - {text}" if text else " (warning text unreadable).")


# The setup-level blocker vocabulary: each code is a state the read VERIFIED, beside the tool that
# clears it. The remedy is looked up per code so a verdict never names a fix for a code it did not
# read. 'no_machine_selected' is minted when the setup's machine reads no label at all.
_SETUP_BLOCKER_REMEDY = {"no_machine_selected": "cam_edit_setup assigns a machine"}


def setup_blockers(setup) -> list:
    """The verified setup-level blocker codes for ONE setup, present-and-empty when none.

    The ONE place a setup's own post prerequisites are read, so cam_get's setups slice, its machine
    slice and every readiness verdict judge one setup off the SAME facts."""
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
# ready_verdict slices the named rows once and hands the SAME slice to the phrase and the remedy,
# which is what stops a fix being offered for a blocker the sentence never printed.
_BLOCKED_ROWS_NAMED = 1


def _blocked_phrase(named, total: int) -> str:
    """The 'name (codes)' clause a verdict names its blocked setups by: the rows in `named` plus a
    count of the `total` it left out. A setup whose name will not read is described rather than
    quoted blank."""
    first = named[0] if named else {}
    name = (first.get("name") or "").strip()
    shown = f"'{name}'" if name else "a setup whose name did not read"
    codes = ", ".join(c for c in (first.get("blocked_by") or []) if c) or "code unreported"
    more = total - len(named)
    return f"{shown} ({codes})" + (f" and {more} more" if more > 0 else "")


def _blocked_remedies(blocked) -> str:
    """The remedy clause for the codes actually PRINTED, deduped in first-seen order. A code with no
    known remedy contributes none - the verdict names a fix only where one is on file.

    It is handed the same rows _blocked_phrase names, never the whole blocked list: a second code in
    the table would otherwise let the sentence carry a remedy for a blocker it never printed."""
    codes = [c for row in (blocked or []) for c in (row.get("blocked_by") or [])]
    remedies = list(dict.fromkeys(_SETUP_BLOCKER_REMEDY[c] for c in codes
                                  if c in _SETUP_BLOCKER_REMEDY))
    return (" " + "; ".join(remedies) + ".") if remedies else ""


def ready_verdict(measure: str, warned: int, warning_sample, blocked=None) -> str:
    """The ONE 'this scope is postable' sentence - built here for EVERY readiness surface
    (live_readiness's document signal, cam_get_status's scoped poll, cam_get's operations summary),
    so no surface can emit a plain 'ready to post' over a job carrying warnings or a BLOCKED setup.

    `measure` is the caller's own count clause ("3 of 3 active ops valid") - each surface counts a
    different thing and keeps its own noun; the VERDICT that clause earns is this function's.

    `blocked` is blocked_setup_records over the setups in the caller's scope. Op state alone cannot
    earn 'ready to post': a setup can hold nothing but valid ops and still carry a blocked_by the
    setups projection reports. What that costs the post is not read here, so the verdict states the
    blocker and withholds the claim rather than asserting the job will fail.

    A WARNED op does NOT block: the job is still postable, so this is never demoted to a blocker.
    But an op can bucket as valid and carry a warning meaning it cut nothing (measured - see
    op_state_tally for the specimen), which a plain 'ready to post' hides - so the count is stated
    and the first warning named instead."""
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


def live_readiness():
    """The SINGLE CAM health/readiness signal for the active document, read live - the home for
    'is this job postable'. cam_get exposes it; cam_get_status (the generation poller) CALLS it instead
    of re-deriving its own op scan. Walks every setup's operations (via walk_operations + the shared
    op_state_tally) PLUS the setup- and NC-program-level errors (a faulted setup/program blocks the job
    even with clean ops; Setup and NCProgram expose the same hasError/error as Operation).

    Returns (signal, None) or (None, reason). signal:
      {valid, out_of_date, errored, generating, suppressed, warnings, total, active,
       setups_errored, programs_errored, setups_blocked, readiness,
       samples:{op,setup,program,warning}}
    setups_blocked is the per-setup blocked_by (setup_blockers) the setups projection publishes -
    read HERE too, so the verdict and cam_get's setup rows answer 'is this job postable' off one
    input set.
    Each level carries ONE sample (name + first error line) - the disclosure signal; the full per-item
    texture is cam_get(include=['operations'/'nc_programs']). 'active' is the op currently computing.
    The op buckets are op_state_tally's (see there for why an ERRORED op is its OWN bucket); the
    all-valid branch words its verdict through ready_verdict (see there for why a warned job never
    reads plainly ready).
    """
    cam, err = get_cam()
    if err:
        return None, err
    samples = {"op": None, "setup": None, "program": None, "warning": None}
    try:
        tally = op_state_tally(walk_operations(cam))
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
    valid, ood, errored = tally["valid"], tally["out_of_date"], tally["errored"]
    warned = tally["warnings"]
    active_total = valid + ood + errored          # active = everything not suppressed
    if errored or setups_errored or programs_errored:
        readiness = ("BLOCKER: "
                     + ", ".join(b for b in [
                         f"{setups_errored} setup(s)" if setups_errored else "",
                         f"{programs_errored} NC program(s)" if programs_errored else "",
                         f"{errored} operation(s)" if errored else ""] if b)
                     + " have errors - the job will not post until fixed.")
    elif active_total and valid == active_total:
        readiness = ready_verdict(f"{valid} of {active_total} active ops valid",
                                  warned, samples["warning"], blocked)
    elif active_total:
        readiness = f"{valid} of {active_total} active ops valid - run cam_generate to finish the rest."
    else:
        readiness = "no active operations to assess."
    return {"valid": valid, "out_of_date": ood, "errored": errored, "generating": tally["generating"],
            "suppressed": tally["suppressed"], "warnings": warned, "total": tally["total"],
            "active": tally["active"],
            "setups_errored": setups_errored, "programs_errored": programs_errored,
            "setups_blocked": blocked,
            "readiness": readiness, "samples": samples}, None


def op_primary_state(facts: dict) -> str:
    """The ONE lifecycle bucket an op falls in, priority-ordered so each op counts once and the tally
    sums to the op total: suppressed > error > generating > no_toolpath > out_of_date > valid. (A
    warning is an OVERLAY, counted separately - it coexists with any of these.) Classifies from the
    op_state_facts dict (the same raw facts op_state_tally shares) rather than re-reading the op."""
    if facts["is_suppressed"]:
        return "suppressed"
    if facts["has_error"]:
        return "error"
    if facts["is_generating"]:
        return "generating"
    state = facts["operation_state"]
    if state == 3:
        return "no_toolpath"
    if state == 1:
        return "out_of_date"
    return "valid"


def validity_basis():
    """'manufacture_verified' iff the Manufacture (CAM) workspace is active - op state/toolpath_valid is
    only trustworthy there (the cam_get description's caveat). Otherwise 'unverified_design_workspace'."""
    try:
        ws = app.userInterface.activeWorkspace
        if ws and ws.id == "CAMEnvironment":
            return "manufacture_verified"
    except Exception:
        pass
    return "unverified_design_workspace"


# ── async generation registry - where every launch path parks its live GenerateToolpathFuture ─────

# Live generations, keyed by a short handle. Each entry holds the Future plus launch metadata.
# Persists across MCP calls for the life of the add-in session.
#
# CRITICAL: holding the GenerateToolpathFuture reference here is not just for polling - if the
# Future is garbage-collected, Fusion ABANDONS the in-progress generation. So this dict is what
# keeps the background work alive between the launch call and the poll calls. Do not stop storing
# the future, and only pop an entry once generation has completed.
_GENERATIONS = {}
_HANDLE_SEQ = [0]


def _carry_generation_keys(old_key, new_key):
    """Re-stamp every live generation launched under a document key that just changed (a save
    re-keys an open document - see _write_guard.on_key_renamed).

    A launch handle survives a stale key on its own (the launch DOCUMENT is kept beside it and
    _same_document falls through to handle equality), so this is not what keeps a status read
    correct - it is what keeps the stored key TRUE, so the fallback covers the one call that
    detects a flip rather than every call after it.
    """
    for entry in _GENERATIONS.values():
        if entry.get("doc_key") == old_key:
            entry["doc_key"] = new_key


on_key_renamed(_carry_generation_keys)


def register_future(future, target, scope, skip_valid, target_name=""):
    """Mint a handle and register a live generation Future - the ONE registration path every launch
    goes through (cam_generate, plus the inline launches in cam_select_geometry and
    cam_create_operation). Keeps the Future referenced and records which DOCUMENT the generation
    belongs to, so a later status read taken while another document is active reports the Future's
    own progress instead of the wrong document's tallies. Returns (handle, total).

    That record is document_key, not the lineage urn alone: a never-saved document HAS no urn, so a
    urn-only record leaves every launch from a scratch document unidentifiable and its status read
    falls back to the Future alone rather than the per-op tallies it could read. doc_name / doc_urn
    stay beside it because the payload NAMES the generating document from them.

    target_name is the RAW setup/folder/operation name a scoped launch resolved to (omit it for a
    whole-document launch): a status read settles this handle's completion on THAT target's own
    operations, so a second generation running beside it cannot keep this handle incomplete.

    'doc' is the launch document itself, kept beside that key because the key is derived from what
    reads on the document and the DOCUMENT outlives the derivation: ONE launch document saved
    mid-generation can answer a different key MORE THAN ONCE - those flips, and the PROBE NEEDED
    (KEY-2) on the transient path-form id behind them, are _write_guard.document_key's. Each flip is
    announced (_carry_generation_keys above re-stamps this entry), and through every one of them the
    same open document still compares equal by HANDLE - the comparison document_key already matches
    a never-saved document on - so _same_document answers on the re-stamped key from the call after
    a flip, and on the handle during the call that detected it."""
    _HANDLE_SEQ[0] += 1
    handle = f"gen{_HANDLE_SEQ[0]}"
    total = safe(lambda: future.numberOfOperations, None)
    doc_name, doc_urn = _active_identity()
    _GENERATIONS[handle] = {
        "future": future,
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

# Non-network machine library locations searched for a machine by vendor/model (Fusion360 = the
# bundled sample machines; Local = the user's saved ones). The cloud/network locations are skipped so
# a headless assignment never blocks on a fetch.
_MACHINE_LOCATIONS = ("LocalLibraryLocation", "Fusion360LibraryLocation")

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
    """Which library location holds `machine`: 'local', 'fusion360', or 'local or fusion360' when
    the Local query itself failed and the two cannot be told apart. ONE FILTERED Local query -
    query_machines searches Local first, so a Local hit carrying this machine's id means Local."""
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
    """ONE machine's 'kind' labels off its capabilities flags - the per-machine read behind the
    catalog's kind column. Reading capabilities is the EXPENSIVE part of a machine row (measured
    ~47ms/machine over the 982-machine bundled library, ~46s for one unfiltered walk), so a caller
    that needs one machine's kinds calls this instead of walking the catalog."""
    caps = safe(lambda: m.capabilities)
    return [k for k, attr in sorted(_MACHINE_KINDS.items())
            if bool(safe(lambda caps=caps, attr=attr: getattr(caps, attr), False))]


def machine_label(m):
    """Readable machine label: .description, else 'vendor model'. adsk.cam.Machine has no .name."""
    if not m:
        return None
    desc = safe(lambda: m.description)
    if desc:
        return desc
    label = ((safe(lambda: m.vendor) or "") + " " + (safe(lambda: m.model) or "")).strip()
    return label or "(unnamed machine)"


def machine_ident(m):
    """(label, vendor, model) for a Machine - label is the readable name (description or 'vendor model')."""
    return machine_label(m), (safe(lambda: m.vendor) or ""), (safe(lambda: m.model) or "")


# ── the machine's own limits: spindle speed + axis travels, off its kinematics tree ────────────────
#
# The route is Machine.elements -> the KinematicsMachineElement -> .parts (a TREE: each MachinePart
# carries .children plus an optional .axis / .spindle / .toolStation). Machine.kinematics reaches
# the same tree in one step and the bindings flag it "not officially supported", so it is never read
# here. Measured on a library Haas A-axis machine: 7 parts, spindle maxSpeed 12000 rpm, X/Y/Z ranges
# 76.2/40.6/50.8 cm and an A axis whose range reads isInfinite.
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
    """{max_rpm, min_rpm} for every spindle in the parts list, highest maxSpeed first. A maxSpeed of
    0 is not a speed anything can be compared against, so it reads as None (never as a limit of 0)
    and sorts last."""
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
    """The machine's highest readable spindle maxSpeed in rpm, or None when no spindle answers one -
    the one number the per-operation over-max comparison is made against, so the machine slice and
    that comparison can never quote different maxima."""
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
            # A zero on a tool station is what an UNSET field reads as on a library machine
            # definition (measured: maxToolDiameter and maxToolLength both 0.0 on a machine whose
            # spindle maxSpeed read 12000), so a zero is never published as a limit of zero. The cm
            # scale below follows the axis ranges, which ARE measured cm on the same machine; no
            # machine reading a NON-zero station has been found to exercise it (PROBE NEEDED).
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
    """(over, requested, marker) for one operation against its machine's spindle maximum.

    over is True only when the operation asks for MORE than the maximum - a request AT the maximum
    is not over it. Either number unreadable answers None with a marker naming which side, never a
    coerced False; the marker is what tells "checked and fine" from "could not be checked"."""
    if machine_max is None:
        return None, None, "machine_max_unavailable"
    requested = op_spindle_speed(op)
    if requested is None:
        return None, None, "op_spindle_speed_unreadable"
    return requested > machine_max, requested, None


def query_machines(lib, vendor, model):
    """Run the machine-library query for (vendor, model) across the Local + bundled Fusion360
    locations, deduped by label. Returns a list of (machine, label, vendor, model); the FIRST
    location that yields any match wins (Local before Fusion360)."""
    found, labels = [], set()
    for loc_name in _MACHINE_LOCATIONS:
        loc = getattr(adsk.cam.LibraryLocations, loc_name, None)
        if loc is None:
            continue
        try:
            matches = lib.createQuery(loc, vendor, model).execute() or []
        except Exception:
            continue
        for m in matches:
            label, v, mo = machine_ident(m)
            if label in labels:      # dedupe identical machines that appear in more than one location
                continue
            labels.add(label)
            found.append((m, label, v, mo))
        if found:
            break                    # prefer the first location that yields any match
    return found


def machine_catalog(vendor: str = "", machine_type: str = "", max_results: int = 100):
    """(rows, truncated, error) - the machine CATALOG the 'machine' input resolves from: every
    machine in the Local + Fusion360 locations (the same two resolve_machine searches), filtered by
    vendor and/or machine_type. The ONE catalog read: cam_edit_setup.read_machines is its wire
    wrapper (cam_get(include=['machines'])) and cam_create_machine checks a new name against these
    rows before creating anything."""
    mt = (machine_type or "").strip().lower()
    if mt and mt not in _MACHINE_KINDS:
        return None, False, (f"Unknown machine_type '{machine_type}'. Valid: "
                             f"{', '.join(sorted(_MACHINE_KINDS))}.")
    try:
        lib = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    except Exception as e:
        return None, False, f"Could not access the machine library: {e}"
    rows, total = [], 0
    for loc_name in _MACHINE_LOCATIONS:
        loc = getattr(adsk.cam.LibraryLocations, loc_name, None)
        if loc is None:
            continue
        loc_label = loc_name.replace("LibraryLocation", "").lower()   # 'local' / 'fusion360'
        try:
            matches = lib.createQuery(loc, vendor or "", "").execute() or []
        except Exception:
            continue
        for m in matches:
            kinds = machine_kinds(m)
            if mt and mt not in kinds:
                continue
            total += 1
            if len(rows) >= max_results:
                continue
            label, v, mo = machine_ident(m)
            rows.append({"name": label, "vendor": v, "model": mo, "location": loc_label,
                         "kind": kinds,
                         "simulation_ready": bool(safe(lambda m=m: m.hasSimulationModel, False))})
    return rows, total > len(rows), None


def _exact_machine(cands, machine, vendor, model):
    """Exact-match, MOST-SPECIFIC first: a unique full-LABEL match wins over a unique 'vendor model'
    match, which wins over a unique model match. Prioritizing the label is what makes same-model
    variants selectable - a Haas library ships three machines that all report vendor|model 'HAAS|VF-2'
    and differ ONLY by description ('Haas VF-2', 'Haas VF-2 with TRT100', ...), so matching the model
    alone can't pick one, but the exact description can. Returns the single candidate at the first
    priority yielding exactly one hit, else None (still ambiguous)."""
    ml = (model or "").strip().lower()
    ven = (vendor or "").strip().lower()
    full = (machine or "").strip().lower()

    def _unique(pred):
        hits, seen = [], set()
        for tup in cands:
            _m, label, v, mo = tup
            if pred(label, v, mo):
                key = (label or "").lower()
                if key not in seen:
                    seen.add(key)
                    hits.append(tup)
        return hits[0] if len(hits) == 1 else None

    return (_unique(lambda label, v, mo: (label or "").lower() == full)                       # label
            or _unique(lambda label, v, mo: ((v or "") + " " + (mo or "")).strip().lower() == full)  # vendor model
            or _unique(lambda label, v, mo: bool(ml) and (mo or "").lower() == ml             # model (+vendor)
                       and (not ven or (v or "").lower() == ven)))


def resolve_machine(machine):
    """Resolve a 'machine' string (vendor|model, vendor/model, a bare model, or a full description) to
    a single Machine. Returns (machine, label, None), or (None, None, error) when nothing matches or
    the match is ambiguous - it refuses to guess. Exact match (LABEL first) beats a shared prefix.
    The ONE machine resolver: cam_edit_setup assigns through it, and cam_create_machine gates a new
    machine's reachability on it."""
    machine = (machine or "").strip()
    sep = "|" if "|" in machine else ("/" if "/" in machine else "")
    if sep:
        vendor, model = (p.strip() for p in machine.split(sep, 1))
    else:
        vendor, model = "", machine
    try:
        lib = adsk.cam.CAMManager.get().libraryManager.machineLibrary
    except Exception as e:
        return None, None, f"Could not access the machine library: {e}"

    cands = query_machines(lib, vendor, model)
    # WIDEN when the model as given matches nothing: the library query prefix-matches the MODEL field,
    # but a variant's distinguishing text ('Haas VF-2 with TRT100') lives in its DESCRIPTION, and callers
    # pass the label they SEE ('Haas VF-2', 'Haas|Haas VF-2'). Recover a (vendor, broad-model-token) to
    # fetch the candidate POOL, then LABEL-match it below.
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

    if not cands:
        return None, None, (f"No machine matches '{machine}' (vendor='{vendor}', model='{model}') in the "
                            "Local or Fusion360 machine libraries. Use the machine name (its description) "
                            "you see in the Manufacture machine library.")
    # EXACT match wins BEFORE refusing ambiguity (the house rule).
    exact = _exact_machine(cands, machine, vendor, model)
    if exact is not None:
        return exact[0], exact[1], None
    if len(cands) > 1:
        # List the distinct LABELS (descriptions) - the selectable key, since same-model variants share
        # vendor|model. The agent passes one of these exact names back to pick a specific variant.
        labels, seen = [], set()
        for (_m, lab, _v, _mo) in cands:
            if lab and lab.lower() not in seen:
                seen.add(lab.lower())
                labels.append(lab)
        return None, None, (f"Ambiguous machine '{machine}' - {len(labels)} matches: "
                            f"{', '.join(labels[:8])}. Pass one of these exact names.")
    return cands[0][0], cands[0][1], None


# ── CAM LIBRARY folder walks - the ONE traversal the tool / post / template libraries share ──────
#
# All three expose the same shape off a LibraryLocations root url: childFolderURLs nests, and the
# leaves are read per library kind (childAssetURLs for a tool library or a post config,
# childTemplates for a template). What the bounds below are for: a cloud/Hub tree NESTS and is
# network-slow, and enumerating one unbounded is a measured way to hang the add-in.

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
    per folder INCLUDING the root - the ONE library traversal; the leaf op stays the caller's.
    `visit` returns True to stop the whole walk (its own collection is full). Bounded on both axes:
    `max_depth` levels below the root and `max_folders` folders visited. An absent root walks nothing.
    Returns True when a cap (or a full `visit`) stopped it early - i.e. the read is INCOMPLETE."""
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
    """(assets, truncated) - every child ASSET url under a library location root, folders recursed:
    the collect-the-assets projection of walk_library_folders that the tool-library and post-library
    reads share. An asset url is the addressable identity of a library/post - .leafName is its name
    and .toString() its url - which is why the walk collects those rather than the heavy loaded
    objects. `max_assets` caps the collection itself; truncated is True when any cap stopped it."""
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
    before the LAST dot.

    MEASURED live: a stored asset's leafName carries the file extension
    ('SweepMach3Axis 20260830-194520.mch') while the machine's own name does not, so comparing the
    name against the whole leafName finds nothing at all. The STEM is compared rather than the name
    plus a hardcoded extension because the extension is the library's to choose, and the whole
    leafName is kept in the set so an asset stored WITHOUT one still matches. Both are EXACT
    comparisons - a substring match here would delete 'SweepMach3Axis Mk2.mch' for
    'SweepMach3Axis'."""
    leaf = asset_leaf(url).lower()
    keys = {leaf}
    stem = leaf.rpartition(".")[0]
    if stem:
        keys.add(stem)
    return keys


def assets_named(assets, wanted):
    """The assets one of whose names - leafName as stored, or its stem - EXACTLY matches
    (case-insensitively) one of `wanted`, deduped by url."""
    keys, hits = set(), []
    for a in assets:
        if asset_leaf_keys(a) & wanted:
            key = asset_key(a)
            if key not in keys:
                keys.add(key)
                hits.append(a)
    return hits
