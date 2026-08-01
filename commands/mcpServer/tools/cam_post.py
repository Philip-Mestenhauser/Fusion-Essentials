# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Create-or-reuse an NC Program for the chosen scope, then post it to a G-code / NC file on disk
(cam_post). The NC Program is the document's persistent post deliverable (what live_readiness health
and cam_get(include=['nc_programs']) report), so posting goes through ncPrograms.add/postProcess, not
a fire-and-forget direct post. Success is gated on the OUTPUT FILE landing on disk - postProcess
returning true is not proof a file was written - so the handler diffs the output folder and reports
the files that actually appeared. The output extension is decided by the post config (.cps), so files
are matched by newness, not a fixed extension."""

import glob
import os
import tempfile
import time

import adsk.core
import adsk.cam

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _assert
from . import _inputs
from . import _outputs
from ._cam_common import get_cam, live_readiness, resolve_cam_node, setups as cam_setups, operations_under
from ._export import verify_written   # the file-landed proof (postProcess() true != a file)

app = adsk.core.Application.get()

# What cam_post RETURNS: the written NC file path(s) on disk - the deliverable the whole CAM job exists
# to produce.
RETURNS = [
    _outputs.ReturnsValue("file_path", "an NC/G-code file written to disk", in_list=True),
]

_UNITS_CHOICE = _inputs.Choice("units", options=["document", "inch", "mm"], default="document",
                               description="Output units for the NC file (default: document units).")

# NC-program output parameters live on the CAMParameters collection of the NCProgramInput/NCProgram
# (the UI's Name / Comment / Output-folder fields), distinct from the .cps post OPTIONS on
# .postParameters. Name and Comment are confirmed live; the folder/editor/unit names are the
# established Fusion internal names, and every set is read back so the actual applied value is
# reported rather than assumed.
_P_NAME = "nc_program_name"
_P_COMMENT = "nc_program_comment"
_P_FOLDER = "nc_program_output_folder"
_P_OPEN_EDITOR = "nc_program_openInEditor"
_P_UNIT = "nc_program_unit"
_MISSING = object()   # sentinel: the parameter is not present on this program


# Cloud/Hub post scope -> the LibraryLocations enum member that names its root. The team library is
# network-slow and NESTED in folders (a flat listing is empty), so the walk is bounded on both depth
# and node count.
_POST_LOCATIONS = {"cloud": "CloudLibraryLocation", "hub": "HubLibraryLocation"}
_CLOUD_MAX_DEPTH = 4
_CLOUD_MAX_NODES = 400

_POST_SCOPE_CHOICE = _inputs.Choice(
    "post_scope", options=["local", "cloud", "hub"], default="local",
    description="Where to resolve 'post' from: local .cps folder (default) | cloud | hub team post "
                "library. cloud/hub reach deployed team posts and are network-slow.")


def _post_library():
    """The shared PostLibrary - on CAMManager.get().libraryManager.postLibrary (parallel to the tool
    libraries; NOT the document's CAM product). Patched in tests."""
    return safe(lambda: adsk.cam.CAMManager.get().libraryManager.postLibrary)


def _resolve_local_cps(cam, post):
    """Resolve a LOCAL 'post' to a full .cps path that exists on disk. Accepts a full path to a .cps
    file, or a bare name resolved against the personal then the installed (generic) post folder.
    Returns (path, None) or (None, error)."""
    post = post.replace("\\", "/")
    stem = post if post.lower().endswith(".cps") else post + ".cps"
    personal = safe(lambda: cam.personalPostFolder)
    generic = safe(lambda: cam.genericPostFolder)
    candidates = []
    if os.path.isabs(post) or "/" in post:
        candidates.append(stem)                       # treat as a path as given
    base = os.path.basename(stem)
    for folder in (personal, generic):
        if folder:
            candidates.append(folder.replace("\\", "/").rstrip("/") + "/" + base)
    for c in candidates:
        if safe(lambda c=c: os.path.isfile(c), False):
            return c, None
    return None, (f"Post config not found for '{post}'. Tried: {', '.join(candidates) or '(none)'}. "
                  f"Provide a full .cps path, or a post name in the personal ({personal}) or installed "
                  f"({generic}) post folder. For a deployed team post use post_scope=cloud/hub.")


def _walk_post_assets(lib, root):
    """(assets, truncated): every post-asset URL under root, recursing folders BOUNDED (depth and node
    caps - Cloud/Hub is NESTED and network-slow). childAssetURLs yields the post URLs (each exposing
    .leafName = the post name and .toString() = the full url); childPostConfigurations would load the
    heavy PostConfiguration objects, which carry no name/url, so the asset URLs are what we match on."""
    assets = []
    truncated = [False]
    seen = [0]

    def walk(url, depth):
        if url is None or depth > _CLOUD_MAX_DEPTH:
            return
        if seen[0] >= _CLOUD_MAX_NODES:
            truncated[0] = True
            return
        for a in (safe(lambda: list(lib.childAssetURLs(url)), []) or []):
            if seen[0] >= _CLOUD_MAX_NODES:
                truncated[0] = True
                return
            assets.append(a)
            seen[0] += 1
        for f in (safe(lambda: list(lib.childFolderURLs(url)), []) or []):
            walk(f, depth + 1)

    walk(root, 0)
    return assets, truncated[0]


def _norm_post_name(name):
    """Case-fold a post name and drop a trailing .cps so 'Generic Fanuc' matches 'generic fanuc.cps'."""
    low = (name or "").strip().lower()
    return low[:-4] if low.endswith(".cps") else low


def _resolve_cloud_post(post, post_scope):
    """Resolve a CLOUD/HUB 'post' to a PostConfiguration by matching the post NAME (case-insensitive)
    against the asset URLs' leafName under the scope root, then loading it via postConfigurationAtURL.
    Refuses an ambiguous name with the candidate urls. Returns (post_config, label, None) or
    (None, None, error). Network-slow. Patched in tests."""
    lib = _post_library()
    if not lib:
        return None, None, "Post library unavailable (CAMManager.get().libraryManager.postLibrary)."
    loc = getattr(adsk.cam.LibraryLocations, _POST_LOCATIONS[post_scope], None)
    root = safe(lambda: lib.urlByLocation(loc)) if loc is not None else None
    if root is None:
        return None, None, f"Could not resolve the {post_scope} post-library root URL."
    assets, truncated = _walk_post_assets(lib, root)
    want = _norm_post_name(post)

    def leaf(a):
        return safe(lambda a=a: a.leafName) or ""

    matches = [a for a in assets if _norm_post_name(leaf(a)) == want]
    if not matches:
        names = sorted({leaf(a) for a in assets if leaf(a)})[:30]
        hint = (f" Available posts: {', '.join(names)}." if names
                else " No posts were found in this library.")
        trunc = " (search was capped - more posts may exist deeper)" if truncated else ""
        return None, None, f"No {post_scope} post named '{post}'.{hint}{trunc}"
    if len(matches) > 1:
        urls = [safe(lambda a=a: a.toString()) or leaf(a) for a in matches]
        return None, None, (f"Ambiguous {post_scope} post '{post}' - {len(matches)} match: "
                            f"{', '.join(str(u) for u in urls)}. Pass a more specific name or the url.")
    url = matches[0]
    pc = safe(lambda: lib.postConfigurationAtURL(url))
    if pc is None:
        return None, None, (f"The {post_scope} post '{post}' matched a url but postConfigurationAtURL "
                            f"returned null ({safe(lambda: url.toString())}).")
    label = safe(lambda: url.toString()) or leaf(url)
    return pc, label, None


def _resolve_post_config(cam, post, post_scope):
    """Resolve 'post' to a READY PostConfiguration for the chosen scope, plus a human label. Returns
    (post_config, label, None) or (None, None, error).
    - local (default): a full .cps path or a bare name in the personal/installed post folder; the
      file's CONTENT is loaded via PostConfiguration.createFromContent. label = the .cps path.
    - cloud/hub: the team post library (network-slow); match the post NAME on the asset url leafName
      and load via postConfigurationAtURL - no createFromContent round-trip. label = the post url.
    The handler receives a PostConfiguration ready to assign to program.postConfiguration either way."""
    post = (post or "").strip()
    if not post:
        return None, None, ("Provide 'post' - the post processor: for post_scope=local a full .cps path "
                            "or a name in the personal/installed post folder; for cloud/hub the NAME of "
                            "a post in the team library.")
    if post_scope in _POST_LOCATIONS:
        return _resolve_cloud_post(post, post_scope)
    path, perr = _resolve_local_cps(cam, post)
    if perr:
        return None, None, perr
    pc, lerr = _load_post_configuration(path)
    if lerr:
        return None, None, lerr
    return pc, path, None


def _load_post_configuration(cps_path):
    """Build a PostConfiguration from a .cps file's content. Returns (post_config, None) or
    (None, error). createFromContent takes the file CONTENT string, not a path."""
    try:
        with open(cps_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return None, f"Could not read the post config '{cps_path}': {e}"
    try:
        pc = adsk.cam.PostConfiguration.createFromContent(content)
    except Exception as e:
        return None, f"Could not load the post config '{cps_path}': {e}"
    if pc is None:
        return None, f"The post config '{cps_path}' did not load (createFromContent returned null)."
    return pc, None


def _dir_snapshot(folder):
    """{file_path: mtime} for the regular files currently in folder (empty if it does not exist yet)."""
    snap = {}
    if safe(lambda: os.path.isdir(folder), False):
        for n in safe(lambda: os.listdir(folder), []) or []:
            p = os.path.join(folder, n)
            if safe(lambda p=p: os.path.isfile(p), False):
                snap[p] = safe(lambda p=p: os.path.getmtime(p), 0.0)
    return snap


def _new_or_changed(folder, before):
    """Sorted paths in folder that are new since 'before' or whose mtime advanced - the files this post
    actually wrote."""
    out = []
    if not safe(lambda: os.path.isdir(folder), False):
        return out
    for n in sorted(safe(lambda: os.listdir(folder), []) or []):
        p = os.path.join(folder, n)
        if not safe(lambda p=p: os.path.isfile(p), False):
            continue
        m = safe(lambda p=p: os.path.getmtime(p), 0.0)
        if p not in before or m > before[p]:
            out.append(p)
    return out


def _unquote(expr):
    if expr is None:
        return None
    s = str(expr)
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _set_str_param(params, name, value):
    """Set a string CAMParameter by name and read it back. Returns the read-back value, or _MISSING if
    the parameter is not present on this program."""
    p = safe(lambda: params.itemByName(name))
    if p is None:
        return _MISSING
    quoted = "'" + str(value).replace("'", "\\'") + "'"
    try:
        p.expression = quoted
    except Exception as e:
        return f"<error: {e}>"
    return _unquote(safe(lambda: p.expression))


def _set_value_param(params, name, value):
    """Set a value-backed CAMParameter (bool/choice) by name and read it back. Returns the read-back
    value, or _MISSING if the parameter is not present on this program."""
    p = safe(lambda: params.itemByName(name))
    if p is None:
        return _MISSING
    try:
        p.value.value = value
    except Exception as e:
        return f"<error: {e}>"
    return safe(lambda: p.value.value)


def _set_unit_param(params, units_key):
    """Set the NC-program output-unit ChoiceParameter to `units_key` and read it back. Returns
    (applied_value, note): applied_value is _MISSING when the program has no unit parameter, the
    read-back value on success, or an '<error: ...>' string on failure; note is None on success or a
    human explanation when the units did NOT apply.

    A ChoiceParameterValue rejects a bare int index (live: the set raises a std::string type error),
    and it has NO '.choices' property - the legal values come from getChoices(), which returns
    (ok, names, values) as SWIG out-params (live API doc). We pick the value whose NAME carries the
    requested unit word (the closed 3-entry platform list), set it, and read back - so a failure
    surfaces as a partial note instead of a silently-wrong-units file."""
    p = safe(lambda: params.itemByName(_P_UNIT))
    if p is None:
        return _MISSING, None
    cval = safe(lambda: p.value)
    got = safe(lambda: cval.getChoices()) if cval is not None else None
    try:
        names, values = (list(got[1]), list(got[2])) if got and got[0] else ([], [])
        token = {"inch": "inch", "mm": "milli", "document": "document"}[units_key]
        hits = [v for n, v in zip(names, values) if token in str(n).lower()]
        if len(hits) != 1:
            raise ValueError(
                f"no unique '{token}' entry in the program's unit choices {names or '(none exposed)'}")
        cval.value = hits[0]
    except Exception as e:
        note = (f"Output units could not be set to '{units_key}' ({e}) - the NC file posts in the "
                "program's current units. Set units in the post config / Fusion UI, or omit 'units'.")
        return f"<error: {e}>", note
    return safe(lambda: cval.value), None


def _stored_str_param(params, name):
    """Read-only companion to _set_str_param: the CURRENT expression of a string CAMParameter, or None
    when absent - lets as-is posting target the program's STORED output folder without writing
    anything (as-is skips every set_*_param call)."""
    p = safe(lambda: params.itemByName(name))
    if p is None:
        return None
    return _unquote(safe(lambda: p.expression))


def _apply_output_params(params, program_name, out_dir, comment, units_key):
    """Apply the NC-program output parameters onto a CAMParameters collection (an NCProgramInput's or an
    existing NCProgram's). Returns (applied, unresolved, unit_note): applied is {param: read_back_value},
    unresolved is the list of expected params this program did not expose, unit_note is a human string
    when the output units failed to apply (else None) - so a units failure is surfaced, never swallowed."""
    applied = {}
    unresolved = []

    def record(name, result):
        if result is _MISSING:
            unresolved.append(name)
        else:
            applied[name] = result

    record(_P_NAME, _set_str_param(params, _P_NAME, str(program_name).strip()))
    # Point the program at the folder the file-landed gate watches (forward slashes: robust in the
    # Fusion expression parser on every platform).
    record(_P_FOLDER, _set_str_param(params, _P_FOLDER, out_dir.replace("\\", "/")))
    if (comment or "").strip():
        record(_P_COMMENT, _set_str_param(params, _P_COMMENT, comment.strip()))
    record(_P_OPEN_EDITOR, _set_value_param(params, _P_OPEN_EDITOR, False))   # headless: never pop the editor
    unit_note = None
    if units_key != "document":
        uval, unit_note = _set_unit_param(params, units_key)
        record(_P_UNIT, uval)
    return applied, unresolved, unit_note


# Fusion writes the DETAILED post error to <TEMP>/Fusion360CAM/<session>/<n>/<program>.log - NOT the
# output folder (there it leaves only a '.failed' stub that says "See log for details"). So on a failed
# post, the actionable error ("Program number 'NaN' is out of range...") is only in that log.
_CAM_LOG_ROOT = os.path.join(tempfile.gettempdir(), "Fusion360CAM")


def _is_failure_marker(path):
    """A '.failed' file (e.g. '1001.nc.failed') is a POST-FAILED stub, not a deliverable NC file."""
    return path.lower().endswith(".failed")


def _post_log_errors(program_name, since):
    """Best-effort Error/Warning lines from THIS post's log, so a failure is actionable, not a guess.
    Finds the newest <program>.log under the Fusion CAM temp tree touched since the post started."""
    base = os.path.basename(str(program_name).strip())
    if not base:
        return []
    try:
        matches = glob.glob(os.path.join(_CAM_LOG_ROOT, "**", base + ".log"), recursive=True)
    except Exception:
        return []
    recent = [(m, p) for (m, p) in
              ((safe(lambda p=p: os.path.getmtime(p), 0.0), p) for p in matches) if m >= since - 2.0]
    if not recent:
        return []
    _, newest = max(recent)
    lines = []
    try:
        with open(newest, encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                s = raw.strip()
                if s.lower().startswith(("error", "warning")) and s not in lines:
                    lines.append(s)
                    if len(lines) >= 15:
                        break
    except Exception:
        return []
    return lines


def _operations_collection(cam, target):
    """The list of things to post: the single resolved target, or every setup for the whole document.
    NCProgramInput/NCProgram.operations is a vector<OperationBase> setter - it takes a plain Python
    LIST of setups/folders/operations (NOT an ObjectCollection) and expands children."""
    return [target] if target is not None else cam_setups(cam)


def _expand_ops(items):
    """Real terminal Operation objects for any item NCProgram.operations may hold: a Setup/CAMFolder/
    CAMPattern (expanded through operations_under - the same expansion the setter itself applies) or
    an already-bare Operation (included as-is). Lets the OVERWRITE GUARD compare the program's STORED
    operations against a REQUESTED scope on the same flattened shape, regardless of which form each
    side is in."""
    out = []
    for it in (items or []):
        has_children = (safe(lambda it=it: it.operations) is not None
                        or safe(lambda it=it: it.allOperations) is not None)
        if has_children:
            out.extend(operations_under(it))
        else:
            out.append(it)
    return out


def _op_token_set(ops):
    """Stable per-operation identity for a stored-vs-requested comparison: entityToken, falling back
    to id() (the entityToken-or-id() convention used across the tool surface, e.g. model_extrude,
    surface_edit) for objects that expose no entityToken."""
    return {(safe(lambda o=o: o.entityToken) or id(o)) for o in (ops or [])}


def handler(scope: str = "", post: str = "", post_scope: str = "local", output_folder: str = "",
            program_name: str = "", units: str = "document", program_comment: str = "",
            overwrite: bool = False) -> dict:
    """See TOOL_DESCRIPTION."""
    cam, cerr = get_cam()
    if cerr:
        return error(cerr)

    if not (program_name or "").strip():
        return error("Provide 'program_name' - the NC Program name or number (some posts require a number).")
    prog_name = str(program_name).strip()

    # Reuse-or-create. Looked up FIRST (before the configuration guards below) so as-is mode can skip
    # them entirely for an EXISTING program called with no configuration knobs.
    existing = safe(lambda: cam.ncPrograms.itemByName(prog_name))
    reused = existing is not None
    as_is = (reused and not (scope or "").strip() and not (post or "").strip()
            and not (output_folder or "").strip())

    units_key, uerr = _UNITS_CHOICE.resolve(units)
    if uerr:
        return error(uerr)

    post_scope_key, pserr = _POST_SCOPE_CHOICE.resolve(post_scope)
    if pserr:
        return error(pserr)

    if not as_is and not (output_folder or "").strip():
        return error("Provide 'output_folder' - the directory where the NC file(s) will be written.")

    # Resolve the scope up front so a bad name fails before we touch the NC Program. Stays None/
    # "document" for as-is (scope is guaranteed blank by the as_is check above).
    target, kind = (None, "document")
    want = (scope or "").strip()
    if want and want.lower() not in ("all", "document", "*"):
        node, serr = resolve_cam_node(cam, want, kinds=("setup", "folder", "operation"),
                                      label="setup/folder/operation")
        if serr:
            return error(serr + " Omit 'scope' to post the whole document.")
        target, kind = node.obj, node.kind

    # Up-front refusal: nothing valid to post. live_readiness is the one CAM health signal; the post
    # omits invalid/empty operations, so zero valid ops -> no file. Applies to as-is too - a stale
    # program still writes wrong G-code.
    live, lerr = live_readiness()
    if lerr:
        return error(lerr)
    live = live or {}
    if not live.get("valid"):
        return error("No valid toolpaths to post - every operation is out-of-date, errored, or "
                     "ungenerated. Run cam_generate (in the Manufacture workspace) first. "
                     f"({live.get('readiness', '')})")

    # OVERWRITE GUARD: reconfiguring (scope/post/output_folder given) an EXISTING program whose STORED
    # operations differ from the REQUESTED scope would silently clobber a program that may be
    # machinist-curated. A program with no stored operations yet has nothing to protect. Identical sets
    # (the common re-post case) proceed with no friction.
    if reused and not as_is:
        requested_ops = _expand_ops(_operations_collection(cam, target))
        stored_tokens = _op_token_set(_expand_ops(safe(lambda: list(existing.operations)) or []))
        if stored_tokens and stored_tokens != _op_token_set(requested_ops) and not overwrite:
            return error(
                f"NC Program '{prog_name}' already exists and its stored operations differ from what "
                "'scope' resolves to - reconfiguring would overwrite a program that may be machinist-"
                "curated. Omit 'scope', 'post', and 'output_folder' to post it exactly as stored, or "
                "pass overwrite=true to reconfigure it.")

    if as_is:
        # Post the program EXACTLY as stored: no operations/postConfiguration/parameter writes - just
        # postProcess() against its own configuration.
        program = existing
        out_dir = _stored_str_param(program.parameters, _P_FOLDER)
        if not out_dir:
            return error(f"NC Program '{prog_name}' has no stored '{_P_FOLDER}' output folder to post "
                         "as-is against - configure it once with 'output_folder' and 'post'.")
        post_label = (safe(lambda: program.postConfiguration.description)
                     if safe(lambda: program.postConfiguration) else None)
        applied, unresolved, unit_note = {}, [], None
    else:
        # Resolve the post AFTER the cheap guards - a cloud/hub lookup is network-slow, so a bad
        # output_folder/program_name/scope or an empty document fails fast without touching the network.
        post_config, post_label, perr = _resolve_post_config(cam, post, post_scope_key)
        if perr:
            return error(perr)

        out_dir = os.path.abspath(output_folder.strip())

        # Prefer UPDATE IN PLACE over delete+recreate: NCProgram.operations, .postConfiguration and its
        # output parameters are all settable, so an in-place update keeps the program's identity
        # (operationId, attributes, browser position) and avoids ever orphaning it.
        try:
            if reused:
                program = existing
                program.operations = _operations_collection(cam, target)
                program.postConfiguration = post_config
                applied, unresolved, unit_note = _apply_output_params(program.parameters, prog_name,
                                                                      out_dir, program_comment, units_key)
            else:
                nc_input = cam.ncPrograms.createInput()
                nc_input.displayName = prog_name
                nc_input.operations = _operations_collection(cam, target)
                applied, unresolved, unit_note = _apply_output_params(nc_input.parameters, prog_name,
                                                                      out_dir, program_comment, units_key)
                program = cam.ncPrograms.add(nc_input)
                program.postConfiguration = post_config
        except Exception as e:
            return error(f"Could not {'update' if reused else 'create'} the NC Program '{prog_name}': {e}. "
                         f"(Post config: {post_label}.)")

        if program is None:
            return error(f"NC Program '{prog_name}' was not {'updated' if reused else 'created'} "
                         "(the API returned null).")

        if unresolved and _P_FOLDER in unresolved:
            # Without the output-folder parameter the file lands at the program default, not out_dir, and
            # the file-landed gate below can't see it - fail loudly and name the missing parameter.
            if not reused:
                safe(lambda: program.deleteMe())
            return error(f"The NC Program has no '{_P_FOLDER}' parameter, so the output folder could not be "
                         f"set to '{out_dir}'. Unresolved parameters: {', '.join(unresolved)}.")

    before = _dir_snapshot(out_dir)
    started = time.time()

    try:
        # postProcess(None) RAISES "Options must not be null" (live-verified, Fusion 2704.1.39) - the
        # API docstring's "Can be null" does not hold in this build; always pass a real options object.
        options = adsk.cam.NCProgramPostProcessOptions.create()
        posted = program.postProcess(options)
    except Exception as e:
        if not reused:
            safe(lambda: program.deleteMe())        # do not leave a just-created program behind a failed post
        return error(f"Post processing raised: {e}. (Post config: {post_label}; check the post matches "
                     "the machine/operations.)")

    # Honesty gate: postProcess returning true is NOT proof a file landed - diff the folder. A '.failed'
    # stub is a failure marker, NOT a deliverable - keep the two apart so a failed post never lists a
    # stub as if it were G-code.
    written = _new_or_changed(out_dir, before)
    files, failed_markers = [], []
    for p in written:
        if _is_failure_marker(p):
            failed_markers.append(p)
            continue
        size, note = verify_written(p)
        if not note:
            files.append({"file_path": p, "size_bytes": size})

    if not files:
        # No deliverable NC file - a created program that produced nothing is an orphan, so roll it back
        # (a reused program pre-existed, leave it). Surface the post LOG's error lines - the real,
        # actionable reason (a '.failed' stub in the folder only says "see log").
        if not reused:
            safe(lambda: program.deleteMe())
        log_errors = _post_log_errors(prog_name, started)
        msg = (f"NC Program '{prog_name}' did not post a usable NC file to '{out_dir}' "
               f"(postProcess={bool(posted)}, failed_stub={bool(failed_markers)}). "
               + ("The just-created program was removed. " if not reused else ""))
        if log_errors:
            msg += "Post log: " + " | ".join(log_errors)
        else:
            msg += "Check the output folder path, the program number/name, and that the post matches the ops."
        return error(msg)

    # A file appeared, but a faulted program (NCProgram.hasError, the same flag live_readiness counts as
    # programs_errored) OR a '.failed' stub alongside is a BLOCKER - the G-code may be incomplete/wrong.
    program_error = bool(safe(lambda: program.hasError, False))
    clean = bool(posted) and not program_error and not failed_markers

    program_verb = "reused" if reused else "created"
    result = {
        "posted": bool(posted),
        "mode": "as_is" if as_is else "configured",
        "scope": "as_is" if as_is else kind,
        "program_name": prog_name,
        "program_reused": reused,
        "nc_program": program_verb,
        "post_config": post_label,
        "output_folder": out_dir,
        "file_count": len(files),
        "files": files,
        "elapsed_seconds": round(time.time() - started, 1),
    }
    if not as_is:
        result.update({
            "post_scope": post_scope_key,
            "units": units_key,
            "params_applied": applied,
        })
        if unresolved:
            # Non-fatal (the file landed); name what the program did not expose so a caller can confirm.
            result["params_unresolved"] = unresolved
        if unit_note:
            # The units knob failed to apply (non-fatal: the file still posted, but in the program's
            # current units) - surface it explicitly rather than burying it in params_applied.
            result["units_note"] = unit_note
    if not clean:
        # A file appeared but the post flagged failure or the program faulted - surface both facts AND
        # the post log's error lines so the caller sees the real reason, not just "review the output".
        result["partial"] = True
        result["program_error"] = safe(lambda: program.error) if program_error else None
        log_errors = _post_log_errors(prog_name, started)
        if log_errors:
            result["post_log"] = log_errors
        if failed_markers:
            result["failed_stubs"] = failed_markers
        reason = (" ".join(log_errors) if log_errors
                  else f"postProcess={bool(posted)}, program_error={program_error}")
        result["note"] = (f"Post did not report clean success - review before running. {reason}")
        return ok(result)

    if as_is:
        result["note"] = (f"NC Program '{prog_name}' posted AS-IS from its stored configuration - "
                          f"{len(files)} file(s). {live.get('readiness', '')} No operations/post/"
                          "output-folder settings were changed.")
    else:
        result["note"] = (f"NC Program '{prog_name}' {program_verb}, posted {len(files)} file(s). "
                          f"{live.get('readiness', '')} Only valid toolpaths were posted "
                          "(out-of-date/errored ops are omitted); cam_get(include=['nc_programs']) shows the "
                          "program, cam_get(include=['operations']) any ops that were skipped.")
    if unit_note:
        result["note"] += " " + unit_note
    return ok(result)


TOOL_DESCRIPTION = (
    "Create (or reuse) an NC Program for the chosen toolpaths, then post it to a G-code / NC file on "
    "disk - the final CAM step. If an NC Program already named 'program_name' exists it is UPDATED "
    "and re-posted (not duplicated); "
    "otherwise a new one is created. 'scope': omit (or 'document') for the whole document, or a "
    "setup/folder/operation NAME. 'post': the post processor. 'post_scope': local (a full .cps path or "
    "a NAME in the personal/installed post folder - default) | cloud | hub (a NAME in the team post "
    "library; cloud/hub reach deployed team posts and are network-slow). 'output_folder': where the NC file(s) land "
    "(created if absent). 'program_name': the NC Program name / number (also its browser name; some "
    "posts require a number). 'units': document/inch/mm. Only VALID toolpaths post (out-of-date/errored "
    "ops are omitted) - run cam_generate first. Success is gated on the file actually landing on disk. "
    "If 'program_name' names an EXISTING program and scope/post/output_folder are all omitted, it posts "
    "AS-IS from the program's own stored configuration; passing any of those against a stored "
    "configuration that resolves differently is refused unless 'overwrite' is set. "
    "WRITES a persistent NC Program and a file.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="cam_post", description=TOOL_DESCRIPTION)
    .add_input_property("scope", {"type": "string",
            "description": "Setup/folder/operation NAME to post; omit (or 'document') for the whole document."})
    .add_input_property("post", {"type": "string",
            "description": "Post processor: for post_scope=local a full .cps path or a name in the personal/installed post folder; for cloud/hub a post NAME in the team library."})
    .add_input_property(_POST_SCOPE_CHOICE.name, _POST_SCOPE_CHOICE.schema())
    .add_input_property("output_folder", {"type": "string",
            "description": "Directory where the NC file(s) will be written (created if it does not exist)."})
    .add_input_property("program_name", {"type": "string",
            "description": "NC Program name or number (also the browser name; an existing program with this name is reused)."})
    .add_input_property(_UNITS_CHOICE.name, _UNITS_CHOICE.schema())
    .add_input_property("program_comment", {"type": "string",
            "description": "Optional comment embedded in the NC program header."})
    .add_input_property("overwrite", {"type": "boolean",
            "description": "Required true to reconfigure (scope/post/output_folder) an existing program whose stored operations differ from the requested scope (default false)."})
    .strict_schema()
)

# DeliverablesExist is a REDUNDANT gate here: the handler's snapshot-diff verification stays (it
# CONSTRUCTS files[] and drives the partial/rollback logic); the kernel re-stats every claimed
# deliverable so a claim that drifted from disk reality can never ship as success.
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.DeliverablesExist()])


def register_tool():
    register(item)
