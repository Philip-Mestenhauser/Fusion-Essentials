# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Create a 2D drawing document from the active design's saved cloud DataFile via the automatic
generator (CreateDrawingInput + its automationPreferences tree; only automatic creation exists).
The new drawing is a CLOUD file, returned as file_id (lineage URN) and NOT opened: a never-reviewed
auto-drawing surfaces an interactive view pane that blocks a headless open - review it once in the
Fusion UI first. WRITES a drawing.
"""

import adsk.core
import adsk.drawing

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe
from . import _common
from . import _outputs
from . import _inputs
from ._data_common import _resolve_data_file

app = adsk.core.Application.get()

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsUrn("file_id", consumers=["drawing_export", "doc_open", "data_get"]),
    _outputs.ReturnsName("drawing_name", of="drawing document"),
]

# ISO sizes only apply when standard=iso; ASME sizes only when standard=asme (the API silently ignores a
# mismatch, so this tool GUARDS it). value -> (required_standard, SheetSizes member name).
_SHEET_SIZE_MAP = {
    "a4": ("iso", "A4ISOSheetSize"), "a3": ("iso", "A3ISOSheetSize"),
    "a2": ("iso", "A2ISOSheetSize"), "a1": ("iso", "A1ISOSheetSize"), "a0": ("iso", "A0ISOSheetSize"),
    "a": ("asme", "AASMESheetSize"), "b": ("asme", "BASMESheetSize"), "c": ("asme", "CASMESheetSize"),
    "d": ("asme", "DASMESheetSize"), "e": ("asme", "EASMESheetSize"),
}
# Portrait is rejected by Fusion for the largest sheet of each standard (A0 ISO / E ASME).
_NO_PORTRAIT = {("iso", "a0"), ("asme", "e")}

_VIEW_STYLE_MAP = {
    "visible": "VisibleEdgesDrawingViewStyleType",
    "hidden": "VisibleAndHiddenEdgesDrawingViewStyleType",
    "shaded": "ShadedDrawingViewStyleType",
    "shaded_edges": "ShadedWithVisibleEdgesDrawingViewStyleType",
}
_DIM_STRATEGY_MAP = {
    "overall": "OverallDimensionStrategyType",
    "automatic": "AutomaticDimensionStrategyType",
    "baseline": "BaselineDimensionStrategyType",
    "chain": "ChainDimensionStrategyType",
}
# sheet-type name -> GlobalPreferences toggle property.
_SHEET_TYPE_ATTR = {
    "component": "isComponentSheetGenerated",
    "main_assembly": "isMainAssemblySheetGenerated",
    "sub_assembly": "isSubAssemblySheetGenerated",
    "flat_pattern": "isFlatPatternSheetGenerated",
    "folded_model": "isFoldedModelSheetGenerated",
    "animation": "isAnimationSheetGenerated",
}
# Sheet-type paths whose autoDimensionPreferences (strategy + hole-annotation) this tool sets.
_AUTODIM_PATHS = ("componentPreferences", "mainAssemblyPreferences", "subAssemblyPreferences",
                  "flatPatternPreferences")
# Sheet-type paths whose drawingViewPreferences (style + drafting-display) this tool sets.
_VIEWSTYLE_PATHS = ("componentPreferences", "mainAssemblyPreferences", "subAssemblyPreferences")

_TABLE_LOCATION_MAP = {
    "top_left": "TopLeftTableLocationType", "top_right": "TopRightTableLocationType",
    "bottom_left": "BottomLeftTableLocationType", "bottom_right": "BottomRightTableLocationType",
}
_HOLE_PREF_MAP = {
    "both": "HoleAndThreadNoteHolePreferencesType",
    "hole": "HoleNoteOnlyHolePreferencesType",
    "thread": "ThreadNoteOnlyHolePreferencesType",
    "none": "NoHoleAnnotationsHolePreferencesType",
}
_CENTER_LINE_MAP = {
    "off": "OffCenterLineDisplayType", "cylindrical": "AllCylindricalCenterLineDisplayType",
    "holes": "AllHolesCenterLineDisplayType",
}
_CENTER_MARK_MAP = {
    "off": "OffCenterMarkDisplayType", "holes": "AllHolesCenterMarkDisplayType",
    "fillets": "AllFilletsCenterMarkDisplayType", "edges": "AllCircularEdgesCenterMarkDisplayType",
    "punches": "AllPunchesCenterMarkDisplayType",
}
_TANGENT_EDGE_MAP = {
    "off": "OffTangentEdgeDisplayType", "on": "OnTangentEdgeDisplayType",
    "partial": "ForeshortenedTangentEdgeDisplayType",
}
_STANDARD = _inputs.Choice("standard", ["iso", "asme"], default="iso",
                           description="ISO (first-angle) or ASME (third-angle).")
_UNITS = _inputs.Choice("units", ["mm", "inch"], default="mm",
                        description="Dimension display units.")
_CONTENT = _inputs.Choice("content", ["full", "visible"], default="full",
                          description="Full assembly, or visible-only.")
_SHEET_SIZE = _inputs.Choice("sheet_size",
                             ["default", "a4", "a3", "a2", "a1", "a0", "a", "b", "c", "d", "e", "custom"],
                             default="default",
                             description="Preset (must match standard) or 'custom' + custom_width_mm/"
                                         "custom_height_mm.")
_ORIENTATION = _inputs.Choice("orientation", ["landscape", "portrait"], default="landscape",
                              description="No portrait on A0 ISO / E ASME.")
_SHEET_SCOPE = _inputs.Choice("sheet_scope", ["all_levels", "first_level"], default="all_levels",
                              description="All levels, or first-level only.")
_AUTO_DIMENSION = _inputs.Choice("auto_dimension", ["default", "off", "overall", "automatic", "baseline", "chain"],
                                 default="default",
                                 description="'off' disables it; else sets placement. Default: on, overall.")
_VIEW_STYLE = _inputs.Choice("view_style", ["default", "visible", "hidden", "shaded", "shaded_edges"],
                             default="default",
                             description="View rendering style.")
_PARTS_LIST_LOCATION = _inputs.Choice("parts_list_location",
                             ["default", "top_left", "top_right", "bottom_left", "bottom_right"],
                             default="default",
                             description="BOM table corner.")
_HOLE_ANNOTATIONS = _inputs.Choice("hole_annotations",
                             ["default", "both", "hole", "thread", "none"],
                             default="default",
                             description="Hole/thread callout style.")
_CENTER_LINE = _inputs.Choice("center_line", ["default", "off", "cylindrical", "holes"], default="default",
                             description="Center lines on generated views.")
_CENTER_MARK = _inputs.Choice("center_mark",
                             ["default", "off", "holes", "fillets", "edges", "punches"],
                             default="default",
                             description="Center marks.")
_TANGENT_EDGES = _inputs.Choice("tangent_edges", ["default", "off", "on", "partial"], default="default",
                             description="Tangent-edge display.")


def _source_datafile(design):
    """The cloud DataFile the drawing is generated from, or (None, error). Automatic drawing creation
    needs a saved cloud source design; an unsaved design has no DataFile to draw from."""
    doc = safe(lambda: design.parentDocument) or safe(lambda: app.activeDocument)
    df = safe(lambda: doc.dataFile)
    if not df:
        return None, ("The active design has not been saved to the cloud, so it has no DataFile to "
                      "draw from. Automatic drawing creation needs a cloud source design - save it "
                      "first with doc_save_as, then retry.")
    return df, None


def _apply_input_settings(di, cfg, template_data_file=None):
    """Best-effort configuration of the CreateDrawingInput + its automationPreferences tree. Each setter
    is wrapped in safe() (a property missing on this Fusion version must not sink the create); the
    requested values are echoed to the caller as 'settings_requested' rather than read back.
    'template_data_file' is a resolved DataFile (not JSON-safe, so it stays out of cfg)."""
    d = adsk.drawing
    safe(lambda: setattr(di, "standard",
         d.DrawingStandardTypes.ASMEDrawingStandardType if cfg["standard"] == "asme"
         else d.DrawingStandardTypes.ISODrawingStandardType))
    safe(lambda: setattr(di, "units",
         d.DrawingUnitTypes.InchDrawingUnitType if cfg["units"] == "inch"
         else d.DrawingUnitTypes.MillimeterDrawingUnitType))
    safe(lambda: setattr(di, "content",
         d.DrawingContentTypes.VisibleOnlyDrawingContentType if cfg["content"] == "visible"
         else d.DrawingContentTypes.FullAssemblyDrawingContentType))
    if cfg["sheet_size"] == "custom":
        safe(lambda: setattr(di, "sheetSize", d.SheetSizes.CustomSizeSheetSize))
        cs = safe(lambda: di.customSize)
        if cs is not None:
            safe(lambda: setattr(cs, "width", cfg["custom_width_cm"]))
            safe(lambda: setattr(cs, "height", cfg["custom_height_cm"]))
    elif cfg["sheet_size"] != "default":
        member = _SHEET_SIZE_MAP[cfg["sheet_size"]][1]
        safe(lambda m=member: setattr(di, "sheetSize", getattr(d.SheetSizes, m)))
    safe(lambda: setattr(di, "orientationType",
         d.SheetOrientationTypes.PortraitSheetOrientationType if cfg["orientation"] == "portrait"
         else d.SheetOrientationTypes.LandscapeSheetOrientationType))
    safe(lambda: setattr(di, "sheetCreationType",
         d.SheetCreationTypes.FirstLevelOnlySheetCreationType if cfg["sheet_scope"] == "first_level"
         else d.SheetCreationTypes.AllLevelsSheetCreationType))

    # Only touch baseDocumentType/templateFile when a template was resolved.
    if template_data_file is not None:
        safe(lambda: setattr(di, "baseDocumentType", d.BaseDocumentTypes.FromTemplateBaseDocumentType))
        safe(lambda: setattr(di, "templateFile", template_data_file))

    gp = safe(lambda: di.automationPreferences.globalPreferences)
    if gp is not None:
        if cfg["sheet_types"] is not None:
            want = set(cfg["sheet_types"])
            for key, attr in _SHEET_TYPE_ATTR.items():
                safe(lambda a=attr, k=key: setattr(gp, a, k in want))
        if cfg["auto_dimension"] == "off":
            safe(lambda: setattr(gp, "isAutoDimensionEnabled", False))
        elif cfg["auto_dimension"] != "default":
            safe(lambda: setattr(gp, "isAutoDimensionEnabled", True))
        safe(lambda: setattr(gp, "isDetectAndOmitFasteners", bool(cfg["omit_fasteners"])))
        if cfg["fastener_keywords"]:
            safe(lambda: setattr(gp, "omitComponentsWithKeywords", cfg["fastener_keywords"]))

    # Apply the requested strategy/hole-annotation to every sheet type's autoDimensionPreferences, not
    # just componentPreferences.
    strat_member = _DIM_STRATEGY_MAP.get(cfg["auto_dimension"])
    hole_member = _HOLE_PREF_MAP.get(cfg["hole_annotations"])
    if strat_member or hole_member:
        for path in _AUTODIM_PATHS:
            prefs = safe(lambda p=path: getattr(di.automationPreferences, p))
            node = safe(lambda pr=prefs: pr.autoDimensionPreferences) if prefs is not None else None
            if node is None:
                continue
            if strat_member:
                safe(lambda n=node, m=strat_member: setattr(
                    n, "dimensionStrategyType", getattr(d.DimensionStrategyTypes, m)))
            if hole_member:
                safe(lambda n=node, m=hole_member: setattr(
                    n, "holePreferencesType", getattr(d.HolePreferencesTypes, m)))

    # Parts-list (BOM) inclusion/placement on both main- and sub-assembly sheet prefs (iso + orthogonal).
    loc_member = _TABLE_LOCATION_MAP.get(cfg["parts_list_location"])
    if cfg["parts_list"] is not None or loc_member:
        for path in ("mainAssemblyPreferences", "subAssemblyPreferences"):
            prefs = safe(lambda p=path: getattr(di.automationPreferences, p))
            if prefs is None:
                continue
            for sheet_kind in ("isoViewSheetPreferences", "orthogonalViewSheetPreferences"):
                node = safe(lambda pr=prefs, sk=sheet_kind: getattr(pr, sk))
                if node is None:
                    continue
                if cfg["parts_list"] is not None:
                    safe(lambda n=node: setattr(n, "isPartsListIncluded", cfg["parts_list"]))
                if loc_member:
                    safe(lambda n=node, m=loc_member: setattr(
                        n, "partsListLocationType", getattr(d.TableLocationTypes, m)))

    # Per-view drafting display, on the same objects already reached for .style.
    style_member = _VIEW_STYLE_MAP.get(cfg["view_style"])
    cl_member = _CENTER_LINE_MAP.get(cfg["center_line"])
    cmk_member = _CENTER_MARK_MAP.get(cfg["center_mark"])
    te_member = _TANGENT_EDGE_MAP.get(cfg["tangent_edges"])
    if (style_member or cl_member or cmk_member or te_member
            or cfg["show_interference_edges"] is not None or cfg["show_thread_edges"] is not None):
        for path in _VIEWSTYLE_PATHS:
            node = safe(lambda p=path: getattr(di.automationPreferences, p).drawingViewPreferences)
            if node is None:
                continue
            if style_member:
                safe(lambda n=node, m=style_member: setattr(n, "style", getattr(d.DrawingViewStyleTypes, m)))
            if cl_member:
                safe(lambda n=node, m=cl_member: setattr(
                    n, "centerLineType", getattr(d.CenterLineDisplayTypes, m)))
            if cmk_member:
                safe(lambda n=node, m=cmk_member: setattr(
                    n, "centerMarkType", getattr(d.CenterMarkDisplayTypes, m)))
            if te_member:
                safe(lambda n=node, m=te_member: setattr(
                    n, "tangentEdgesType", getattr(d.TangentEdgeDisplayTypes, m)))
            if cfg["show_interference_edges"] is not None:
                safe(lambda n=node: setattr(n, "isShowInterferenceEdges", cfg["show_interference_edges"]))
            if cfg["show_thread_edges"] is not None:
                safe(lambda n=node: setattr(n, "isShowThreadEdges", cfg["show_thread_edges"]))

    # Optional isometric view alongside the orthographic set on each component sheet.
    safe(lambda: setattr(
        di.automationPreferences.componentPreferences.sheetViewPreferences,
        "isIsometricViewAdded", bool(cfg["isometric"])))


def handler(standard: str = "iso", units: str = "mm", content: str = "full", isometric: bool = True,
            sheet_size: str = "default", orientation: str = "landscape", sheet_scope: str = "all_levels",
            sheet_types=None, auto_dimension: str = "default", omit_fasteners: bool = False,
            fastener_keywords: str = "", view_style: str = "default", parts_list: bool = None,
            parts_list_location: str = "default", template_file: str = "",
            custom_width_mm: float = None, custom_height_mm: float = None,
            hole_annotations: str = "default", center_line: str = "default",
            center_mark: str = "default", tangent_edges: str = "default",
            show_interference_edges: bool = None, show_thread_edges: bool = None) -> dict:
    """See TOOL_DESCRIPTION."""
    std, e = _STANDARD.resolve(standard)
    if e:
        return error(e)
    units_v, e = _UNITS.resolve(units)
    if e:
        return error(e)
    content_v, e = _CONTENT.resolve(content)
    if e:
        return error(e)
    size_v, e = _SHEET_SIZE.resolve(sheet_size)
    if e:
        return error(e)
    orient_v, e = _ORIENTATION.resolve(orientation)
    if e:
        return error(e)
    scope_v, e = _SHEET_SCOPE.resolve(sheet_scope)
    if e:
        return error(e)
    dim_v, e = _AUTO_DIMENSION.resolve(auto_dimension)
    if e:
        return error(e)
    style_v, e = _VIEW_STYLE.resolve(view_style)
    if e:
        return error(e)
    loc_v, e = _PARTS_LIST_LOCATION.resolve(parts_list_location)
    if e:
        return error(e)
    hole_v, e = _HOLE_ANNOTATIONS.resolve(hole_annotations)
    if e:
        return error(e)
    cl_v, e = _CENTER_LINE.resolve(center_line)
    if e:
        return error(e)
    cmk_v, e = _CENTER_MARK.resolve(center_mark)
    if e:
        return error(e)
    te_v, e = _TANGENT_EDGES.resolve(tangent_edges)
    if e:
        return error(e)

    # Guard the two real constraints the API silently ignores rather than reports.
    if size_v not in ("default", "custom"):
        need_std = _SHEET_SIZE_MAP[size_v][0]
        if need_std != std:
            fam = [k for k, v in _SHEET_SIZE_MAP.items() if v[0] == std]
            return error(f"sheet_size '{size_v}' is a {need_std.upper()} size but standard is '{std}'. "
                         f"Use an {std.upper()} size ({', '.join(fam)}) or switch the standard.")
        if orient_v == "portrait" and (std, size_v) in _NO_PORTRAIT:
            return error(f"portrait orientation is not supported for the largest {std.upper()} sheet "
                         f"('{size_v}'); use landscape or a smaller sheet.")

    custom_w_cm = custom_h_cm = None
    if size_v == "custom":
        if custom_width_mm is None or custom_height_mm is None:
            return error("sheet_size 'custom' requires both custom_width_mm and custom_height_mm.")
        try:
            w_mm, h_mm = float(custom_width_mm), float(custom_height_mm)
        except (TypeError, ValueError):
            return error(f"custom_width_mm and custom_height_mm must be numbers (got "
                         f"{custom_width_mm!r} / {custom_height_mm!r}).")
        if w_mm <= 0 or h_mm <= 0:
            return error(f"custom_width_mm and custom_height_mm must be positive (got {w_mm} / {h_mm}).")
        custom_w_cm = w_mm * _common.scale("mm")
        custom_h_cm = h_mm * _common.scale("mm")
    elif custom_width_mm is not None or custom_height_mm is not None:
        return error("custom_width_mm/custom_height_mm only apply when sheet_size='custom'.")

    template_df = None
    tf_raw = (template_file or "").strip()
    if tf_raw:
        template_df, _resolved_tf, tried = _resolve_data_file(tf_raw)
        if not template_df:
            tried_s = ", ".join(tried) if tried else tf_raw
            return error(f"template_file '{tf_raw}' could not be resolved to a file. Tried: {tried_s}. "
                         "Pass a DataFile id/versionId or a fusionWebURL from data_get / "
                         "design_get(include=['tree']).")

    types_v = None
    if sheet_types is not None:
        if not isinstance(sheet_types, (list, tuple)):
            return error("sheet_types must be a list of sheet-type names (e.g. ['component', "
                         "'main_assembly']).")
        bad = [t for t in sheet_types if t not in _SHEET_TYPE_ATTR]
        if bad:
            return error(f"sheet_types has unknown value(s) {bad}. Allowed: "
                         f"{', '.join(_SHEET_TYPE_ATTR)}.")
        types_v = list(sheet_types)

    design = _common.design()
    if not design:
        return error("No active design to draw. Open or create a design first (see doc_new), then retry.")

    src, serr = _source_datafile(design)
    if serr:
        return error(serr)

    dm = safe(lambda: adsk.drawing.DrawingManager.get())
    if not dm:
        return error("DrawingManager is unavailable in this Fusion session - cannot create a drawing.")

    try:
        di = dm.createDrawingInput(src, adsk.drawing.DrawingCreationModes.AutomaticDrawingCreationMode)
    except Exception as ex:
        return error(f"createDrawingInput failed: {ex}")
    if not di:
        return error("createDrawingInput returned null - Fusion could not start a drawing from this design.")

    cfg = {
        "standard": std, "units": units_v, "content": content_v, "isometric": bool(isometric),
        "sheet_size": size_v, "orientation": orient_v, "sheet_scope": scope_v,
        "sheet_types": types_v, "auto_dimension": dim_v, "omit_fasteners": bool(omit_fasteners),
        "fastener_keywords": (fastener_keywords or "").strip(), "view_style": style_v,
        "parts_list": (bool(parts_list) if parts_list is not None else None),
        "parts_list_location": loc_v,
        "template_file": tf_raw,
        "custom_width_mm": custom_width_mm, "custom_height_mm": custom_height_mm,
        "custom_width_cm": custom_w_cm, "custom_height_cm": custom_h_cm,
        "hole_annotations": hole_v, "center_line": cl_v, "center_mark": cmk_v, "tangent_edges": te_v,
        "show_interference_edges": (bool(show_interference_edges) if show_interference_edges is not None
                                     else None),
        "show_thread_edges": (bool(show_thread_edges) if show_thread_edges is not None else None),
    }
    _apply_input_settings(di, cfg, template_data_file=template_df)

    try:
        df = dm.createDrawing(di)
    except Exception as ex:
        return error(f"createDrawing failed: {ex}")
    if not df:
        return error("createDrawing returned null - Fusion did not generate a drawing (nothing created).")

    file_id = safe(lambda: df.id)
    if not file_id:
        return error("createDrawing returned a drawing DataFile but no file_id could be read from it, "
                     "so the created drawing cannot be located for export. Treating this as a failure.")

    return ok({
        "created": True,
        "drawing_name": safe(lambda: df.name),
        "file_id": file_id,
        "version_id": safe(lambda: df.versionId),
        "file_extension": safe(lambda: df.fileExtension),
        "settings_requested": cfg,
        "note": ("Drawing created as a CLOUD file (NOT opened). Opening a never-reviewed auto-drawing "
                 "surfaces an interactive view pane that blocks a headless open - open it ONCE in the "
                 "Fusion UI to review the auto-layout and save; after that doc_open and drawing_export "
                 "(PDF) work headlessly. settings_requested were applied best-effort to the generator "
                 "(they configure generation and are not read back). Manual dimensions/annotations and "
                 "custom title blocks beyond the automatic layout are not placed by this tool."),
    })


TOOL_DESCRIPTION = (
    "Create a 2D drawing from the active design via Fusion's automatic generator (the only "
    "creation mode the API supports). Configures standard/units/content, sheet size/orientation/"
    "scope/types, auto-dimensioning + hole/thread annotation style (all sheet types), fastener "
    "omission, per-view drafting display, an isometric view, an assembly parts list (BOM), and an "
    "optional create-from-template mode. Source design must be cloud-saved. Result is a CLOUD "
    "file, NOT opened - file_id (lineage URN) returned. Open it ONCE in the Fusion UI before "
    "doc_open/drawing_export can run headlessly (an unreviewed auto-drawing blocks headless open). "
    "Per-view placement/scale is not API-controllable. This call can run long; it waits rather "
    "than timing out falsely."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_create", description=FULL_DESCRIPTION)
    .add_input_property(*_STANDARD.as_property())
    .add_input_property(*_UNITS.as_property())
    .add_input_property(*_CONTENT.as_property())
    .add_input_property("isometric", {"type": "boolean",
            "description": "Add an isometric view (default true)."})
    .add_input_property(*_SHEET_SIZE.as_property())
    .add_input_property(*_ORIENTATION.as_property())
    .add_input_property(*_SHEET_SCOPE.as_property())
    .add_input_property("sheet_types", {"type": "array",
            "items": {"type": "string", "enum": list(_SHEET_TYPE_ATTR)},
            "description": "Enabled sheet kinds (others disabled)."})
    .add_input_property(*_AUTO_DIMENSION.as_property())
    .add_input_property("omit_fasteners", {"type": "boolean",
            "description": "Auto-detect and omit fastener components (default false)."})
    .add_input_property("fastener_keywords", {"type": "string",
            "description": "Comma-separated fastener-omission keywords."})
    .add_input_property(*_VIEW_STYLE.as_property())
    .add_input_property("parts_list", {"type": "boolean",
            "description": "Include a parts list (BOM) on assembly sheets."})
    .add_input_property(*_PARTS_LIST_LOCATION.as_property())
    .add_input_property("template_file", {"type": "string",
            "description": "DataFile id/URL for a drawing template (doc_open idiom); empty = scratch."})
    .add_input_property("custom_width_mm", {"type": "number",
            "description": "Sheet width in mm (needs sheet_size='custom')."})
    .add_input_property("custom_height_mm", {"type": "number",
            "description": "Sheet height in mm (needs sheet_size='custom')."})
    .add_input_property(*_HOLE_ANNOTATIONS.as_property())
    .add_input_property(*_CENTER_LINE.as_property())
    .add_input_property(*_CENTER_MARK.as_property())
    .add_input_property(*_TANGENT_EDGES.as_property())
    .add_input_property("show_interference_edges", {"type": "boolean",
            "description": "Show interference edges on generated views."})
    .add_input_property("show_thread_edges", {"type": "boolean",
            "description": "Show thread edges on generated views."})
    .strict_schema()
)

# enforce_timeout=False: createDrawing is a blocking, uninterruptible main-thread call that generates
# views and commits a cloud DataFile - it can run past the server's call timeout even for a small
# design. Timing it out would report a false failure for a drawing that WAS created, so this tool waits
# for it (like sys_execute_script) rather than false-failing.
item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             enforce_timeout=False)


def register_tool():
    register(item)
