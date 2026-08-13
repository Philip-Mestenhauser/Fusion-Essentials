# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set (or create) sketch-text entities in the active design (e.g. an engraved
label/nameplate). WRITES. The writable handle is SketchText.textParameter - a ModelParameter
whose expression is the QUOTED string.
"""

import math

import adsk.core
import adsk.fusion

app = adsk.core.Application.get()

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe, scale, resolve_sketch, all_sketch_names
from . import _common
from . import _inputs
# The SketchText readers live in _sketch_detail (the shared sketch X-ray helper) - a tool imports
# from a helper, never the reverse.
from ._sketch_detail import font_read_back as _font_read_back, unquote_text as _unquote

_MAX = 500

_PATH_MODES = ("along_path", "fit_on_path")
_MODE = _inputs.Choice("mode", ["multi_line"] + list(_PATH_MODES), default="multi_line",
                       description="Layout of NEW text: a box at (x,y), or along 'path'.")
_ALIGN = _inputs.Choice("align", ["left", "center", "right"], default="left",
                        description="Horizontal alignment of NEW text.")

_ALIGN_MEMBERS = {"left": "LeftHorizontalAlignment", "center": "CenterHorizontalAlignment",
                  "right": "RightHorizontalAlignment"}

# Where (x,y) sits along the multi_line box: the fraction of the box width to shift the box LEFT so
# the requested x is the text's left edge / center / right edge.
_ALIGN_ANCHOR = {"left": 0.0, "center": 0.5, "right": 1.0}

# Each mode's definition class, as the created text's objectType reports it. All three spellings
# are bindings-sourced; only fit-on-path's is MEASURED live, where objectType reads
# 'adsk::fusion::FitOnPathTextDefintion' - the misspelling is the real string, so the token keeps
# it rather than correcting it. An objectType matching none of these publishes mode_verified false
# rather than failing the create.
_DEFINITION_TOKENS = {"multi_line": "MultiLineTextDefinition",
                      "along_path": "AlongPathTextDefinition",
                      "fit_on_path": "FitOnPathTextDefintion"}

# The placement values each mode's definition object exposes, for read-back instead of an echo.
_DEFINITION_READBACKS = {
    "multi_line": (("align", "horizontalAlignment"), ("character_spacing", "characterSpacing")),
    "along_path": (("above_path", "isAbovePath"), ("align", "horizontalAlignment"),
                   ("character_spacing", "characterSpacing")),
    "fit_on_path": (("above_path", "isAbovePath"),),
}

# Inputs that only shape NEW text; passing one with create=false would silently do nothing. x/y
# belong here because they default to None, so a supplied value is detectable; 'height' and 'units'
# carry non-None defaults that a caller cannot be told apart from, so they stay exempt.
_CREATE_ONLY = ("mode", "path", "above_path", "align", "character_spacing", "angle_deg",
                "flip_h", "flip_v", "x", "y")

# The read half of this tool: a written text is re-readable as its own entity record, so a caller
# verifying a label does not have to fall back to a screenshot.
_READ_BACK_POINTER = (
    " Read it back with sketch_get(sketch_name=..., include_entities=true): the 'text:<i>' entity "
    "carries the string, height, font and sketch-space bounding box.")


def _given(value) -> bool:
    """True if the caller actually supplied this optional input (False/0 count as supplied)."""
    return value is not None and value != ""


def _refuse_create_only(supplied):
    """The error naming create-only inputs passed with create=false, or ''."""
    named = [n for n, v in supplied if _given(v)]
    if not named:
        return ""
    return (", ".join(f"'{n}'" for n in named) + " shape NEW text only, so add create=true - "
            "editing changes the displayed string and its font, and nothing else. To change an "
            "existing text's layout or rotation, create a replacement.")


def _font_failure(exc, font_name, what):
    """The error for a write that failed while a font was being applied, naming that font.

    A font name is only checked when the text is WRITTEN: an unknown one is accepted onto the
    SketchTextInput and raises '3 : invalid input font name' at add(), and the SketchText.fontName
    setter raises the same sentence with the text's own font left as it was. No API lists or
    validates font names first, so that raise is the whole check and its sentence is handed on."""
    msg = f"Could not {what} with font '{font_name}': {exc}."
    if "font" in str(exc).lower():
        msg += (" Fusion named the font as the problem and no API lists the legal names - pass a "
                "font name that exists on this machine, or omit 'font_name' to keep the current "
                "font.")
    return msg


def _already_changed(changed):
    """The clause naming the texts an aborted edit had ALREADY updated, so a refusal never reads as
    'nothing happened' when part of the run landed."""
    if not changed:
        return " No sketch text was changed."
    names = list(dict.fromkeys(c["sketch"] for c in changed))
    where = ", ".join(f"'{n}'" for n in names[:5]) + (", ..." if len(names) > 5 else "")
    return f" {len(changed)} sketch text(s) earlier in this call were already updated ({where})."


def _refuse_wrong_mode_inputs(mode, path, above_path, align, character_spacing, x, y):
    """The error naming an input the chosen mode has no API slot for, or ''."""
    if mode == "multi_line":
        for label, value in (("path", path), ("above_path", above_path)):
            if _given(value):
                return (f"'{label}' belongs to mode 'along_path'/'fit_on_path'; mode is "
                        f"'multi_line', which places the text in a box at (x,y). Set mode, or drop "
                        f"'{label}'.")
        return ""
    for label, value in (("x", x), ("y", y)):
        if _given(value):
            return (f"'{label}' belongs to mode 'multi_line'; in mode '{mode}' the path curve "
                    f"places the text. Drop '{label}', or move the curve itself.")
    if mode == "fit_on_path":
        for label, value in (("align", align), ("character_spacing", character_spacing)):
            if _given(value):
                # setAsFitOnPath(path, isAbovePath) takes two arguments - there is no slot for
                # either, and fitting sets the spacing itself from the path length.
                return (f"'{label}' is not accepted in mode 'fit_on_path', which spaces the "
                        f"characters over the whole path by itself. Use mode 'along_path' to "
                        f"control '{label}'.")
    return ""


def _resolve_path_curve(sketch, ref):
    """(curve, error) for the SKETCH CURVE a path-mode text follows."""
    s = (ref or "").strip()
    if not s:
        return None, ("'path' is required in mode 'along_path'/'fit_on_path' - the sketch curve "
                      "the text follows, as '<type>:<index>' from "
                      "sketch_get(include_entities=true).")
    if _inputs.is_handle(s):
        # Measured live: a BRepEdge path makes setAsAlongPath return true and then add() raises
        # '2 : InternalValidationError : pSketchCurve'. The API doc's "SketchCurve or BRepEdge"
        # does not hold at add(), so the edge is refused here instead of at a failed create.
        return None, ("'path' takes a SKETCH CURVE id, not a find_geometry handle: a model EDGE is "
                      "accepted by the placement call and then REJECTED by Fusion when the text is "
                      "added ('InternalValidationError : pSketchCurve'). Project the edge into the "
                      "sketch with sketch_project first, then pass the projected curve's "
                      "'<type>:<index>' id.")
    if s.lower().rpartition(":")[0] == "point":
        return None, (f"'path' = '{s}' is a sketch POINT; the text follows a CURVE. Pass a line, "
                      "arc, circle, ellipse or spline id - a CLOSED circle wraps the text right "
                      "around it.")
    ent = _common.resolve_entity_ref(sketch, s)
    if ent is None:
        return None, (f"'path' did not resolve '{ref}'. Use '<type>:<index>', type = "
                      + "/".join(_common.ENTITY_REF_KINDS)
                      + " - ids come from sketch_get(include_entities=true), and the curve must "
                      "live in the same sketch as the text.")
    return ent, None


def _apply_formatting(ipt, angle_deg, flip_h, flip_v):
    """Set angle/flips on the SketchTextInput. Returns (requested values, error).

    Measured live: the input echoes an assigned angle exactly (pi/4 reads back as
    0.7853981633974483) and both flips read back, so set_verified's exact compare holds here and
    catches the SWIG proxy's silent accept of a name it does not define."""
    requested = {}
    if angle_deg is not None:
        try:
            deg = float(angle_deg)
        except Exception:
            return requested, ("'angle_deg' must be a number - the text's rotation in DEGREES from "
                               "the sketch x-axis.")
        err = _common.set_verified(ipt, "angle", math.radians(deg), "angle_deg", "SketchTextInput")
        if err:
            return requested, err
        requested["angle_deg"] = deg
    for value, prop, label in ((flip_h, "isHorizontalFlip", "flip_h"),
                               (flip_v, "isVerticalFlip", "flip_v")):
        if value is None:
            continue
        err = _common.set_verified(ipt, prop, bool(value), label, "SketchTextInput")
        if err:
            return requested, err
        requested[label] = bool(value)
    return requested, ""


def _align_key_of(value):
    """The align key naming a HorizontalAlignments member, or None if it names none of them."""
    if value is None:
        return None
    for key, member in _ALIGN_MEMBERS.items():
        if getattr(adsk.core.HorizontalAlignments, member, None) == value:
            return key
    return None


def _definition_facts(st, mode, index, sketch_name):
    """(facts, error) read off the created text's definition - which mode actually landed, and the
    placement values the definition itself reports. A value that will not read is None.

    `index` is the created text's own creation-order index in sketch.sketchTexts, and `sketch_name`
    the sketch it landed in, so a refusal can hand over the exact sketch_delete_entity call (that
    tool resolves its sketch by name too - a bare target would resolve against the wrong sketch)."""
    definition = safe(lambda: st.definition)
    obj_type = safe(lambda: definition.objectType)
    obj_type = obj_type if isinstance(obj_type, str) and obj_type else None
    placement = {}
    for key, prop in _DEFINITION_READBACKS[mode]:
        value = safe(lambda prop=prop: getattr(definition, prop))
        placement[key] = _align_key_of(value) if key == "align" else value
    facts = {"definition_type": obj_type,
             "mode_verified": bool(obj_type and obj_type.endswith(_DEFINITION_TOKENS[mode])),
             "placement": placement}
    if facts["mode_verified"] or obj_type is None:
        return facts, ""
    landed = [m for m, token in _DEFINITION_TOKENS.items() if obj_type.endswith(token)]
    if landed:
        return facts, (f"Asked for mode '{mode}' but the new text reports a '{landed[0]}' "
                       f"definition ({obj_type}), so it is laid out the wrong way. The text WAS "
                       f"created - remove it with sketch_delete_entity(sketch_name="
                       f"'{sketch_name}', target='text:{index}'), then retry.")
    return facts, ""


def _create_text(design, text, sketch_name, height, x, y, units, mode, path, above_path,
                 align, character_spacing, angle_deg, flip_h, flip_v, font_name):
    """Create a new SketchText in the named sketch, in one of the three layout modes. WRITES."""
    if not (sketch_name or "").strip():
        return error("create=true needs 'sketch_name' - the sketch to add the text to (create one "
    "first with sketch_create).")
    k = scale(units)
    if k is None:
        return error(f"Unknown units '{units}'. Use mm, cm, or in.")
    try:
        h = float(height)
    except Exception:
        return error("'height' must be a number (text height in 'units').")
    if h <= 0:
        return error("'height' must be > 0.")

    merr = _refuse_wrong_mode_inputs(mode, path, above_path, align, character_spacing, x, y)
    if merr:
        return error(merr)
    align_key, aerr = _ALIGN.resolve(align)
    if aerr:
        return error(aerr)
    halign = getattr(adsk.core.HorizontalAlignments, _ALIGN_MEMBERS[align_key], None)
    if halign is None:
        return error(f"Alignment '{align_key}' is not available on this Fusion version.")
    # No range guard: the legal span of the spacing percentage is not established, so a value
    # Fusion rejects comes back as Fusion's own error rather than an invented bound.
    spacing = 0.0
    if character_spacing is not None:
        try:
            spacing = float(character_spacing)
        except Exception:
            return error("'character_spacing' must be a number - the percent change from the "
                         "default spacing (0 = default, 50 = half again as wide).")

    # Resolve across the whole design (active component first) - a sketch created in an activated
    # sub-component must be a valid text target, not only one in the root component.
    sk = resolve_sketch(design, sketch_name.strip())
    if not sk:
        names = all_sketch_names(design)
        return error(f"No sketch named '{sketch_name}'. Available: "
                     + (", ".join(n for n in names if n) or "(none)")
                     + ". Create it first with sketch_create.")

    curve = None
    if mode in _PATH_MODES:
        curve, perr = _resolve_path_curve(sk, path)
        if perr:
            return error(perr)
    above = True if above_path is None else bool(above_path)
    px = 0.0 if x is None else x
    py = 0.0 if y is None else y

    try:
        texts = sk.sketchTexts
        before = safe(lambda: texts.count, 0) or 0
        ipt = texts.createInput2(text, h * k) # text + height (cm)
        if _given(font_name):
            # The input accepts any string; add() below is where Fusion checks the name, so the
            # font is confirmed by reading it off the LANDED text rather than off this input.
            # A font set BEFORE the setAs* placement survives it (the placed text lands with that
            # font) and an unknown name still raises at add(), so the assignment sits here.
            ipt.fontName = font_name
        requested, ferr = _apply_formatting(ipt, angle_deg, flip_h, flip_v)
        if ferr:
            return error(ferr)
        if mode == "along_path":
            placed = ipt.setAsAlongPath(curve, above, halign, spacing)
        elif mode == "fit_on_path":
            placed = ipt.setAsFitOnPath(curve, above)
        else:
            # setAsMultiLine takes SKETCH-plane coordinates (the text lies on the sketch x-y plane,
            # NOT in world/model space); the corner->diagonal box must not be axis-aligned, so both
            # offsets are strictly non-zero (len>=1, h>0).
            # Measured: halign aligns the glyphs WITHIN this box and never moves the box, so the box
            # is ANCHORED per align - centered on x for 'center', ending at x for 'right'. That is
            # what puts the text on the requested x whatever the crude width estimate is worth: the
            # estimate sizes the box, and both edges of it move together with the anchor.
            width = max(len(text), 1) * h * k
            corner_x = px * k - _ALIGN_ANCHOR[align_key] * width
            placed = ipt.setAsMultiLine(
                adsk.core.Point3D.create(corner_x, py * k, 0),
                adsk.core.Point3D.create(corner_x + width, py * k + h * k, 0),
                halign,
                adsk.core.VerticalAlignments.BottomVerticalAlignment, spacing)
        if not placed:
            return error(f"Fusion refused the '{mode}' text placement (it returned false), so no "
                         "text was placed.")
        st = texts.add(ipt)
    except Exception as e:
        if _given(font_name):
            return error(_font_failure(e, font_name, f"create sketch text in '{sketch_name}'"))
        return error(f"Could not create sketch text in '{sketch_name}': {e}.")
    after = safe(lambda: texts.count, 0) or 0

    # Honesty read-back: trust the sketchTexts COUNT, not add's return value. On an on-face sketch
    # the API can hand back a text object while the collection stays empty (nothing materialized) -
    # that false ok is the cardinal sin, so confirm the count actually rose before claiming success.
    if not st or after <= before:
        tail = ("On a sketch built on a FACE, (x,y) are SKETCH-plane coordinates (not world) - "
                "place the text using the 'frame' from sketch_create (where sketch (0,0) sits and "
                "where +X/+Y point) so it lands on the face."
                if mode == "multi_line" else
                f"The path curve '{path}' resolved, so re-read it with "
                "sketch_get(include_entities=true) and confirm it is the curve you meant.")
        return error(
            f"Sketch text did not materialize in '{safe(lambda: sk.name)}': sketchTexts count stayed "
            f"at {before} after add(). Nothing was created. " + tail)

    # SketchTexts.add appends, so the text just created is at count - 1 (pinned by the TextDel act
    # in tool_verify), and that is the index the refusals below hand to sketch_delete_entity.
    facts, derr = _definition_facts(st, mode, after - 1, sketch_name)
    if derr:
        return error(derr)

    landed_font = _font_read_back(st) if _given(font_name) else None
    if landed_font and landed_font != font_name:
        return error(f"Asked for font '{font_name}' but the new text reports '{landed_font}', so "
                     "the font did not take. The text WAS created - remove it with "
                     f"sketch_delete_entity(sketch_name='{sketch_name}', "
                     f"target='text:{after - 1}'), then retry.")

    if mode == "multi_line":
        note = (f"Sketch text created (verified: sketchTexts {before} -> {after}). (x,y) are "
                "SKETCH-plane coordinates - on an on-face sketch use the 'frame' from sketch_create "
                "to keep the text on the face. Extrude/emboss the sketch to engrave it, or edit it "
                "later with sketch_set_text (without create).")
    else:
        verified = f"sketchTexts {before} -> {after}"
        if facts["definition_type"]:
            verified += f", definition {facts['definition_type']}"
        note = (f"Sketch text created on '{path}' (verified: {verified}). A CLOSED path such as a "
                "circle wraps the text right around it. Extrude/emboss the sketch to engrave it, "
                "or edit the string later with sketch_set_text (without create).")
    note += _READ_BACK_POINTER
    out = {
    "created": True,
    "sketch": safe(lambda: sk.name),
    "sketch_text_count": after,
    "text": text,
    "height": round(h, 6),
    "mode": mode,
    "definition_type": facts["definition_type"],
    "mode_verified": facts["mode_verified"],
    }
    if mode == "multi_line":
        out["position"] = {"x": px, "y": py, "units": units}
    else:
        out["path"] = (path or "").strip()
    # Placement is READ BACK off the definition object, never echoed from the request; one that
    # will not read is published None and its requested value moves into 'requested' instead.
    asked = {"above_path": above, "align": align_key, "character_spacing": spacing}
    for key, value in facts["placement"].items():
        out[key] = value
        if value is None:
            requested[key] = asked[key]
    if _given(font_name):
        out["font"] = landed_font
        if landed_font is None:
            requested["font"] = font_name
    if requested:
        out["requested"] = requested
        note += (" " + ", ".join(f"{n}={v}" for n, v in requested.items()) + " are the REQUESTED "
                 "values - they were not read back off the created text, so confirm them with "
                 "view_screenshot.")
    out["note"] = note
    return ok(out)


def _quote(text):
    """Quote a plain string for a text-parameter expression, escaping any single quotes."""
    return "'" + str(text).replace("'", "\\'") + "'"


def _iter_sketch_texts(design, sketch_name):
    """Yield (component_name, sketch_name, sketch_text) for the target sketch(es)."""
    want = (sketch_name or "").strip()
    for comp in safe(lambda: design.allComponents, []) or []:
        try:
            sketches = comp.sketches
        except Exception:
            continue
        for sk in _common.iter_collection(sketches):
            sk_name = safe(lambda sk=sk: sk.name) or ""
            if want and sk_name != want:
                continue
            texts = safe(lambda sk=sk: sk.sketchTexts)
            if not texts:
                continue
            # A text's INDEX within its sketch is its address (the 'index' input picks the Nth text,
            # and it is the same index sketch_delete_entity('text:<index>') deletes by), so this stays
            # a positional walk: iter_collection drops an unreadable text, which would slide every
            # later text onto the wrong index. item(j) is guarded the same way - a stale text proxy
            # burns its slot (st None) instead of raising the whole walk away.
            for j in range(safe(lambda texts=texts: texts.count, 0)):
                yield (safe(lambda comp=comp: comp.name), sk_name,
                       safe(lambda texts=texts, j=j: texts.item(j)))


def handler(text: str = "", sketch_name: str = "", index: int = -1,
            create: bool = False, height: float = 5.0, x: float = None, y: float = None,
            units: str = "mm", mode: str = "", path: str = "", above_path: bool = None,
            align: str = "", character_spacing: float = None, angle_deg: float = None,
            flip_h: bool = None, flip_v: bool = None, font_name: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    if text is None:
        return error("Provide 'text' - the string to display.")

    design = _common.design()
    if not design:
        return error("No active design (open a document with sketch text).")

    if create:
        mode_key, merr = _MODE.resolve(mode)
        if merr:
            return error(merr)
        return _create_text(design, text, sketch_name, height, x, y, units, mode_key, path,
                            above_path, align, character_spacing, angle_deg, flip_h, flip_v,
                            font_name)

    cerr = _refuse_create_only(zip(_CREATE_ONLY, (mode, path, above_path, align,
                                                  character_spacing, angle_deg, flip_h, flip_v,
                                                  x, y)))
    if cerr:
        return error(cerr)

    targets = list(_iter_sketch_texts(design, sketch_name))
    if not targets:
        if (sketch_name or "").strip():
            return error(f"No sketch text found in a sketch named '{sketch_name}'. (Use "
    "sketch_get to list sketches; the text must live in a sketch with "
    "that exact name.)")
        return error("No sketch text found in the active design.")

    want_index = int(index) if index is not None else -1
    changed = []
    skipped = 0
    truncated = False
    # Track per-sketch running index so 'index' selects the Nth text within that sketch. Keyed by
    # (component, sketch) - a sketch NAME alone is not unique across components, and a name-keyed
    # counter would interleave two same-named sketches' texts onto wrong indices.
    per_sketch_counter = {}
    for comp_name, sk_name, st in targets:
        if len(changed) >= _MAX:
            truncated = True
            break
        k = per_sketch_counter.get((comp_name, sk_name), 0)
        per_sketch_counter[(comp_name, sk_name)] = k + 1
        if want_index >= 0 and k != want_index:
            skipped += 1
            continue
        if st is None:
            # The slot is burned (the index space must not slide), but the text itself would not
            # read - editing it blind is impossible, and silently skipping a SELECTED index would
            # report success over a hole.
            if want_index >= 0:
                return error(f"Sketch text {k} in '{sk_name}' could not be read (a stale or "
                             "deleted text proxy holds that index). Re-read the sketch with "
                             "sketch_get(include_entities=true) and retry with a readable index."
                             + _already_changed(changed))
            skipped += 1
            continue
        before = _unquote(safe(lambda st=st: st.textParameter.expression))
        font_now = None
        if _given(font_name):
            # The font goes on FIRST: a name Fusion refuses raises here, leaving this text's string
            # untouched too, so the refusal is reported against unchanged state.
            try:
                st.fontName = font_name
            except Exception as e:
                return error(_font_failure(e, font_name,
                                           f"set the font of sketch text in '{sk_name}'")
                             + _already_changed(changed))
            font_now = _font_read_back(st)
            if font_now and font_now != font_name:
                return error(f"Setting the font of sketch text in '{sk_name}' did not take - "
                             f"SketchText.fontName reads back '{font_now}', not '{font_name}'."
                             + _already_changed(changed))
        try:
            st.textParameter.expression = _quote(text)
        except Exception as e:
            msg = f"Failed to set sketch text in sketch '{sk_name}': {e}"
            if _given(font_name):
                # The font already landed on THIS text - a bare 'failed' would read as no effect.
                landed = f"'{font_now}'" if font_now else f"the requested '{font_name}'"
                msg += (f". Its font WAS changed to {landed} before the string failed, so that one "
                        "text now carries the new font with its old string."
                        + _already_changed(changed))
            return error(msg)
        after = _unquote(safe(lambda st=st: st.textParameter.expression))
        record = {"component": comp_name, "sketch": sk_name, "before": before, "after": after}
        if _given(font_name):
            record["font"] = font_now
        changed.append(record)

    if not changed:
        return error(f"No sketch text matched index {want_index} in sketch '{sketch_name}'.")

    # Force a recompute so DOWNSTREAM features rebuild against the new text. Changing
    # textParameter.expression updates the sketch, but a feature that consumes the text (e.g. an
    # Emboss/extrude that engraves it) can show STALE geometry until the design recomputes - which
    # is why an engraving can look unchanged even though the text value is correct. computeAll
    # makes the visible model match. Only meaningful in parametric mode (direct mode has no tree).
    recomputed = False
    try:
        if safe(lambda: design.designType) == 1:  # ParametricDesignType
            design.computeAll()
            recomputed = True
    except Exception:
        recomputed = False

    out = {
    "set": True,
    "text": text,
    "changed_count": len(changed),
    "changed": changed,
    "truncated": truncated,
    "recomputed": recomputed,
    "note": ("Sketch text updated" + (" and design recomputed so any engraving/emboss that "
                "consumes it rebuilt" if recomputed else "") + "." + _READ_BACK_POINTER),
    }
    if _given(font_name):
        out["note"] += (f" Each entry's 'font' is what the text reports after applying "
                        f"'{font_name}'.")
    if truncated:
        out["note"] += f" Hit the {_MAX}-edit cap - not every match was updated; narrow with 'sketch_name'/'index' and call again."
    return ok(out)


TOOL_DESCRIPTION = (
"Set the displayed string of sketch text (e.g. an engraved label) in the active design, or add "
"new text with create=true. Editing: 'sketch_name' limits the change to one sketch and 'index' "
"to one text in it (omit both to update EVERY sketch text); each text's before/after is "
"reported. Creating: 'mode' boxes the text at (x,y) (multi_line) or runs it along the sketch "
"curve named by 'path' - along_path keeps normal glyph spacing, fit_on_path stretches the string "
"over the whole curve, and a CLOSED path such as a circle wraps the text around it. Read sketch "
"names and curve ids with sketch_get."
)

tool = (
    Tool.create_with_string_input(
        name="sketch_set_text",
        description=TOOL_DESCRIPTION,
        input_param_name="text",
        input_param_description="The new string to display.",
    )
    .add_input_property("sketch_name", {"type": "string",
            "description": "Only update sketch texts in the sketch with this name (omit = all)."})
    .add_input_property("index", {"type": "integer",
            "description": "If a sketch has multiple texts, the 0-based one to update (default all)."})
    .add_input_property("create", {"type": "boolean",
            "description": "CREATE new text instead of editing: add it to 'sketch_name' at (x,y) with 'height'. Default false."})
    .add_input_property("height", {"type": "number", "description": "Text height in 'units' (create only; default 5)."})
    .add_input_property("x", {"type": "number", "description": "Text X in 'units' (multi_line only): with align left/center/right it is the text's left edge / center / right edge."})
    .add_input_property("y", {"type": "number", "description": "Text Y position in 'units' (multi_line only)."})
    .add_input_property(*_inputs.units_property(description="Units for text height (create only)."))
    .add_input_property(*_MODE.as_property())
    .add_input_property("path", {"type": "string",
            "description": "Curve the text follows, '<type>:<index>' from the same sketch."})
    .add_input_property("above_path", {"type": "boolean",
            "description": "Put the text above the path rather than below (default true)."})
    .add_input_property(*_ALIGN.as_property())
    .add_input_property("character_spacing", {"type": "number",
            "description": "Percent change from the default character spacing."})
    .add_input_property("angle_deg", {"type": "number",
            "description": "Rotation of new text, DEGREES from the sketch x-axis."})
    .add_input_property("flip_h", {"type": "boolean", "description": "Mirror new text horizontally."})
    .add_input_property("flip_v", {"type": "boolean", "description": "Mirror new text vertically."})
    .add_input_property("font_name", {"type": "string",
            "description": "Font to use, when creating AND when editing. Case-sensitive: 'Arial' works, 'arial' is refused. Omit to keep the current font."})
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
