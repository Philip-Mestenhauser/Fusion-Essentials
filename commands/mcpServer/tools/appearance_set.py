# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: set the color/appearance of a body, occurrence, or component. WRITES.

Reuses the design's existing appearance of the target name (addByCopy refuses duplicate names) or
copies the base appearance named below (Appearances.addByCopy) so the tool owns an editable
instance, writes the requested color into its albedo channel, then assigns it to the resolved
target - a component (or the whole design) colors all its bodies.
"""

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import error, ok, safe
from . import _common
from . import _inputs
from . import _materials

# MEASURED: a Fusion appearance's transparency is its Prism material CLASS - a see-through appearance
# carries interior_model=3 plus transparent_color / transparent_distance / transparent_ior - and NOT
# the alpha of its Color property. Both directions were measured on one body: an OPAQUE appearance
# renders fully opaque at alpha 100, and a transparent appearance whose own alpha is 255 renders
# see-through. Color.create stores the alpha faithfully and the render ignores it, so an opacity
# this tool cannot deliver is REFUSED rather than echoed back in the payload as landed.
_OPAQUE = 255

# The one appearance every color override is copied from, resolved by EXACT name in the library
# named here. MEASURED on Fusion 2705.1.4: interior_model 0 (opaque), opaque_albedo (246, 246, 243),
# opaque_f0 0.06027, surface_roughness 0.07746, opaque_emission False. The same library holds
# 'Plastic - Matte (White)' (surface_roughness 0.70711) as the matte counterpart - this tool takes
# no finish input, so it copies the glossy one and offers no choice.
_BASE_LIBRARY = "Fusion Appearance Library"
_BASE_SOURCE = "Paint - Enamel Glossy (White)"
# The name the copy is kept under in the document, and looked up by on every later call.
_BASE_NAME = "MCP Neutral Base"

# The ColorProperty ids MEASURED on that base: 'opaque_albedo', which reads (246, 246, 243) on this
# white base - the channel carrying its color - and 'opaque_luminance_modifier'. Only the albedo is
# written.
_ALBEDO_IDS = ("opaque_albedo",)


def _parse_color(spec):
    """Parse '#RRGGBB' / 'RRGGBB' / 'r,g,b' -> (r, g, b) ints 0-255, or (None, msg)."""
    s = (spec or "").strip()
    if not s:
        return None, "Provide 'color' as '#RRGGBB', 'RRGGBB', or 'r,g,b' (0-255 each)."
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip() != ""]
        if len(parts) != 3:
            return None, f"'{spec}' is not 'r,g,b' (three 0-255 components)."
        try:
            rgb = tuple(int(p) for p in parts)
        except ValueError:
            return None, f"'{spec}' has non-integer components; use 'r,g,b' (0-255 each)."
    else:
        h = s[1:] if s.startswith("#") else s
        if len(h) != 6:
            return None, f"'{spec}' is not a 6-digit hex color (e.g. '#1E8E3E')."
        try:
            rgb = tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None, f"'{spec}' is not valid hex; use '#RRGGBB'."
    if any(c < 0 or c > 255 for c in rgb):
        return None, f"'{spec}' has a component outside 0-255."
    return rgb, None


# A face/body/occurrence/component (or '' = whole design) all carry a settable .appearance - exactly
# TargetRef's universe (minus mesh: a MeshBody has no appearance override). The whole-design ('') case
# is a Component (apply to all bodies), so 'design' is folded into the component branch below.
_TARGET = _inputs.TargetRef("target", allow=("body", "face", "occurrence", "component", "design"))


def _base_appearance(appearances):
    """The Appearance every color override is copied FROM, as (appearance, reused, err), given the
    DESIGN's appearances collection.

    It is resolved by EXACT name (_BASE_SOURCE) in the library named _BASE_LIBRARY, copied into the
    document once under _BASE_NAME, and looked up by that name on every later call - so one base
    serves the whole document. design.appearances.item(0) is NOT consulted: it is whichever
    appearance entered the document first (insertion order - measured 'Steel - Satin', an
    interior_model=1 METAL carrying no opaque_albedo channel), so an override copied from it is a
    tinted metal at steel's roughness. An absent library or an absent source appearance is REFUSED,
    naming what was looked for."""
    held = safe(lambda: appearances.itemByName(_BASE_NAME))
    if held is not None:
        return held, True, None
    lib, lerr = _materials.find_library(_BASE_LIBRARY)
    if lerr:
        return None, False, (f"'{_BASE_LIBRARY}' is where the color base '{_BASE_SOURCE}' is read "
                             f"from. {lerr}")
    src = safe(lambda: lib.appearances.itemByName(_BASE_SOURCE))
    if src is None:
        return None, False, (f"No appearance named '{_BASE_SOURCE}' in '{_BASE_LIBRARY}' - it is "
                             "the base every color override is copied from. List that library with "
                             "design_get(include=['appearances'], library='" + _BASE_LIBRARY + "').")
    copied = safe(lambda: appearances.addByCopy(src, _BASE_NAME))
    if copied is None:
        # a parallel call can land the name between the lookup and the copy - re-check once
        copied = safe(lambda: appearances.itemByName(_BASE_NAME))
        if copied is not None:
            return copied, True, None
        return None, False, (f"Could not copy '{_BASE_SOURCE}' into this document as "
                             f"'{_BASE_NAME}' (addByCopy returned nothing).")
    return copied, False, None


def _write_albedo(appr, color):
    """Write `color` into the appearance's albedo channel(s). Returns (written_ids, seen_ids) -
    every ColorProperty id the write took on, and every one the appearance exposed. A ColorProperty
    outside _ALBEDO_IDS is left alone: the base carries a second one,
    'opaque_luminance_modifier', whose effect was not measured here (the base reads
    opaque_emission False), so writing the requested color into it is a change nothing read back."""
    written, seen = [], []
    for p in _common.iter_collection(safe(lambda: appr.appearanceProperties)):
        if safe(lambda p=p: type(p).__name__) != "ColorProperty":
            continue
        pid = safe(lambda p=p: p.id)
        seen.append(str(pid))
        if pid not in _ALBEDO_IDS:
            continue
        try:
            p.value = color
            written.append(str(pid))
        except Exception:
            pass  # a read-only / texture-backed channel; try the next albedo one
    return written, seen


def _make_colored_appearance(design, rgb, opacity, name):
    """An appearance named `name` with the requested color set: REUSE one already in the design
    (addByCopy refuses a duplicate name, so a second same-color call - or a retry after a half-made
    copy - must find the existing one, not fail), else copy the base appearance in. The color is
    (re)applied either way, so a reused appearance always ends up at the requested color.
    Returns (appearance, reused, base_reused, err), where base_reused is None when the named
    appearance already existed and no base was consulted at all."""
    appearances = safe(lambda: design.appearances)
    if appearances is None:
        return None, False, None, ("The design's appearances collection could not be read, so no "
                                   "color override could be made.")
    appr = safe(lambda: appearances.itemByName(name))
    reused = appr is not None
    base_reused = None
    if appr is None:
        base, base_reused, berr = _base_appearance(appearances)
        if berr:
            return None, False, None, berr
        appr = safe(lambda: appearances.addByCopy(base, name))
        if not appr:
            # a parallel call can land the name between the lookup and the copy - re-check once
            appr = safe(lambda: appearances.itemByName(name))
            reused = appr is not None
        if not appr:
            return None, False, None, ("Could not create an appearance copy (addByCopy returned "
                                       "nothing).")
    color = adsk.core.Color.create(rgb[0], rgb[1], rgb[2], opacity)
    written, seen = _write_albedo(appr, color)
    if not written:
        # A REUSED appearance is one this call found under the requested name and did not make, so
        # a different 'name' gets a fresh copy of the base; a freshly copied one has no such route.
        hint = (f" Pass a different 'name' to mint a fresh override from '{_BASE_NAME}'."
                if reused else "")
        return None, False, None, (
            f"'{name}' exposes no writable albedo color property, so the color was not applied "
            f"(looked for {', '.join(_ALBEDO_IDS)}; its color properties: "
            f"{', '.join(seen) or 'none'})." + hint)
    return appr, reused, base_reused, None


def _reads_as(entity, appr_id, appr_name):
    """True / False / None - does `entity` (a body, face, or occurrence) now read the exact
    appearance this call just applied? None means the comparison could not be made.

    BOTH keys must match, because NEITHER alone identifies an appearance INSTANCE:

    - NAME alone is meaningless across the catalog: names are non-unique (MEASURED: 92 of 172
      distinct names over 530 library appearances are shared), so a body that kept a same-named
      appearance of its own reads as reached.
    - ID alone is not an instance either. An id identifies the SOURCE ASSET: two appearances this
      tool minted seconds apart from the same base library asset carry byte-identical ids
      (MEASURED - a copy keeps its source's id), and the tool mints EVERY color from one base, so
      that is the common case, not the corner. Reproduced live: after a body-level override, an
      occurrence write in a different color listed the overridden body as reached on id alone
      while the body had plainly kept its own appearance.

    Together they hold: this tool controls the name (it minted or reused exactly one appearance
    under a name it chose), so name pins the instance among same-id copies while id pins the asset
    among same-name strangers. Unreadable on either side answers None - a one-sided fallback would
    answer with the key that cannot discriminate.
    """
    got_id = safe(lambda: entity.appearance.id)
    got_name = safe(lambda: entity.appearance.name)
    if got_id is None or got_name is None or appr_id is None or appr_name is None:
        return None
    return got_id == appr_id and got_name == appr_name


def _occurrence_fanout(occ, appr_id, appr_name):
    """(reached, not_reached, unverified) after an OCCURRENCE-level appearance write.

    MEASURED: an occurrence-level assignment fans onto the occurrence's bodies, but a body that
    already carried its own appearance keeps it - while the occurrence's .appearance still reads
    back as the newly set one. So the occurrence read-back is not proof the bodies changed, and
    each body is compared through _reads_as instead.

    Anything the comparison cannot be made on is 'unverified', never a classification: a body whose
    appearance declines to answer, and - since there is nothing to compare against - every body
    when the newly applied appearance's own id or name could not be read.

    The CAUSE of a miss is not read here. BRepBody.appearanceSourceType does separate a body
    carrying its own appearance from one the occurrence reached, but adsk.fusion.
    AppearanceSourceTypes has no row in the generated live_api_facts.ENUMS, and a reference to a
    family with no row is what test_enum_families_measured refuses - so this reports the
    OBSERVATION (which appearance each body reads now) and never a cause.
    """
    reached, not_reached, unverified = [], [], []
    for i, b in enumerate(_common.iter_collection(safe(lambda: occ.bRepBodies))):
        bname = safe(lambda b=b: b.name) or f"body #{i}"
        verdict = _reads_as(b, appr_id, appr_name)
        if verdict is None:
            unverified.append(bname)
        elif verdict:
            reached.append(bname)
        else:
            not_reached.append({"body": bname,
                                "appearance": safe(lambda b=b: b.appearance.name)})
    return reached, not_reached, unverified


def handler(target: str = "", color: str = "", opacity: int = 255, name: str = "") -> dict:
    """Apply a solid-color appearance override to the resolved target. WRITES."""
    rgb, cerr = _parse_color(color)
    if cerr:
        return error(cerr)
    try:
        opacity = int(opacity)
    except (TypeError, ValueError):
        return error(f"'opacity' must be an integer, and only {_OPAQUE} (opaque) is accepted.")
    if opacity != _OPAQUE:
        return error(
            f"'opacity'={opacity} cannot be delivered by this tool, so it is refused rather than "
            f"reported as applied. Only opacity={_OPAQUE} is accepted. This tool sets an "
            "appearance's COLOR, and a Fusion appearance's transparency is its Prism material class "
            "(interior_model plus transparent_color / transparent_distance / transparent_ior), not "
            "the alpha of that color - measured, an OPAQUE appearance renders fully opaque at "
            "alpha 100. For a see-through body, assign a transparent appearance such as "
            "'Acrylic (Clear)' from the Fusion Appearance dialog.")

    design = _common.design()
    if not design:
        return error("No active design with geometry.")

    resolved, terr = _TARGET.resolve(target)
    if terr:
        return terr if isinstance(terr, dict) else error(terr)
    entity, kind = resolved
    if kind == "design":
        kind = "component"      # whole design = the root component; color all its bodies
    desc = (f"{kind} '{safe(lambda: entity.fullPathName) or safe(lambda: entity.name)}'"
            if safe(lambda: entity.name) else kind)

    # EVERY precondition the target has to clear runs BEFORE the appearance is minted: creating it
    # first leaves an ORPHAN appearance asset in the design each time a refusal below fires (measured:
    # an empty component's refusal took the design's appearance count 0 -> 1).
    bodies = None
    if kind == "component":
        bodies = safe(lambda: entity.bRepBodies)
        if (safe(lambda: bodies.count, 0) or 0) == 0:
            return error(f"{desc} has no bodies to color.")

    appr_name = (name or "").strip() or f"AgentColor_{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"
    appr, appr_reused, base_reused, aerr = _make_colored_appearance(design, rgb, opacity, appr_name)
    if aerr:
        return error(aerr)

    applied_to = []
    failed = []
    not_reached = []
    unverified = []
    # The two keys every read-back below compares against - read ONCE off the appearance this call
    # actually applied, so a later re-read of the object cannot drift the comparison.
    appr_id, appr_landed_name = safe(lambda: appr.id), safe(lambda: appr.name)
    if kind == "component":
        # a Component has no single .appearance; apply to each of its bodies (the collection was
        # resolved and counted above, before anything was created). A failure on one body must not
        # hide that other bodies already got colored - collect per-body, don't abort the loop.
        for b in _common.iter_collection(bodies):
            name = safe(lambda b=b: b.name)
            try:
                b.appearance = appr
            except Exception as e:
                failed.append({"body": name, "error": str(e)})
                continue
            if _reads_as(b, appr_id, appr_landed_name) is False:
                got = safe(lambda b=b: b.appearance.name)
                failed.append({"body": name, "error": f"appearance still reads '{got}' after the set"})
            else:
                applied_to.append(name)
        if not applied_to:
            return error(f"Could not apply appearance to any body of {desc}: "
                         f"{failed[0]['error'] if failed else 'unknown error'}.")
    else:
        # body / occurrence / face all carry a settable .appearance
        try:
            entity.appearance = appr
        except Exception as e:
            return error(f"Could not apply appearance to {desc}: {e}")
        if _reads_as(entity, appr_id, appr_landed_name) is False:
            got = safe(lambda: entity.appearance.name)
            return error(f"Assignment was accepted but {desc} still reads appearance '{got}' - "
                         "the override did not take.")
        # a BRepFace has no .name; fall back to the target description
        applied_to.append(safe(lambda: entity.name) or desc)
        if kind == "occurrence":
            # The occurrence read-back above agrees with what was just set even for a body the write
            # never reached - read the BODIES back to publish where the color actually landed.
            reached, not_reached, unverified = _occurrence_fanout(entity, appr_id,
                                                                  appr_landed_name)
            applied_to.extend(reached)
            # NOT ONE body took the appearance while at least one demonstrably kept another: the
            # occurrence's own read-back is the only thing that "succeeded", and it agrees with the
            # write whether or not anything changed. The component branch already errors here.
            if not reached and not_reached:
                names = ", ".join(o["body"] for o in not_reached[:5])
                return error(f"Assignment to {desc} reached NONE of its {len(not_reached)} "
                             f"body(ies) - each still reads a different appearance ({names}). "
                             "Color the bodies directly (target = the body name).")

    note = ("Appearance override applied. Set a new color anytime; to revert, the override is on "
            "the body/occurrence (.appearance). Pair with view_screenshot to see it.")
    if base_reused is False:
        note = (f"Copied '{_BASE_SOURCE}' from '{_BASE_LIBRARY}' into this document as "
                f"'{_BASE_NAME}' - the base every color override here is copied from. " + note)
    if kind in ("body", "component"):
        # LIVE-CONFIRMED: a body-level write reaches the NATIVE body even through an
        # instance-exact proxy handle, so the color shows on EVERY instance of that component. It
        # is not a per-instance act, and the payload has to say so before the caller assumes it was.
        note = ("This write landed on the BODY, which is the component's NATIVE body - the color "
                "shows on EVERY instance of that component, not just one. To color one instance, "
                "target the OCCURRENCE (its fullPathName). " + note)
    if failed:
        note = (f"Appearance applied to {len(applied_to)} of {len(applied_to) + len(failed)} bodies; "
                f"{len(failed)} failed - see 'failed'. " + note)
    if not_reached:
        note = (f"PARTIAL: {len(not_reached)} body(ies) of this occurrence do NOT carry the new "
                f"appearance ({', '.join(o['body'] for o in not_reached[:5])}) - each still reads the "
                "one named in 'bodies_not_reached'; color those directly (target = the body). " + note)
    if unverified:
        note = (f"{len(unverified)} body(ies) could not be compared, so the color is UNCONFIRMED "
                "there - see 'unverified_bodies'. " + note)

    result = {
        "applied": True,
        "target": desc,
        "kind": kind,
        "color_rgb": list(rgb),
        "color_hex": f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}",
        "opacity": opacity,
        "appearance": appr_landed_name,
        "appearance_reused": appr_reused,
        "applied_to": applied_to,
        "note": note,
    }
    if base_reused is not None:
        # Only a call that actually consulted the base publishes it. A call that reused the named
        # appearance never read what that one was copied from, and cannot say.
        result["base_appearance"] = _BASE_NAME
        result["base_reused"] = base_reused
    if failed:
        result["failed"] = failed
    if not_reached:
        result["bodies_not_reached"] = not_reached
    if unverified:
        result["unverified_bodies"] = unverified
    return ok(result)


_DESC = (
"Set the color/appearance of a FACE, body, occurrence, or component (all its bodies) as a revertible "
"override. 'target' = a find_geometry FACE handle (colors one face) or body, an occurrence name/"
"fullPath, a body name, or a component name (empty = whole design). 'color' = '#RRGGBB', 'RRGGBB', or "
"'r,g,b' (0-255). The color is written onto a copy of the Fusion Appearance Library's 'Paint - "
"Enamel Glossy (White)', kept in the document as 'MCP Neutral Base'. Color only - transparency is "
"an appearance's Prism material class, not its color alpha, so this tool cannot make a body "
"see-through. Pair with view_screenshot to verify."
)

tool = (
    Tool.create_simple(name="appearance_set", description=_DESC)
    .add_input_property(*_TARGET.as_property())
    .add_input_property("color", {"type": "string",
            "description": "Color as '#RRGGBB', 'RRGGBB', or 'r,g,b' (0-255 each)."})
    .add_input_property("opacity", {"type": "integer",
            "description": "Only 255 (opaque) is accepted - transparency is not the color's alpha."})
    .add_input_property("name", {"type": "string",
            "description": "Optional name for the created appearance."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
