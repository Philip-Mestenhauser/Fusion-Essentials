# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared CAM substrate: resolves the active document's CAM product and judges job health, for
cam_get and the CAM action/poll tools (cam_get_status, cam_activate_setup, ...) to reuse."""

import collections
import json
import math
import re
import time

import adsk.core
import adsk.cam
import adsk.fusion

from ._common import (CM_TO_UNIT, counted, measured, named_with_remainder, ok, error,
                      iter_collection, read_flag, safe, told_apart)
from ._write_guard import (_active_identity, document_key,   # the one active-document identity read,
                           on_key_renamed)                   # the one key a cross-call store
                                                             # remembers a document by, and the
                                                             # announcement when that key changes

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("get_cam (the shared CAM-product resolver every CAM tool calls) + walk_cam_tree / "
             "resolve_cam_node (the ONE CAM tree traversal + by-name resolver every CAM tool targets "
             "through: case-insensitive EXACT, a miss lists the available names, a DUPLICATED name is "
             "REFUSED naming each hit's '<name>#<n>' address - the form the SAME input takes to pick "
             "one, since a setup's path is its own bare name and discriminates nothing - beside the "
             "path that tells an operation apart or the operation count that tells a setup apart; "
             "kinds=/setup= scope it) + owning_setup (the "
             "Setup OBJECT one CamNode sits under, climbed through the walk's own parent links - "
             "never by re-resolving node.setup by NAME, since setup names can collide) + "
             "operations_under (the "
             "ops nested under one setup/folder/pattern) + find_setup (a (setup, available_names, "
             "error) wrapper handing back the resolver's refusal verbatim) + operation_nodes_under "
             "(the operations under ONE setup/folder/pattern node as CamNodes, walked from that "
             "node's own path - the form a caller that NAMES its operations on the wire takes, "
             "since the object-only operations_under drops the breadcrumb that tells two "
             "same-named operations apart) + operation_nodes (the "
             "ONE operation pool every by-name operation resolve and available-name listing reads "
             "off) + find_operation (the "
             "(obj, available_names) wrapper over the same resolver) + resolve_operation (the "
             "unscoped resolve handing back the refusal AND that same available list off ONE walk, "
             "for a caller wording a narrower remedy of its own) + expression_error (the post-set "
             "CAMParameter evaluation read-back every CAM param editor gates on) + live_readiness "
             "(the one CAM job-health signal) + setup_blockers / blocked_setup_records (the ONE read "
             "of a setup's OWN post prerequisites - today no_machine_selected, when Setup.machine "
             "reads no label - and its [{name, blocked_by}] projection over a list of setups; "
             "cam_get's setups slice, its machine slice and every readiness verdict consume this "
             "one read, so they cannot answer 'is this postable' off different inputs) + "
             "ready_verdict / first_line (the ONE postable-verdict "
             "sentence every readiness surface emits - it demotes 'ready to post' whenever active-op "
             "warnings > 0, naming the first warning op and line, and WITHHOLDS it entirely while "
             "any setup in scope carries a blocked_by, naming that setup and the remedy on file for "
             "its code, so no scoped or summary re-roll "
             "can overstate) + op_state_facts / op_primary_state / validity_basis "
             "(the shared per-op lifecycle read, its one mutually-exclusive bucket classifier, and "
             "the Manufacture-workspace trust gate every op-state rollup reads) + clamp_rows (the "
             "ONE 'max_results' clamp - a non-numeric request falls back to the read's default, the "
             "result is held inside 1..ceiling, so no caller can lift a wire cap) + register_future "
             "(the ONE async-generation registration - it mints the handle cam_get_status reads and "
             "keeps the GenerateToolpathFuture referenced, which is what stops Fusion abandoning the "
             "background work, and stamps the launch document's _write_guard.document_key so a status "
             "read can tell the generating document from the active one even when neither was ever "
             "saved; every launch path registers here) + machine_catalog / resolve_machine "
             "/ machine_label / machine_ident / machine_kinds / query_machines (the ONE "
             "machine-library catalog read and the ONE by-name machine resolver - exact LABEL match "
             "first, ambiguity REFUSED - that an assignment and a machine create both run through; "
             "machine_kinds is the per-machine capabilities read, the expensive part of a catalog "
             "row, for a caller that needs ONE machine's kinds without a 46s unfiltered walk) + "
             "machine_library / machine_location (the ONE MachineLibrary handle - it hangs off "
             "CAMManager.libraryManager, so no open CAM job is needed - and the ONE 'which location "
             "holds this machine' read, a single FILTERED Local query answering local / fusion360, "
             "or 'local or fusion360' when that query itself failed and the two cannot be told "
             "apart; the create's clash report and the delete's local-only gate read the same "
             "answer) + "
             "parse_parameters (the ONE "
             "{name: expression} / 'name=value, ...' parameter-request parser both CAM parameter "
             "editors validate their request through) + walk_library_folders / library_assets / "
             "library_children (the ONE CAM library folder-tree walk - tool, post and template "
             "libraries all nest folders under a location root, so every walk is bounded on depth "
             "AND folder count and each site passes its own leaf op; library_assets is the "
             "collect-the-child-asset-urls projection over it) + asset_leaf / asset_key / "
             "asset_leaf_keys / assets_named (the ONE 'which asset answers to this name' matcher "
             "every library DELETE resolves its target on: a stored leafName carries the file "
             "EXTENSION the object's own name does not, so an asset answers to its whole leafName "
             "AND to its stem - the part before the LAST dot - both compared EXACTLY, since a "
             "substring match here deletes the neighbour whose name merely starts the same; "
             "assets_named is the deduped-by-url hit list over a wanted-name set) + "
             "is_empty_toolpath (the ONE "
             "'generated but cut nothing' test over op_state_facts - has_toolpath read False AND "
             "is_toolpath_valid read True on a valid op; an UNREADABLE flag answers False, never "
             "'empty') + kinematics_parts / machine_limits / machine_spindle_max (the ONE "
             "Machine.elements -> kinematics -> parts walk and the spindle-max / axis-travel / "
             "tool-station projections over it - never Machine.kinematics, never the -1 "
             "machine_dimension_x/y/z setup parameters; a 0 or infinite field is omitted, never a "
             "limit of 0) + spindle_check (the ONE op-asks vs machine-allows rpm comparison: True "
             "only when the op asks for MORE, None with a marker naming the unreadable side)")

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


# The segment a breadcrumb carries for a level whose own name did NOT read. The walk joins whatever
# each level answered, and a None joined into an f-string prints the literal 'None' - a segment
# nothing tells apart from a container actually NAMED that, so the path reads as a complete address
# to a container that was never identified. This marker is the disclosure instead: the path states
# that a level did not read rather than naming one.
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
    twice. A caller that only needs the objects calls operations_under; a caller that NAMES the
    operations on the wire takes this."""
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

    On a miss `setup` is None and `error` is resolve_cam_node's ready-to-return refusal - a plain
    absence lists the available names, a DUPLICATED name is REFUSED as ambiguous (never resolved to
    the first hit). Callers return that text verbatim: the resolver is the one place that knows
    WHICH of the two happened, so a caller wrapping it in its own 'not found' prefix would assert
    absence about a name that was found twice. `available_names` stays the plain name list it has
    always been, for callers that offer the choices elsewhere in their payload."""
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
    But an op can bucket as valid and carry a warning meaning it cut nothing (measured: a
    geometry-less 2D Contour reads isToolpathValid True with hasToolpath False), which a plain
    'ready to post' hides - so the count is stated and the first warning named instead."""
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
    An ERRORED op is its OWN bucket: it has a parameter/geometry fault and will NEVER finish generating,
    so counting it as out_of_date/generating would make a poller wait forever. A WARNED op does NOT
    block - it can be posted - but it never reads as a plain 'ready to post' either: the verdict states
    the warning count and names the first one, because an op can carry a warning and still bucket as
    valid (measured: a 2D Contour with no geometry selected reads valid with no toolpath).
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


# ---------------------------------------------------------------------------
# Read-implementation handlers + helpers behind cam_get's slices (cam_get(include=[...])).
# These are the rich read's LOGIC; cam_get is the thin router/surface over them. Not tools.
# ---------------------------------------------------------------------------

_MAX_ITEMS = 1000

# safe() cannot tell "read None" from "the read raised", and those are different answers wherever a
# CAM property RAISES instead of reading empty.
_MISSING = object()

# Why an operation went out of date - Fusion records it in op.messageLog (NOT in op.warning/op.error,
# which are empty for a plain invalidation). Two kinds of line:
#   "<ts> I Invalidated: Design changed: Op1: WCS origin"      <- the high-signal CATEGORY of change
#   "... different value for parameter 'tool_feedCutting' ..." <- one of many per-parameter deltas (noise)
# We surface the deduped categories (what an agent needs to intuit "the WCS moved") and collapse the
# parameter deltas to a count.
_INVAL_CATEGORICAL = ("Design changed", "Dependency changed", "Holder changed", "Tool", "Stock", "Suppress")
_INVAL_REASON_CAP = 12
_INVAL_RE = re.compile(r"Invalidated:\s*(.+?)\s*$")
_INVAL_PARAM_RE = re.compile(r"different value for parameter '")
# The MACHINE definition / its limits changing is logged as "External changed: machine.<field>" (NOT an
# "Invalidated:" line). It's a setup-wide signal (the post target shifted), so it's surfaced separately.
_INVAL_MACHINE_RE = re.compile(r"External changed:\s*machine\.")

def _invalidation_reasons(op):
    """Parse op.messageLog into (categorical_reasons, parameter_change_count, machine_changed). Reasons
    are deduped, order-preserved, capped. machine_changed is True if the machine definition/limits
    changed. Only meaningful for an out-of-date op (a valid op's log has none)."""
    ml = safe(lambda: op.messageLog) or ""
    reasons = []
    param_changes = 0
    machine_changed = False
    for line in ml.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        if _INVAL_MACHINE_RE.search(line):
            machine_changed = True
            continue
        if _INVAL_PARAM_RE.search(line):
            param_changes += 1
            continue
        m = _INVAL_RE.search(line)
        if not m:
            continue
        reason = m.group(1).strip()
        if any(reason.startswith(c) for c in _INVAL_CATEGORICAL) and reason not in reasons:
            reasons.append(reason)
    return reasons[:_INVAL_REASON_CAP], param_changes, machine_changed

def _operation_type_name(op_type) -> str:
    """Map an OperationTypes enum value to a readable name, defensively."""
    mapping = {
        getattr(adsk.cam.OperationTypes, n, object()): n
        for n in ("MillingOperation", "TurningOperation", "JetOperation",
        "AdditiveOperation")
    }
    return mapping.get(op_type, str(op_type))


def _model_names(getter) -> tuple:
    """(names, truncated) - readable names of one of a setup's model/fixture/stock collections
    (Occurrence/BRepBody/MeshBody), capped at _MAX_ITEMS. Takes the GETTER, not the collection: the
    property itself RAISES on some setups (measured twice: Setup.models raises "3 : input is null"
    on a setup whose selected occurrence was removed by doc_insert_occurrence(remove_existing=...)),
    and safe(read, []) turns that raise into an empty list indistinguishable from a setup that
    selected nothing.

    names is None when the collection property RAISED - nothing about its contents is claimed.
    truncated means INCOMPLETE for any of three reasons: the property raised, the cap was hit, or
    the collection raised mid-iteration (a short list from a dying walk is indistinguishable from a
    full read without the flag)."""
    collection = safe(getter, _MISSING)
    if collection is _MISSING:
        return None, True
    names = []
    truncated = False
    try:
        for i, m in enumerate(collection):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            names.append(safe(lambda: m.name, "(unnamed)"))
    except Exception:
        truncated = True
    return names, truncated

# The setup's WCS lives in the setup's own CAMParameters, which is where cam_edit_setup binds it -
# setup.parameters.itemByName reads both kinds back (live-verified, Fusion 2705): a mode parameter is
# a ChoiceParameterValue whose .value.value is the mode string ('point', 'modelOrientation',
# 'axesZX'), and a geometry binding is a CadObjectParameterValue whose .value.value is an ITERABLE of
# the bound entities.
_WCS_MODE_PARAMS = (("origin_mode", "wcs_origin_mode"),
                    ("orientation_mode", "wcs_orientation_mode"))
_WCS_ENTITY_PARAMS = (("origin_entities", "wcs_origin_point"),
                      ("orientation_z_entities", "wcs_orientation_axisZ"))


def _wcs_bound_entities(param) -> list:
    """[{type, name?}] for ONE CadObjectParameterValue's bound entities. The entity kind is always
    published (the leaf of objectType, so 'adsk::fusion::JointOrigin' and 'JointOrigin' both read
    'JointOrigin'); a name is emitted only when it reads back non-empty, because not every bindable
    entity carries a readable one (a BRepFace does not)."""
    rows = []
    for ent in safe(lambda: list(param.value.value), []) or []:
        kind = safe(lambda ent=ent: ent.objectType) or ""
        row = {"type": str(kind).split("::")[-1] or None}
        name = safe(lambda ent=ent: ent.name)
        if name:
            row["name"] = name
        rows.append(row)
    return rows


def setup_wcs(setup):
    """ONE setup's WCS as BOUND state - {origin_mode, orientation_mode, origin_entities,
    orientation_z_entities} - the read-back side of cam_edit_setup's 'wcs' binding. Terse: a mode
    that does not read and an EMPTY entity list are omitted, so a box-point WCS carries modes only.
    None when the setup exposes no readable parameters - nothing about its WCS can be claimed then."""
    params = safe(lambda: setup.parameters)
    if params is None:
        return None
    wcs = {}
    for key, pname in _WCS_MODE_PARAMS:
        mode = safe(lambda pname=pname: params.itemByName(pname).value.value)
        if mode is not None:
            wcs[key] = mode
    for key, pname in _WCS_ENTITY_PARAMS:
        p = safe(lambda pname=pname: params.itemByName(pname))
        rows = _wcs_bound_entities(p) if p is not None else []
        if rows:
            wcs[key] = rows
    return wcs or None


def get_cam_setups_handler() -> dict:
    cam, err = get_cam()
    if err:
        return error(err)

    setups = []
    setups_truncated = False
    try:
        setups_total = safe(lambda: cam.setups.count, 0) or 0
        for i in range(setups_total):
            if i >= _MAX_ITEMS:
                setups_truncated = True
                break
            s = cam.setups.item(i)
            models, models_trunc = _model_names(lambda: s.models)
            fixtures, fixtures_trunc = _model_names(lambda: s.fixtures)
            stock, stock_trunc = _model_names(lambda: s.stockSolids)
            setups.append({
        "name": safe(lambda: s.name),
        "operation_type": _operation_type_name(safe(lambda: s.operationType)),
        "is_active": safe(lambda: s.isActive),
        "machine": machine_label(safe(lambda: s.machine)),
            # The bound WCS - the only read-back of what cam_edit_setup's 'wcs' binding did.
            "wcs": setup_wcs(s),
            # null (not []) for a list whose collection property RAISED - see _model_names.
            "selected_models": models,
            "fixtures": fixtures,
            "stock_solids": stock,
            # True when any of the three lists above is INCOMPLETE: its property raised, the walk
            # raised mid-iteration, or the _MAX_ITEMS cap was hit.
            "model_lists_truncated": bool(models_trunc or fixtures_trunc or stock_trunc),
            # operation_count = the REAL total (allOperations sees ops nested in folders); a
            # folder-organized shop setup must not read as empty. folder_count is the depth breadcrumb
            # (structure exists; include=['operations'] groups by it) without the per-folder texture.
            "operation_count": safe(lambda: s.allOperations.count, 0),
            "folder_count": safe(lambda: s.folders.count, 0),
            })
            # Rollup of WHY this setup is stale: how many ops are out of date + the DISTINCT reasons
            # across them (so a cold orientation read hints "the WCS moved", not just "47 stale ops").
            _attach_setup_invalidation(setups[-1], s)
            # Which of the three lists is null because its property raised - present only when one
            # is, so the marker names the unread collection instead of leaving a bare null to read
            # as "none selected".
            unreadable = [key for key, names in (("selected_models", models), ("fixtures", fixtures),
                                                 ("stock_solids", stock)) if names is None]
            if unreadable:
                setups[-1]["model_lists_unreadable"] = unreadable
            # The setup's own post prerequisites, through the shared read every readiness verdict
            # also consumes. Verified state, present-and-empty.
            setups[-1]["blocked_by"] = setup_blockers(s)
    except Exception as e:
        return error(f"Could not read setups: {e}")

    return ok({"setup_count": len(setups), "setups": setups, "truncated": setups_truncated})

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


# Reason-code vocabulary. Each MUST be a state the code can VERIFY and
# that Fusion actually refuses on - never an invented or intent-guessed block. A SUPPRESSED op blocks
# nothing (it's excluded from posting by design), so its blocked_by is always [].
_GENERATE_REQUIRES = {"tool": "cam_generate", "workspace": "Manufacture"}


def _op_blocked_by(op, summary):
    """(blocked_by, requires) for one op. blocked_by is a list of verified reason codes; present-and-
    empty when nothing blocks. requires is the tool/workspace to unblock, only when applicable."""
    if summary.get("is_suppressed"):
        return [], None                    # suppressed = excluded from posting; blocks nothing
    blocked = []
    requires = None
    if summary.get("tool") is None:
        blocked.append("tool_unselected")  # real refusal: "Toolpath requires tool to be selected"
    if summary.get("is_out_of_date"):
        blocked.append("toolpath_out_of_date")
        requires = dict(_GENERATE_REQUIRES)
    return blocked, requires


def _attach_setup_invalidation(rec, setup):
    """In ONE walk of the setup's ops, add: op_states (the per-state tally, terse - zero buckets
    dropped; the out-of-date COUNT lives here as op_states['out_of_date'], not duplicated), the distinct
    invalidation_reasons across the out-of-date ops, and machine_out_of_date if the machine definition
    changed. A clean setup shows op_states={valid:N}."""
    tally = {}
    warnings = 0
    reasons = []
    machine_changed = False
    try:
        for o in setup.allOperations:
            op = adsk.cam.Operation.cast(o)
            if op is None:
                continue
            facts = op_state_facts(op)
            st = op_primary_state(facts)
            tally[st] = tally.get(st, 0) + 1
            if facts["has_warning"]:
                warnings += 1
            if st == "out_of_date":
                op_reasons, _, op_machine = _invalidation_reasons(op)
                if op_machine:
                    machine_changed = True
                for r in op_reasons:
                    if r not in reasons:
                        reasons.append(r)
    except Exception:
        pass
    if warnings:
        tally["warning"] = warnings        # overlay: ops with a warning (may also be in another bucket)
    if tally:
        rec["op_states"] = tally           # terse: only non-zero buckets present (incl. out_of_date)
    # invalidation_reasons = WHY the out-of-date ops are stale (the count is op_states['out_of_date']).
    if tally.get("out_of_date", 0) and reasons:
        rec["invalidation_reasons"] = reasons[:_INVAL_REASON_CAP]
    if machine_changed:
        rec["machine_out_of_date"] = True

# The spindle marker a SUPPRESSED row carries in place of the comparison: it was not made, and
# 'not made' is a different fact from either answer or from a number that would not read.
_SUPPRESSED_NOT_COMPARED = "suppressed_not_compared"

_OPERATIONS_NOTE = (
    "Per row: 'path' is the Setup / Folder / Operation breadcrumb and 'folder' the container the op "
    "sits in, which reads beside is_suppressed; 'preset' is the tool preset this op uses, which two "
    "ops sharing one tool can differ on. 'spindle_over_machine_max' compares the op's "
    "tool_spindleSpeed against the setup's machine_spindle_max_rpm: true is over it, false is at or "
    "under it, and null means one of the two could not be read - 'spindle_check' names which side. "
    "A SUPPRESSED op is excluded from posting, so it is NOT compared at all: its row carries "
    "spindle_check 'suppressed_not_compared' and no flag. summary.spindle_over_machine_max_count "
    "counts the ACTIVE rows that are over.")


def get_cam_operations_handler(setup: str = "") -> dict:
    """Operations across all setups, or just the named setup (`setup`)."""
    cam, err = get_cam()
    if err:
        return error(err)

    # Two-branch filter: a named setup resolves through the shared resolver (a duplicated setup
    # name is REFUSED, not first-matched); empty = every setup via the shared walk.
    want = (setup or "").strip()
    if want:
        node, rerr = resolve_cam_node(cam, want, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        target_setups = [node.obj]
    else:
        target_setups = setups(cam)

    result_setups = []
    try:
        for s in target_setups:
            # Read the setup's machine maximum ONCE - every op row under it is compared against
            # this same number, so the per-op flags cannot quote different maxima.
            machine_max = machine_spindle_max(safe(lambda s=s: s.machine))
            ops, ops_truncated = _operations_in(s, machine_max)
            # This setup's own blockers ride into the verdict: op state alone cannot earn
            # 'ready to post' while the setup projection reports a blocked_by for the same setup.
            blocked = blocked_setup_records([s])
            rec = {
            "setup": safe(lambda s=s: s.name),
            "summary": _operations_summary(ops, blocked),   # exception-first rollup BEFORE the list
            "operations": ops,
            "operations_truncated": ops_truncated,
            }
            if machine_max is not None:
                rec["machine_spindle_max_rpm"] = machine_max
            result_setups.append(rec)
    except Exception as e:
        return error(f"Could not read operations: {e}")

    # Also summarize the distinct tools used across the returned operations.
    tools_used = {}
    for rs in result_setups:
        for op in rs["operations"]:
            t = op.get("tool")
            if t:
                tools_used[t] = tools_used.get(t, 0) + 1

    return ok({
    "setup_count": len(result_setups),
    "setups": result_setups,
    "tools_used": [{"tool": k, "operation_count": v} for k, v in tools_used.items()],
    "note": _OPERATIONS_NOTE,
    })


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


def _operations_summary(op_records, setup_blocked=None) -> dict:
    """Exception-first rollup of an operations list. states = the count tally over each row's own
    'state' - which _operation_summary derives through op_primary_state, so this tally and the
    per-setup op_states rollup are the SAME classification and cannot contradict each other;
    exceptions = only ACTIVE ops that block (suppressed ops never block); readiness = a factual
    next-action string, gated by validity_basis (no toolpath verdict unless Manufacture-verified)
    and worded by the shared ready_verdict, so a warned job - or one whose SETUP carries a
    blocked_by (`setup_blocked`, from blocked_setup_records) - never reads plainly ready here
    either. The name keeps the SETUP-level list apart from each row's own op-level `blocked`.

    spindle_over_machine_max_count is the same active-only scoping over the rows' spindle flag: a
    suppressed row carries no flag to count, so the aggregate and the rows agree by construction."""
    states = {}
    exceptions = []
    active_total = 0
    valid_active = 0
    warned = 0
    over_spindle = 0
    warning_sample = None
    for r in op_records:
        st = r.get("state")
        states[st] = states.get(st, 0) + 1
        if r.get("is_suppressed"):
            continue                              # suppressed = excluded from posting; not active, not blocking
        active_total += 1
        # Counted past the same suppressed skip the rest of this rollup uses, so the aggregate
        # covers exactly the rows that carry a comparison. `is True` only: a null is a comparison
        # that could not be made, and counting it would state a number the reads do not support.
        if r.get("spindle_over_machine_max") is True:
            over_spindle += 1
        # Counted through the SAME predicate live_readiness counts by (the record carries the facts
        # it reads), so the two surfaces can never disagree about which warnings demote a verdict.
        if counts_as_warning(r):
            warned += 1
            if warning_sample is None:
                warning_sample = {"name": r.get("name"), "warning": first_line(r.get("warning"))}
        has_err = bool(r.get("has_error"))
        # An op counts as good-to-post only when its toolpath is valid AND it carries no error.
        # has_error is the authoritative per-op fault flag this record ships beside the toolpath flag
        # (a toolpath can read valid while the op is errored - live: "4 of 4 valid, ready to post"
        # while a Drill op carried has_error). Derive the summary from BOTH, never toolpath_valid alone.
        if r.get("toolpath_valid") and not has_err:
            valid_active += 1
        blocked = list(r.get("blocked_by") or [])
        if has_err and "operation_error" not in blocked:
            blocked.append("operation_error")    # an errored op blocks the post even if nothing else flags it
        if blocked:
            exceptions.append({"name": r.get("name"), "blocked_by": blocked})

    basis = validity_basis()
    summary = {"states": states, "active_count": active_total, "exceptions": exceptions,
               "validity_basis": basis}
    if over_spindle:
        summary["spindle_over_machine_max_count"] = over_spindle   # active rows only; absent = none
    if basis == "manufacture_verified":
        if active_total and valid_active == active_total and not exceptions:
            summary["readiness"] = ready_verdict(
                f"{active_total} of {active_total} active ops have valid toolpaths",
                warned, warning_sample, setup_blocked)
        else:
            summary["readiness"] = (f"{valid_active} of {active_total} active ops have valid toolpaths - "
                                    "resolve the exceptions (run cam_generate) before posting.")
    else:
        summary["readiness"] = ("op validity is only trustworthy after entering the Manufacture "
                                "workspace - enter it (and run cam_generate) to assess post-readiness.")
    return summary



def _operations_in(setup_obj, machine_max=None) -> tuple:
    """(ops, truncated) - summarize the operations under a setup with their folder breadcrumb,
    capped at _MAX_ITEMS. truncated means INCOMPLETE: the cap was hit OR the walk raised.

    Drives the shared _walk_children (what tree_nodes is built on) rather than setup.allOperations:
    allOperations flattens the folder-nested ops and DROPS the folder objects, so the breadcrumb
    every row publishes as 'path' and the folder each row names exist only in the
    container-preserving walk. It calls _walk_children directly rather than tree_nodes because a
    walk that dies part-way has to leave its partial rows behind, which needs the caller to own the
    output list."""
    ops = []
    truncated = False
    root = _setup_node(setup_obj)
    nodes = [root]
    try:
        # _walk_children appends AS it walks, so a walk that dies mid-iteration leaves the nodes it
        # already reached in the list instead of taking them down with it. root is passed as the
        # parent node, exactly as tree_nodes passes it - it is what each row's folder is read from.
        _walk_children(setup_obj, root.name, root.path, nodes, root)
    except Exception:
        truncated = True
    # The completeness check the container-preserving walk needs: allOperations is the setup's own
    # flat count of the SAME operations, so a walk that came back with fewer read short (a
    # collection that stopped answering item(i) yields survivors silently) and its list is
    # INCOMPLETE, never the full read.
    expected = counted(lambda: setup_obj.allOperations.count)
    walked = sum(1 for n in nodes if n.kind == "operation")
    if expected is not None and walked < expected:
        truncated = True
    try:
        for i, node in enumerate(n for n in nodes if n.kind == "operation"):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            # Only real operations have a tool; a container that slipped through is skipped by the
            # cast returning None.
            operation = adsk.cam.Operation.cast(node.obj)
            if not operation:
                continue
            ops.append(_operation_summary(operation, machine_max, node))
    except Exception:
        # A row read that dies left an INCOMPLETE list - flagged, never passed off as the full read.
        truncated = True
    return ops, truncated


def _folder_of(node):
    """The name of the folder/pattern an operation sits IN, or None for an op parked directly under
    the setup. Read from the walk's own PARENT NODE, never by splitting the breadcrumb: a setup or
    folder whose name contains ' / ' would split into a folder that does not exist."""
    if node is None or node.parent is None:
        return None
    return node.parent.name if node.parent.kind in ("folder", "pattern") else None


def _operation_summary(op, machine_max=None, node=None) -> dict:
    tool_desc = None
    try:
        t = op.tool
        if t:
            tool_desc = t.description
    except Exception:
        tool_desc = None

    facts = op_state_facts(op)
    state = facts["operation_state"]
    has_warn = facts["has_warning"]
    has_err = facts["has_error"]
    summary = {
        "name": safe(lambda: op.name),
        "tool": tool_desc,
        "strategy": safe(lambda: op.strategy),
        # ONE state vocabulary for every rollup: this row, the summary tally built from it, and the
        # per-setup op_states all read op_primary_state off the same facts. operationState ALONE is
        # not the roll-up - it reads 0 ('valid') on an op carrying hasError, so a state derived from
        # it alone calls an errored op valid while op_states calls the same op errored.
        "state": op_primary_state(facts),
        "has_toolpath": safe(lambda: op.hasToolpath),
        "toolpath_valid": safe(lambda: op.isToolpathValid),
        "is_generating": safe(lambda: op.isGenerating),
        "is_suppressed": safe(lambda: op.isSuppressed),
    "is_optional": safe(lambda: op.isOptional),
    "has_warning": has_warn,
    "has_error": has_err,
    }
    # Surface the actual message text - a machinist reviewing toolpaths needs the content
    # (e.g. "Spindle speed is larger than supported", "empty toolpath"), not just a bool.
    if has_warn:
        summary["warning"] = (safe(lambda: op.warning) or "").strip()
    # 'out of date' = it has (or should have) a toolpath but that toolpath is not valid,
    # and it isn't intentionally suppressed. This is what generate(skip_valid=true) will redo.
    summary["is_out_of_date"] = bool(
        state in (1, 3) and not summary["is_suppressed"]
    )
    if has_err:
        summary["error"] = safe(lambda: op.error)
    # WHY it's out of date - the invalidation reasons Fusion logged (Design changed: WCS/Fixture/Model,
    # Dependency changed: <op>, Tool, ...). Only when out-of-date; a valid op has none. This is the
    # diagnostic op.warning/op.error DON'T carry (both are empty for a plain invalidation).
    if summary["is_out_of_date"]:
        reasons, param_changes, machine_changed = _invalidation_reasons(op)
        if reasons:
            summary["invalidation_reasons"] = reasons
        if param_changes:
            summary["invalidation_param_changes"] = param_changes
        if machine_changed:
            summary["machine_changed"] = True
    # Structured prerequisites: machine-readable reason codes alongside the prose, so an
    # agent branches without string-matching. present-and-empty when nothing blocks.
    blocked, requires = _op_blocked_by(op, summary)
    summary["blocked_by"] = blocked
    if requires:
        summary["requires"] = requires
    # WHERE it sits: the breadcrumb the shared walk already computed, and the owning folder beside
    # is_suppressed (the folder NAME is where the shop declares why an op is parked).
    if node is not None:
        summary["path"] = node.path
        folder = _folder_of(node)
        if folder:
            summary["folder"] = folder
    # The tool PRESET this op uses - two ops can share one tool and run DIFFERENT presets
    # (measured), which the tool description alone cannot tell apart.
    preset = safe(lambda: op.toolPreset)
    summary["preset"] = safe(lambda preset=preset: preset.name) if preset is not None else None
    # Does the op ask its spindle for more than the machine allows? Both numbers ride along on the
    # rows where the answer is not a plain 'no', so the comparison is checkable, not just asserted.
    # A SUPPRESSED op is excluded from posting, so what it asks for never reaches the machine: its
    # comparison is WITHHELD rather than answered, and the marker says that is why - the same
    # active-ops scoping the readiness rollups apply, in the one place the flag is built.
    if summary["is_suppressed"]:
        summary["spindle_check"] = _SUPPRESSED_NOT_COMPARED
        return summary
    over, requested, marker = spindle_check(op, machine_max)
    summary["spindle_over_machine_max"] = over
    if over is not False:
        if requested is not None:
            summary["spindle_rpm"] = requested
        if machine_max is not None:
            summary["machine_max_rpm"] = machine_max
    if marker:
        summary["spindle_check"] = marker
    return summary

def get_setup_references_handler(setup: str = "") -> dict:
    """Resolve each setup's externally-referenced (X-ref) components to source docs.

    For every model/fixture/stock occurrence in a setup that is an external
    reference, returns its source DataFile id (UID), name, version, and
    fusionWebURL - so the caller can `doc_open` the referenced fixture/part.
    """
    cam, err = get_cam()
    if err:
        return error(err)

    # Two-branch filter: named -> the shared resolver (a duplicated setup name is REFUSED);
    # empty -> every setup via the shared walk.
    want = (setup or "").strip()
    if want:
        node, rerr = resolve_cam_node(cam, want, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        target_setups = [node.obj]
    else:
        target_setups = setups(cam)

    out_setups = []
    try:
        for s in target_setups:
            refs = []
            seen_ids = set()
            refs_truncated = False
            for role, getter in (("model", lambda: s.models),
                                 ("fixture", lambda: s.fixtures),
                                 ("stock", lambda: s.stockSolids)):
                found, role_truncated = _references_in(getter, role)
                refs_truncated = refs_truncated or role_truncated
                for ref in found:
                    key = ref.get("source_id")
                    # De-dupe identical references that appear in multiple roles.
                    if key and key in seen_ids:
                        continue
                    if key:
                        seen_ids.add(key)
                    refs.append(ref)

            out_setups.append({"setup": safe(lambda s=s: s.name), "reference_count": len(refs),
        "references": refs, "references_truncated": refs_truncated})
    except Exception as e:
        return error(f"Could not read setup references: {e}")

    return ok({"setup_count": len(out_setups), "setups": out_setups})


def _references_in(getter, role: str) -> tuple:
    """(found, truncated) - resolved external-reference info for occurrences in one of a setup's
    model/fixture/stock collections, capped at _MAX_ITEMS. Takes the GETTER for the same reason
    _model_names does - the property RAISES on some setups. truncated means INCOMPLETE: the property
    raised, the cap was hit, or the walk raised mid-iteration."""
    found = []
    truncated = False
    collection = safe(getter, _MISSING)
    if collection is _MISSING:
        return found, True
    try:
        for i, item in enumerate(collection):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            occ = adsk.fusion.Occurrence.cast(item)
            if not occ:
                # Not an occurrence (could be a BRepBody/MeshBody) -> no external ref.
                continue
            if not safe(lambda: occ.isReferencedComponent, False):
                continue
            info = {"role": role, "occurrence_name": safe(lambda: occ.name),
    "source_id": None, "source_name": None, "version": None,
    "fusion_web_url": None, "is_out_of_date": None}
            try:
                docref = occ.documentReference
                if docref:
                    df = safe(lambda: docref.dataFile)
                    info["version"] = safe(lambda: docref.version)
                    info["is_out_of_date"] = safe(lambda: docref.isOutOfDate)
                    if df:
                        info["source_id"] = safe(lambda: df.id)
                        info["source_name"] = safe(lambda: df.name)
                        info["fusion_web_url"] = safe(lambda: df.fusionWebURL)
            except Exception:
                pass
            found.append(info)
    except Exception:
        # A reference walk that dies mid-iteration left an INCOMPLETE list - flagged, never
        # passed off as the full read.
        truncated = True
    return found, truncated

def get_tool_list_handler() -> dict:
    """Distinct cutting tools used across the document, with the ops that use each."""
    cam, err = get_cam()
    if err:
        return error(err)

    tools = {}  # description -> {"operations": [...], "setups": set()}
    try:
        for i in range(cam.setups.count):
            s = cam.setups.item(i)
            s_name = safe(lambda: s.name)
            for op in safe(lambda: s.allOperations, []):
                operation = adsk.cam.Operation.cast(op)
                if not operation:
                    continue
                desc = None
                try:
                    t = operation.tool
                    if t:
                        desc = t.description
                except Exception:
                    desc = None
                if not desc:
                    continue
                entry = tools.setdefault(desc, {"operations": [], "setups": set()})
                # Qualify with the setup: the same op NAME can exist in two setups, so a bare-name list
                # reads as a duplicate-bug when it's really one op per setup. "setup / op" disambiguates.
                #
                # BOTH halves go through _segment - the same disclosure every walked breadcrumb takes.
                # A name that did not read would otherwise join as the literal 'None' or, on the
                # unqualified branch, cross the wire as a bare null inside a list of strings while
                # still counting in operation_count. Every row here came out of a setup, so every row
                # is qualified: the marker names the half that did not read instead of dropping the
                # setup and leaving a row that looks like a document with one setup.
                op_name = safe(lambda: operation.name)
                entry["operations"].append(f"{_segment(s_name)} / {_segment(op_name)}")
                if s_name:
                    entry["setups"].add(s_name)
    except Exception as e:
        return error(f"Could not read tools: {e}")

    tool_list = [{
    "tool": desc,
    "operation_count": len(info["operations"]),
    "operations": info["operations"],
    "setups": sorted(info["setups"]),
    } for desc, info in tools.items()]
    # Most-used first.
    tool_list.sort(key=lambda t: t["operation_count"], reverse=True)

    return ok({"distinct_tool_count": len(tool_list), "tools": tool_list})

def _timeable_ops(setup_obj) -> tuple:
    """(ops, suppressed_count) - the setup's operations with the SUPPRESSED ones held back.

    getMachiningTime fails with "Machining time could not be calculated" whenever a suppressed
    operation is inside the target: measured on one job with three targets - the Setup object
    (17 active + 35 suppressed) failed, a collection of 21 valid ops returned 5700.6 s, the same
    21 plus the 65 suppressed ones failed again, and the 21 plus 13 EMPTY-toolpath ops returned
    5700.6 s. So the suppressed ops are what breaks the call and the empty ones are harmless."""
    ops, suppressed = [], 0
    for raw in operations_under(setup_obj):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        if safe(lambda op=op: op.isSuppressed, False):
            suppressed += 1
            continue
        ops.append(op)
    return ops, suppressed


def _any_valid_toolpath(ops) -> bool:
    """True if any op in the list has a valid generated toolpath (the precondition getMachiningTime
    needs; without it the API fails uncatchably)."""
    return any(safe(lambda o=o: o.isToolpathValid, False) for o in ops)


def _op_collection(ops):
    """(collection, added) - an ObjectCollection carrying `ops`, the target getMachiningTime takes
    in place of the Setup object. `added` is how many actually went in (ObjectCollection.add answers
    whether it took the item), so a partial collection is never timed as if it held everything.
    (None, 0) when the collection could not be created."""
    coll = safe(lambda: adsk.core.ObjectCollection.create())
    if coll is None:
        return None, 0
    added = 0
    for op in ops:
        if safe(lambda op=op: coll.add(op), False):
            added += 1
    return coll, added


_TIME_OP_CAP = 200    # one getMachiningTime call per op; bound the per-turn cost on a large job

_TIME_NOTE = (
    "Estimate at 100% feed, ~250 in/min (10.58 cm/s) rapid, 1.5s tool changes. Rapid feed is the "
    "machine's traverse rate, not the cutting feed. SUPPRESSED operations are left out of the "
    "timed collection - the call fails outright when one is in the target (measured) - and "
    "excluded_suppressed counts what each setup left out. Per-operation figures do NOT sum to "
    "their setup total (measured on a 34-operation job: 5445.6 s summed against a 5700.6 s "
    "aggregate, each per-op call reporting 0 tool changes against the aggregate's 17). BOTH "
    "numbers are published per setup, so the gap is visible on THIS job instead of inferred: "
    "machining_time_seconds is the ONE getMachiningTime call over the whole operation collection, "
    "operations_time_sum_seconds is the sum of the per-operation calls that returned a figure, and "
    "operations_time_summed is how many rows that sum covers. So read a per-op number as that "
    "operation's own estimate, not as a decomposition of the setup total, and expect the two "
    "totals to differ. With operations_truncated set the row cap stopped the per-op pass, so the "
    "sum covers only the rows present. A row marked "
    "empty_toolpath carries no time: measured, an operation with no toolpath cannot be timed on "
    "its own, though it is harmless inside the setup's collection, which times fine.")


def _op_time_rows(cam, ops, args, factor) -> tuple:
    """(rows, truncated) - one getMachiningTime call per operation carrying a valid toolpath.
    Distances are cm off MachiningTime and are scaled to the caller's unit.

    An EMPTY-toolpath op is named, not called: measured on a 34-operation job, every one of the 13
    ops reading hasToolpath False raised '3 : Machining time could not be calculated.' on a per-op
    call, while all 21 holding a toolpath returned a time - and the same 13 are harmless inside the
    setup's collection, which timed fine. So the state is reported from the flags instead of from a
    platform error the read can predict."""
    rows = []
    for op in ops:
        facts = op_state_facts(op)
        if not (is_empty_toolpath(facts) or safe(lambda op=op: op.isToolpathValid, False)):
            continue
        if len(rows) >= _TIME_OP_CAP:      # bounds EVERY row, timed or named
            return rows, True
        if is_empty_toolpath(facts):
            rows.append({"operation": facts["name"], "empty_toolpath": True})
            continue
        try:
            mt = cam.getMachiningTime(op, *args)
        except Exception as e:
            rows.append({"operation": safe(lambda op=op: op.name), "error": str(e)})
            continue
        rows.append({"operation": safe(lambda op=op: op.name),
                     "machining_time_seconds": measured(lambda: mt.machiningTime, 1.0, 1),
                     "feed_distance": measured(lambda: mt.feedDistance, factor, 1),
                     "rapid_distance": measured(lambda: mt.rapidDistance, factor, 1)})
    return rows, False


def get_machining_time_handler(setup: str = "", units: str = "mm") -> dict:
    """Estimated machining time for the whole doc, or one setup (`setup`), per setup and per op."""
    cam, err = get_cam()
    if err:
        return error(err)
    unit = (units or "mm").strip().lower()
    factor = CM_TO_UNIT.get(unit)
    if factor is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    # feedScale/rapidFeed/toolChangeTime (API-doc units: percent, cm/s, s) are INERT on Fusion
    # 2704 (measured: cam-machining-time-knobs in tests/live/VERIFIED_API_FACTS.md).
    feed_scale = 100.0          # 100% of programmed feed
    rapid_feed = 10.58          # ~250 in/min = 635 cm/min = 10.58 cm/s
    tool_change = 1.5           # seconds
    args = (feed_scale, rapid_feed, tool_change)

    targets = []  # (label, object)
    if (setup or "").strip():
        node, rerr = resolve_cam_node(cam, setup, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        targets.append((node.name, node.obj))
    else:
        for s in setups(cam):
            targets.append((safe(lambda s=s: s.name), s))

    results = []
    grand = 0.0
    for label, obj in targets:
        ops, suppressed = _timeable_ops(obj)
        # PRECONDITIONS, both measured: getMachiningTime needs at least one VALID toolpath in the
        # target, and the target must hold no SUPPRESSED operation (_timeable_ops holds those back).
        # Inside this handler the failure DOES raise catchably - measured, 13 per-op calls on one
        # job raised '3 : Machining time could not be calculated.' and the call carried on - but
        # through sys_execute_script the same failure took the whole invocation down, so the
        # preconditions are checked BEFORE the call rather than left to the try/except below.
        if not _any_valid_toolpath(ops):
            results.append({"setup": label, "excluded_suppressed": suppressed,
                "error": "No generated toolpath to time - every unsuppressed operation is "
                         "out-of-date or ungenerated. Run cam_generate (in the Manufacture "
                         "workspace), then retry."})
            continue
        collection, added = _op_collection(ops)
        if collection is None or added < len(ops):
            results.append({"setup": label, "excluded_suppressed": suppressed,
                "error": f"Could not build the operation collection to time: {added} of "
                         f"{len(ops)} unsuppressed operations went in."})
            continue
        try:
            mt = cam.getMachiningTime(collection, *args)
            secs = safe(lambda: mt.machiningTime, 0.0) or 0.0
            grand += secs
            rec = {
        "setup": label,
            "machining_time_seconds": round(secs, 1),
            "machining_time_hms": _hms(secs),
            "feed_time_seconds": round(safe(lambda: mt.totalFeedTime, 0.0) or 0.0, 1),
            "rapid_time_seconds": round(safe(lambda: mt.totalRapidTime, 0.0) or 0.0, 1),
            "tool_changes": safe(lambda: mt.toolChangeCount, 0),
            "feed_distance": measured(lambda: mt.feedDistance, factor, 1),
            "rapid_distance": measured(lambda: mt.rapidDistance, factor, 1),
            # What the timed collection HELD, so the number is read against a known set.
            "timed_operations": added,
            "excluded_suppressed": suppressed,
            }
            rows, truncated = _op_time_rows(cam, ops, args, factor)
            rec["operations"] = rows
            # BOTH totals, side by side. The aggregate above is ONE getMachiningTime call over the
            # whole collection; this is the sum of the per-operation calls that returned a figure.
            # They disagree (measured - see _TIME_NOTE), so neither is derived from the other and
            # the count says what the sum actually covers: an empty-toolpath row and a row whose
            # own call errored contribute nothing, and the row cap can stop the pass early.
            timed = [r["machining_time_seconds"] for r in rows
                     if isinstance(r.get("machining_time_seconds"), (int, float))]
            rec["operations_time_sum_seconds"] = round(sum(timed), 1)
            rec["operations_time_summed"] = len(timed)
            if truncated:
                rec["operations_truncated"] = True
            results.append(rec)
        except Exception as e:
            results.append({"setup": label, "excluded_suppressed": suppressed, "error": str(e)})

    return ok({
            "setup_count": len(results),
        "total_machining_time_seconds": round(grand, 1),
        "total_machining_time_hms": _hms(grand),
        "setups": results,
        "units": unit,
    "note": _TIME_NOTE,
    "assumptions": {"feed_scale_percent": feed_scale,
            "rapid_feed_cm_per_s": rapid_feed,
            "tool_change_seconds": tool_change},
    })


def _hms(seconds) -> str:
    try:
        s = int(round(seconds))
    except Exception:
        return "0:00:00"
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"

_NC_PROGRAM_NOTE = (
    "posted_operations is NCProgram.filteredOperations - the operations the program actually posts, "
    "which is a different list from 'operation_count' (NCProgram.operations, measured holding a "
    "single entry, the setup). An operation whose toolpath is EMPTY is in that posted list "
    "(measured: 17 posted operations on a program, empty ones among them), so "
    "empty_toolpath_count is how many would post with nothing to cut and empty_toolpaths names "
    "them, capped. A name several posted operations share is rendered with its POSITION in the "
    "posted list beside it, so no row of that list addresses two operations.")

_NC_EMPTY_NAME_CAP = 20


def _posted_row_label(name, position):
    """What ONE empty posted operation is named by where its name is not its own: the position it
    holds in filteredOperations, stated so the number reads as what it is.

    That position is what this read HOLDS. filteredOperations hands back Operations with no walk
    beside them, and nothing read here attributes one of them to a CamNode, so the 'Setup / op'
    breadcrumb the tree-walking listings are named by is not available here and is not claimed. ''
    where the name did not read - told_apart keeps the row's own name for an empty discriminator."""
    return f"{name} (posted operation {position})" if name else ""


def _program_posted_ops(nc) -> dict:
    """{posted_operations, empty_toolpaths?} for ONE NC program, off filteredOperations - the list
    that expands the program's setup into the operations it really posts. {} when the property does
    not read, so nothing is claimed about a program whose list is unreadable.

    A program's posted list can draw operations from several setups and an operation name is unique
    only within one, so the empty rows are rendered through _common.told_apart: a name only one row
    carries stays that name - the spelling a caller passes to cam_get or cam_generate - and a name
    SEVERAL rows carry is replaced by _posted_row_label."""
    ops = safe(lambda: list(nc.filteredOperations))
    if ops is None:
        return {}
    out = {"posted_operations": len(ops)}
    empty_rows = []
    empty_count = 0
    for position, raw in enumerate(ops[:_MAX_ITEMS], 1):
        op = adsk.cam.Operation.cast(raw)
        if op is None:
            continue
        facts = op_state_facts(op)
        if is_empty_toolpath(facts):
            empty_count += 1
            empty_rows.append((facts["name"], _posted_row_label(facts["name"], position)))
    out["empty_toolpath_count"] = empty_count
    if empty_rows:
        # told_apart judges over EVERY empty row and the cap is applied after, so a listed name
        # whose namesake falls outside the cap is still replaced. The count is the total.
        out["empty_toolpaths"] = told_apart(empty_rows)[:_NC_EMPTY_NAME_CAP]
    return out


def get_nc_programs_handler() -> dict:
    """List the document's NC programs with their reliably-readable details.

    Note: the human "Name / Number / Comment / Output folder" fields seen in the UI
    are NOT exposed as readable post parameters on the NCProgram API (verified live -
    postParameters typically only contains post options like 'metric'). So rather than
    fabricate those fields, we report what IS available: name, machine, post config,
    operation count, and the actual post parameters present (title + expression).
    """
    cam, err = get_cam()
    if err:
        return error(err)

    programs = []
    try:
        ncs = cam.ncPrograms
        for i in range(ncs.count):
            nc = ncs.item(i)
            entry = {
            "name": safe(lambda: nc.name),
            "operation_count": None,
            "machine": machine_label(safe(lambda: nc.machine)),
            "post": safe(lambda: nc.postConfiguration.description) if safe(lambda: nc.postConfiguration) else None,
            "post_parameters": [],
            }
            try:
                entry["operation_count"] = len(nc.operations)
            except Exception:
                pass
            entry.update(_program_posted_ops(nc))
            # Report the actual post parameters as-is (whatever the post exposes).
            params = safe(lambda: nc.postParameters)
            if params is not None:
                try:
                    for j in range(params.count):
                        p = params.item(j)
                        entry["post_parameters"].append({
                        "name": safe(lambda: p.name),
                        "title": safe(lambda: p.title),
                        "expression": safe(lambda: p.expression),
                        })
                except Exception:
                    pass
            programs.append(entry)
    except Exception as e:
        return error(f"Could not read NC programs: {e}")

    return ok({"nc_program_count": len(programs), "nc_programs": programs,
               "note": _NC_PROGRAM_NOTE})

# ---------------------------------------------------------------------------
# Inspection results - the recorded surface-inspection (probing) measurements read by
# cam_get(include=['inspection']).
# ---------------------------------------------------------------------------

# Nothing in the API bounds the point count on a path, so the per-point read is capped.
_INSPECTION_ROW_DEFAULT = 50
_INSPECTION_ROW_CAP = 200

# The actionable states: everything that is not within tolerance. adsk.cam words the two tolerance
# states as POSSIBLY indicating that not enough (above) / too much (below) material was removed, so a
# row reports its state and this code never converts that into a verdict of its own.
_OUT_OF_TOLERANCE = ("above_tolerance", "below_tolerance", "unprojected")

# A document that has never been probed carries this BOTH ways: CAM.inspectionResults reads None on
# some documents and an EMPTY collection on others. Both are a real zero-measure answer, so both are
# published as a state and neither is raised - the None branch takes the note below, the empty
# collection falls through to the ordinary rollup and reports measure_count 0.
_INSPECTION_ABSENT_NOTE = (
    "No inspection results on this document: CAM.inspectionResults reads None, so there is no "
    "results folder to read. Results are recorded by a probing cycle on the machine; nothing in "
    "this server creates them.")

# CAMMeasure exposes inspectionPathResults and nothing else - no name, no operation, no id. The
# collection's itemByName() takes a browser name, but no API call enumerates the legal names, so a
# measure is addressable only by index and no name can be echoed back.
_INSPECTION_UNREADABLE_NOTE = (
    "CAM.inspectionResults could not be read on this document - the property RAISED, and "
    "'read_error' carries the platform text. That is an UNREADABLE state, not an absence of "
    "results: a gated CAM member raises rather than reading empty.")

_INSPECTION_INDEX_NOTE = (
    "Measures are addressed by INDEX: a measure folder exposes no name through the API (its browser "
    "name is not readable, and no call lists the legal names), so no name is echoed back.")


def _read_inspection_results(cam) -> tuple:
    """(collection_or_None, raise_text_or_None). Two DIFFERENT answers have to stay apart: a
    never-probed document reads the property as None or as an empty collection (both are a zero
    answer), and a gated CAM member can RAISE instead of reading empty (measured on
    stockMaterialLibrary). safe()'s single default cannot carry both, so the _MISSING sentinel
    separates them and the platform text is kept."""
    reason = {}

    def read():
        try:
            return cam.inspectionResults
        except Exception as exc:
            reason["text"] = str(exc).strip() or repr(exc)
            raise

    results = safe(read, _MISSING)
    if results is _MISSING:
        return None, reason.get("text") or "the property read raised."
    return results, None


def _point_state_map() -> dict:
    """InspectionPointState value -> wire name. The enum's int values are not documented, so the map
    is keyed off the live members and a value it does not carry degrades to str() (the same
    defensive shape as _operation_type_name) rather than guessing."""
    return {getattr(adsk.cam.InspectionPointState, member, object()): name for member, name in (
        ("WithinTolerance", "within_tolerance"), ("AboveTolerance", "above_tolerance"),
        ("BelowTolerance", "below_tolerance"), ("Unprojected", "unprojected"))}


def _point_state_name(value, state_names) -> str:
    """The wire name for one point's state: 'unknown' when the state cannot be read, str(value) for a
    member this build does not name. Only a NAMED out-of-tolerance state is counted as one."""
    if value is None:
        return "unknown"
    return state_names.get(value, str(value))


def _xyz(pt, f):
    """[x, y, z] for a Point3D/Vector3D, scaled out of Fusion's internal CM (InspectionPointResult:
    "All values are in the Fusion's internal units which for positional and length values is CM").
    None when the point/vector itself is absent."""
    if pt is None:
        return None
    return [measured(lambda: pt.x, f), measured(lambda: pt.y, f), measured(lambda: pt.z, f)]


def _point_row(p, path_index, point_index, state, f) -> dict:
    """One measured point. Lengths go through measured(), not safe(read, 0.0): a deviation of 0.0 is
    an ANSWER ("dead on nominal"), so an unreadable field must read null instead of masquerading
    as one."""
    return {"path": path_index, "index": point_index, "state": state,
            "deviation": measured(lambda: p.deviation, f),
            "error": measured(lambda: p.error, f),
            "offset": measured(lambda: p.offset, f),
            "nominal": _xyz(safe(lambda: p.nominalPosition), f),
            "contact": _xyz(safe(lambda: p.contact), f),
            "projected": _xyz(safe(lambda: p.projectedPoint), f),
            "delta": _xyz(safe(lambda: p.delta), f)}


def _measure_paths(m) -> list:
    """One measure's InspectionPathResults as a list. CAMMeasure.inspectionPathResults is documented
    to return null when the measure holds none, so an absent collection reads as zero paths."""
    return list(iter_collection(safe(lambda: m.inspectionPathResults)))


def _measure_rollup(m, index, f, state_names) -> dict:
    """ONE measure's rollup: the per-state tally (terse - zero buckets dropped, so a clean measure
    reads {'within_tolerance': N}), the actionable out_of_tolerance count, and the worst (highest
    error) out-of-tolerance point. Exception-first: a clean measure carries no 'worst'."""
    tally = {}
    total = 0
    oot = 0
    worst = None
    worst_rank = None
    paths = _measure_paths(m)
    for pi, path in enumerate(paths):
        for qi, p in enumerate(iter_collection(safe(lambda path=path: path.pointResults))):
            total += 1
            state = _point_state_name(safe(lambda p=p: p.state), state_names)
            tally[state] = tally.get(state, 0) + 1
            if state not in _OUT_OF_TOLERANCE:
                continue
            oot += 1
            err = measured(lambda p=p: p.error, f)
            # Rank by MAGNITUDE: the identity ranking if error is unsigned, and the right one if it
            # is signed (a below-tolerance point would otherwise sort under every above-tolerance
            # one). The row still publishes the raw value, sign included.
            rank = None if err is None else abs(err)
            if worst is None or (rank is not None and (worst_rank is None or rank > worst_rank)):
                worst = {"path": pi, "point": qi, "state": state,
                         "deviation": measured(lambda p=p: p.deviation, f), "error": err}
                worst_rank = rank
    row = {"index": index, "path_count": len(paths), "point_count": total,
           "out_of_tolerance": oot}
    if tally:
        row["states"] = tally
    if worst:
        row["worst"] = worst
    return row


def _measure_points(paths, path_filter, f, state_names, cap) -> tuple:
    """(rows, points_in_scope, out_of_tolerance_in_scope, truncated) for one measure's paths, or one
    of them (path_filter). Only out-of-tolerance points become rows - the narrowing that keeps the
    deep read bounded however many points the path carries."""
    rows = []
    total = 0
    oot = 0
    truncated = False
    for pi, path in enumerate(paths):
        if path_filter is not None and pi != path_filter:
            continue
        for qi, p in enumerate(iter_collection(safe(lambda path=path: path.pointResults))):
            total += 1
            state = _point_state_name(safe(lambda p=p: p.state), state_names)
            if state not in _OUT_OF_TOLERANCE:
                continue
            oot += 1
            if len(rows) >= cap:
                truncated = True
                continue
            rows.append(_point_row(p, pi, qi, state, f))
    return rows, total, oot, truncated


def _parse_measure_scope(raw) -> tuple:
    """'<measure>' or '<measure>/<path>' -> (measure_index, path_index_or_None, None); a value that
    is neither -> (None, None, reason naming it)."""
    parts = [s.strip() for s in str(raw).split("/")]
    if len(parts) > 2:
        return None, None, (f"'measure' takes '<measure index>' or '<measure index>/<path index>' - "
                            f"'{raw}' has {len(parts)} parts.")
    idx = []
    for part in parts:
        if not part.isdigit():
            return None, None, (f"'measure': '{part}' is not a non-negative index. A measure folder "
                                "has no API-readable name, so the scope is '<measure index>' or "
                                "'<measure index>/<path index>'.")
        idx.append(int(part))
    return idx[0], (idx[1] if len(idx) == 2 else None), None


def _row_cap(max_results) -> int:
    return clamp_rows(max_results, _INSPECTION_ROW_DEFAULT, _INSPECTION_ROW_CAP)


def get_inspection_results_handler(measure: str = "", max_results: int = 0,
                                   units: str = "mm") -> dict:
    """The recorded probing results: a per-measure state rollup by default, or one measure's (or one
    path's) out-of-tolerance points when 'measure' scopes it. Lengths are scaled out of CM."""
    cam, err = get_cam()
    if err:
        return error(err)
    unit = (units or "mm").strip().lower()
    f = CM_TO_UNIT.get(unit)
    if f is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    results, read_error = _read_inspection_results(cam)
    if read_error is not None:
        return ok({"available": False, "readable": False, "measure_count": 0, "measures": [],
                   "units": unit, "read_error": read_error, "note": _INSPECTION_UNREADABLE_NOTE})
    if results is None:
        return ok({"available": False, "readable": True, "measure_count": 0, "measures": [],
                   "units": unit, "note": _INSPECTION_ABSENT_NOTE})
    count = safe(lambda: results.count, 0) or 0
    state_names = _point_state_map()
    scope = (measure or "").strip()

    if not scope:
        rows = []
        truncated = False
        for i, m in enumerate(iter_collection(results)):
            if i >= _MAX_ITEMS:
                truncated = True
                break
            rows.append(_measure_rollup(m, i, f, state_names))
        note = _INSPECTION_INDEX_NOTE + (
            " Pass measure='<index>' (or '<index>/<path>') for that measure's out-of-tolerance "
            "points." if count else
            " The results collection is present but holds no measures.")
        return ok({"available": True, "measure_count": count, "measures": rows,
                   "measures_truncated": truncated, "units": unit, "note": note})

    mi, pi, perr = _parse_measure_scope(scope)
    if perr:
        return error(perr)
    if mi >= count:
        return error(f"measure index {mi} is out of range - this document holds {count} measure(s)"
                     + (f" (index 0 to {count - 1})." if count else ".") + " " +
                     _INSPECTION_INDEX_NOTE)
    m = safe(lambda: results.item(mi))
    if m is None:
        return error(f"measure index {mi} did not resolve to a measure folder.")
    paths = _measure_paths(m)
    if pi is not None and pi >= len(paths):
        return error(f"path index {pi} is out of range - measure {mi} holds {len(paths)} path(s)"
                     + (f" (index 0 to {len(paths) - 1})." if paths else "."))

    rows, total, oot, truncated = _measure_points(paths, pi, f, state_names, _row_cap(max_results))
    out = {"available": True, "measure": mi, "path_count": len(paths), "point_count": total,
           "out_of_tolerance": oot, "filter": "out_of_tolerance", "returned": len(rows),
           "truncated": truncated, "points": rows, "units": unit,
           "note": ("Out-of-tolerance points only (above/below tolerance and unprojected); "
                    "within-tolerance points are counted, not listed. " + _INSPECTION_INDEX_NOTE)}
    if pi is not None:
        out["path"] = pi
    return ok(out)


# ---------------------------------------------------------------------------
# Async generation registry - where every launch path parks its live GenerateToolpathFuture.
# ---------------------------------------------------------------------------

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
    falls back to the Future alone rather than the per-op tallies it could read. The key answers for
    a never-saved document too (a per-instance token matched by document handle). doc_name /
    doc_urn stay beside it because the payload NAMES the generating document from them. All three
    launch sites register here, so all three are bound the same way.

    target_name is the RAW setup/folder/operation name a scoped launch resolved to (omit it for a
    whole-document launch): a status read settles this handle's completion on THAT target's own
    operations, so a second generation running beside it cannot keep this handle incomplete.

    'doc' is the launch document itself, kept beside that key because the key is derived from what
    reads on the document and the DOCUMENT outlives the derivation. document_key prefers a readable
    data-file id, and that id does not arrive settled: dataFile.id may answer a path-form string
    before the lineage urn resolves, so ONE launch document saved mid-generation can answer a
    different key MORE THAN ONCE - the minted per-instance token, then whatever the id reads first,
    then the urn. Each of those flips is announced (_carry_generation_keys below re-stamps this
    entry), and through every one of them the same open document still compares equal by HANDLE -
    the comparison document_key already matches a never-saved document on - so _same_document
    answers on the re-stamped key from the call after a flip, and on the handle during the call
    that detected it.

    PROBE NEEDED (KEY-2): the transient path-form id is stated here as the MECHANISM this fallback
    covers, not as a ledger fact - no measure_api row records it yet."""
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


# ---------------------------------------------------------------------------
# Machine library - the catalog cam_get publishes and the resolver an assignment runs through.
# ---------------------------------------------------------------------------

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
        # claimed - MachineAxis documents cm for a linear axis and radians for a rotary one.
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


_MACHINE_SLICE_NOTE = (
    "Spindle and axis limits come from the machine's kinematics parts (Machine.elements -> the "
    "kinematics element -> parts). A setup parameter named machine_dimension_x/y/z is a different "
    "number - measured -1 on a job whose axes read 762/406/508 mm - so it is never read as a "
    "travel. A tool-station or spindle field reading 0 is left out rather than published as a "
    "limit of 0.")


def get_machine_limits_handler(setup: str = "", units: str = "mm") -> dict:
    """Per setup: the assigned machine's spindle speed range and per-axis travels."""
    cam, err = get_cam()
    if err:
        return error(err)
    unit = (units or "mm").strip().lower()
    factor = CM_TO_UNIT.get(unit)
    if factor is None:
        return error(f"Unknown units '{units}'. Valid: mm, cm, in.")

    want = (setup or "").strip()
    if want:
        node, rerr = resolve_cam_node(cam, want, kinds=("setup",), label="setup")
        if rerr:
            return error(rerr)
        targets = [(node.name, node.obj)]
    else:
        targets = [(safe(lambda s=s: s.name), s) for s in setups(cam)]

    rows = []
    for label, s in targets:
        m = safe(lambda s=s: s.machine)
        rec = {"setup": label, "machine": machine_label(m)}
        if m is None:
            rec["kinematics_readable"] = False
            rec["blocked_by"] = setup_blockers(s)   # the shared code vocabulary, minted once
        else:
            rec.update(machine_limits(m, factor, unit))
        rows.append(rec)
    return ok({"setup_count": len(rows), "setups": rows, "units": unit,
               "note": _MACHINE_SLICE_NOTE})


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


# ---------------------------------------------------------------------------
# CAM LIBRARY folder walks - the ONE traversal the tool / post / template libraries share.
# All three expose the same shape off a LibraryLocations root url: childFolderURLs nests, and the
# leaves are read per library kind (childAssetURLs for a tool library or a post config, childTemplates
# for a template). Cloud/Hub locations NEST and are network-slow, and an unbounded enumeration of a
# cloud tree is a measured way to hang the add-in, so the walk is bounded on BOTH axes here and each
# site supplies only its own leaf op.
# ---------------------------------------------------------------------------

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
