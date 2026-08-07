# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Owner-present live verification of the drawing user-present tier.

The blind sweep (tool_verify.py) excludes the tools that need an open, reviewed drawing document:
drawing_update, drawing_export, drawing_add_sketch, drawing_dimension, drawing_edit_sheet,
drawing_insert_image - plus drawing_create's two real create paths (its sweep beats are all
refusals). This script drives them, in two phases, because an auto-created drawing cannot be
opened headlessly until a human has opened it once:

  py -3 tests/live/drawing_verify.py --stage
      Builds a parametric plate, saves it to MCP Test Project, and creates TWO drawings from it
      (default ISO, and full-option ASME) - the create beats. Prints the drawing to open.
  <the owner opens the ISO drawing in the Fusion UI and leaves it the active document>
  py -3 tests/live/drawing_verify.py --run
      Drives every user-present drawing beat against the open drawing, round-trips a source-design
      edit through drawing_update, and exports the PDFs the owner eyeballs.

Results go to tests/live/results/drawing-verify-<ts>.json plus a printed ledger. This script never
touches VERIFIED_TOOLS.md - the blind sweep's receipt stays its own.
"""

import argparse
import json
import os
import struct
import sys
import time
import zlib

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
from tool_verify import call, health_gate  # noqa: E402  (the one HTTP driver, reused)

RESULTS_DIR = os.path.join(_HERE, "results")
OUT_DIR = os.path.join(RESULTS_DIR, "drawing_verify")
STAGE_FILE = os.path.join(RESULTS_DIR, "drawing_verify_stage.json")
PROJECT = "MCP Test Project"
URN_PREFIX = "urn:adsk.wipprod:dm.lineage:"


def _write_png(path, size=64, rgb=(255, 140, 0)):
    """A solid-colour PNG written from scratch - the local image drawing_insert_image places."""
    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * size
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)
    return path


def _run_steps(steps, ctx):
    """tool_verify's step contract: rows of (tool, args, expect, save). args may be callable(ctx);
    expect is 'ok' / 'refused' / callable(payload)->bool; save is (key, extract(payload))."""
    rows = []
    for tool, args, expect, save in steps:
        try:
            arguments = args(ctx) if callable(args) else dict(args)
        except KeyError as e:
            rows.append((tool, "blocked", str(e)))
            continue
        is_error, payload = call(tool, arguments)
        if callable(expect):
            if is_error:
                status, note = "FAIL", str(payload)[:160]
            else:
                try:
                    good = bool(expect(payload))
                except Exception as e:
                    good, payload = False, f"predicate raised: {e}"
                status, note = ("pass", "") if good else ("FAIL", str(payload)[:160])
        elif expect == "ok" and not is_error:
            status, note = "pass", ""
        elif expect == "refused" and is_error:
            status, note = "expected-refusal", str(payload)[:100]
        else:
            status, note = "FAIL", str(payload)[:160]
        if status == "pass" and save is not None and not is_error:
            key, extract = save
            try:
                ctx[key] = extract(payload)
            except Exception as e:
                status, note = "FAIL", f"saved-value extraction failed: {e}"
        rows.append((tool, status, note))
        print(f"  {status:18} {tool:26} {note}", flush=True)
        time.sleep(0.1)
    return rows


def _report(rows, phase):
    fails = [r for r in rows if r[1] in ("FAIL", "blocked")]
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"drawing-verify-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"phase": phase, "steps": rows}, fh, indent=2)
    print(f"\n== {phase}: {len(rows)} steps, {len(fails)} FAIL/blocked -> {path}")
    return 1 if fails else 0


def _created_urn(p):
    return bool(p.get("created")) and str(p.get("file_id", "")).startswith(URN_PREFIX)


def _create_drawing_with_retry(args, tries=6, wait_s=15):
    """drawing_create, retried on the processing lag: a design saved seconds earlier fails with
    '3 : Failed to create drawing document' until its DataFile finishes cloud processing
    (about a minute after doc_save_as)."""
    for attempt in range(tries):
        is_error, payload = call("drawing_create", args)
        if not (is_error and "Failed to create drawing document" in str(payload)):
            return is_error, payload
        if attempt < tries - 1:
            print(f"    (source still processing cloud-side - retry {attempt + 1}/{tries - 1} "
                  f"in {wait_s}s)", flush=True)
            time.sleep(wait_s)
    return is_error, payload


def stage():
    health_gate()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    design_name = f"SweepDrawingSource {stamp}"
    ctx = {}
    steps = [
        ("doc_new", {}, "ok", None),
        ("param_add", {"name": "PlateH", "expression": "10 mm"}, "ok", None),
        ("sketch_create", {"plane": "xy", "name": "PlateSketch"}, "ok", None),
        ("sketch_add_geometry", {"kind": "rectangle", "x1": 0, "y1": 0, "x2": 80, "y2": 50,
                                 "sketch_name": "PlateSketch"}, "ok", None),
        ("model_extrude", {"sketch_name": "PlateSketch", "profile_index": 0,
                           "distance": "PlateH"}, "ok", None),
        ("doc_save_as", {"name": design_name, "project": PROJECT}, "ok",
         ("design_urn", lambda p: p.get("document_id"))),
    ]
    print("-- STAGE: source design + the two drawing_create beats --")
    rows = _run_steps(steps, ctx)

    # The two real create beats the blind sweep cannot carry (they mint cloud files). Fusion
    # auto-names both drawings identically, so identity is the lineage URN.
    for label, args in (
            ("iso", {}),
            ("asme", {"standard": "asme", "units": "inch", "sheet_size": "b",
                      "view_style": "shaded_hidden", "tangent_edges": "shortened",
                      "hole_annotations": "thread", "parts_list": True,
                      "parts_list_location": "bottom_right", "auto_dimension": "baseline"})):
        is_error, payload = _create_drawing_with_retry(args)
        good = (not is_error) and isinstance(payload, dict) and _created_urn(payload)
        status = "pass" if good else "FAIL"
        rows.append(("drawing_create", status, "" if good else str(payload)[:160]))
        print(f"  {status:18} {'drawing_create':26} [{label}]", flush=True)
        if good:
            ctx[f"drawing_{label}"] = (payload.get("drawing_name"), payload.get("file_id"))

    rc = _report(rows, "stage")
    if rc == 0:
        state = {"design_name": design_name, "design_urn": ctx.get("design_urn"),
                 "drawing_iso": ctx.get("drawing_iso"), "drawing_asme": ctx.get("drawing_asme")}
        with open(STAGE_FILE, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
        iso_name = state["drawing_iso"][0] if state["drawing_iso"] else "?"
        print(f"\nstage state -> {STAGE_FILE}")
        print(f"\nNEXT (one human step): both drawings carry the name '{iso_name}'. In the Fusion")
        print(f"UI ({PROJECT}) open EACH of them once (review + close is fine), then run:")
        print("  py -3 tests/live/drawing_verify.py --run")
    return rc


def run():
    health_gate()
    if not os.path.isfile(STAGE_FILE):
        sys.exit(f"No stage state at {STAGE_FILE} - run --stage first.")
    with open(STAGE_FILE, encoding="utf-8") as fh:
        state = json.load(fh)
    design_name = state["design_name"]
    iso_name, iso_urn = state["drawing_iso"]

    # Both staged drawings share a name, so the ISO one is reached by URN: doc_activate if it is
    # open, else doc_open (headless open works once the owner has reviewed it in the UI).
    is_error, payload = call("doc_activate", {"name": iso_urn})
    if is_error:
        is_error, payload = call("doc_open", {"file_id": iso_urn, "force_api_open": True})
        if is_error:
            sys.exit(f"Could not reach the staged ISO drawing by URN ({iso_urn}): {payload}\n"
                     f"Open '{iso_name}' in the Fusion UI ({PROJECT}) once, then rerun.")
        time.sleep(3)
    is_error, orient = call("workspace_orient", {})
    active = "" if is_error else ((orient.get("document") or {}).get("name") or "")
    if iso_name not in active:
        sys.exit(f"The active document is '{active or '?'}', not the staged drawing '{iso_name}'.\n"
                 f"Open '{iso_name}' in the Fusion UI ({PROJECT}) and make it active, then rerun.")

    os.makedirs(OUT_DIR, exist_ok=True)
    png = _write_png(os.path.join(OUT_DIR, "sweep_marker.png"))
    stamp = time.strftime("%H%M%S")
    pdf1 = os.path.join(OUT_DIR, f"sweep_drawing_{stamp}.pdf")
    pdf2 = os.path.join(OUT_DIR, f"sweep_drawing_updated_{stamp}.pdf")
    dxf = os.path.join(OUT_DIR, f"sweep_drawing_{stamp}.dxf")
    dwg = os.path.join(OUT_DIR, f"sweep_drawing_{stamp}.dwg")

    ctx = {}
    steps = [
        # Up-to-date no-op: the drawing was just generated from the saved design.
        ("drawing_update", {}, lambda p: p.get("updated") is False and p.get("is_up_to_date") is True,
         None),

        # drawing_dimension - active sheet still holds the generated views.
        ("drawing_dimension", {}, "refused", None),
        ("drawing_dimension", {"view": 999}, "refused", None),
        ("drawing_dimension", {"view": 0, "strategy": "bogus"}, "refused", None),
        ("drawing_dimension", {"view": 0, "strategy": "baseline"},
         lambda p: p.get("dimensioned") is True and p.get("document_modified") is True, None),
        ("drawing_dimension", {"view": 0, "strategy": "ordinate", "datum": "top_right"},
         lambda p: p.get("dimensioned") is True, None),

        # drawing_insert_image - the locally written PNG.
        ("drawing_insert_image", {"image_path": png, "x": 150, "y": 100},
         lambda p: p.get("inserted") is True, None),
        ("drawing_insert_image", {"image_path": png, "x": 40, "y": 40, "scale": 2},
         lambda p: p.get("inserted") is True and p.get("scale") == 2.0, None),
        ("drawing_insert_image", {"image_path": png + ".missing", "x": 0, "y": 0}, "refused", None),
        ("drawing_insert_image", {"image_path": png, "x": 0, "y": 0, "scale": 0}, "refused", None),
        ("drawing_insert_image", {"image_path": png}, "refused", None),

        # drawing_add_sketch - one call, five kinds, six curves.
        ("drawing_add_sketch", {"name": "SweepDwgSketch", "geometry": [
            {"kind": "line", "points": [[0, 0], [30, 0], [30, 20]]},
            {"kind": "rectangle", "points": [[40, 5], [70, 25]]},
            {"kind": "circle", "points": [[15, 35]], "radius": 5},
            {"kind": "arc", "points": [[0, 45], [10, 50], [20, 45]]},
            {"kind": "ellipse", "points": [[55, 40], [65, 40], [55, 44]]},
        ]}, lambda p: p.get("curves_landed") == 6, None),
        ("drawing_add_sketch", {"geometry": [{"kind": "hexagon", "points": [[0, 0]]}]},
         "refused", None),
        ("drawing_add_sketch", {"geometry": [{"kind": "circle", "points": [[0, 0]]}]},
         "refused", None),
        ("drawing_add_sketch", {"sheet_name": "NoSuchSheet",
                                "geometry": [{"kind": "circle", "points": [[0, 0]], "radius": 2}]},
         "refused", None),

        # drawing_edit_sheet - adds/copies change the ACTIVE sheet, so these come after the
        # active-sheet beats above.
        ("drawing_edit_sheet", {"action": "add"},
         lambda p: p.get("added") is True and p.get("sheet_count") == p.get("sheet_count_before") + 1,
         None),
        ("drawing_edit_sheet", {"action": "add", "new_name": "SweepSheetA"},
         lambda p: p.get("sheet") == "SweepSheetA", None),
        ("drawing_edit_sheet", {"action": "add", "new_name": "SweepDup"},
         lambda p: p.get("sheet") == "SweepDup", None),
        ("drawing_edit_sheet", {"action": "add", "new_name": "SweepDup"}, "refused", None),
        ("drawing_edit_sheet", {"action": "rename", "sheet": "SweepSheetA",
                                "new_name": "SweepSheetB"},
         lambda p: p.get("changed") is True and p.get("previous_name") == "SweepSheetA", None),
        # A case variant of another sheet's name: the measured silent no-op, surfaced as an error.
        ("drawing_edit_sheet", {"action": "rename", "sheet": "SweepSheetB", "new_name": "sweepdup"},
         "refused", None),
        ("drawing_edit_sheet", {"action": "rename", "sheet": "SweepSheetB", "new_name": ""},
         "refused", None),
        ("drawing_edit_sheet", {"action": "copy", "sheet": "SweepSheetB", "new_name": "SweepCopy"},
         lambda p: p.get("copied") is True and p.get("copied_from") == "SweepSheetB", None),
        ("drawing_edit_sheet", {"action": "set_size", "sheet": "SweepCopy", "sheet_size": "a3"},
         lambda p: p.get("sheet_size") == "a3", None),
        ("drawing_edit_sheet", {"action": "set_size", "sheet": "SweepCopy", "sheet_size": "b"},
         "refused", None),
        ("drawing_edit_sheet", {"action": "set_orientation", "sheet": "SweepCopy",
                                "orientation": "portrait"},
         lambda p: p.get("orientation") == "portrait", None),
        ("drawing_edit_sheet", {"action": "set_orientation", "sheet": "SweepCopy",
                                "orientation": "landscape"},
         lambda p: p.get("orientation") == "landscape", None),
        ("drawing_edit_sheet", {"action": "set_size", "sheet": "SweepCopy", "sheet_size": "a0"},
         lambda p: p.get("sheet_size") == "a0", None),
        ("drawing_edit_sheet", {"action": "set_orientation", "sheet": "SweepCopy",
                                "orientation": "portrait"}, "refused", None),
        ("drawing_edit_sheet", {"action": "tidy_up"}, lambda p: p.get("tidied") is True, None),
        ("drawing_edit_sheet", {"action": "delete", "sheet": "SweepCopy"},
         lambda p: p.get("deleted") is True, None),
        ("drawing_edit_sheet", {"action": "delete", "sheet": "NoSuchSheet"}, "refused", None),

        # drawing_export - all three formats land non-empty files; a cross-format option refused.
        ("drawing_export", {"format": "pdf", "file_path": pdf1},
         lambda p: (p.get("size_bytes") or 0) > 0, None),
        ("drawing_export", {"format": "dxf", "file_path": dxf},
         lambda p: (p.get("size_bytes") or 0) > 0, None),
        ("drawing_export", {"format": "dwg", "file_path": dwg, "dwg_variant": "autocad"},
         lambda p: (p.get("size_bytes") or 0) > 0, None),
        ("drawing_export", {"format": "dxf", "file_path": dxf, "sheet_range": "1-2"},
         "refused", None),

        # The round-trip: edit the source design, save it, refresh the stale drawing.
        # By URN: opening each drawing loads its source design as an extra same-name loaded doc
        # (measured: two open docs shared the design's exact name), so the display name is refused
        # as ambiguous - correctly - by doc_activate.
        ("doc_activate", {"name": state.get("design_urn") or design_name}, "ok", None),
        ("param_set", {"name": "PlateH", "expression": "16 mm"}, "ok", None),
        ("doc_save", {"description": "PlateH 10 -> 16 for the drawing_update beat"}, "ok", None),
        # By URN: the two staged drawings share a display name.
        ("doc_activate", {"name": iso_urn}, "ok", None),
        ("drawing_update", {},
         lambda p: p.get("updated") is True and p.get("stale_references_before", 0) > 0, None),
        ("drawing_export", {"format": "pdf", "file_path": pdf2},
         lambda p: (p.get("size_bytes") or 0) > 0, None),
    ]
    print(f"-- RUN: user-present drawing beats on '{iso_name}' --")
    rows = _run_steps(steps, ctx)
    rc = _report(rows, "run")
    if rc == 0:
        print("\nEYEBALL (the human half of this verification):")
        print(f"  {pdf1}   - dimensions on view 0, two orange markers, the sketch shapes, the sheets")
        print(f"  {pdf2}   - the plate thickness now 16 mm after drawing_update")
    return rc


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--stage", action="store_true")
    g.add_argument("--run", action="store_true")
    args = ap.parse_args()
    sys.exit(stage() if args.stage else run())
