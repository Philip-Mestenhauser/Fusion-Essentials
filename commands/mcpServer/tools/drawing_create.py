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

_STANDARD = _inputs.Choice("standard", ["iso", "asme"], default="iso",
                           description="Drafting standard: ISO (first-angle) or ASME (third-angle).")
_UNITS = _inputs.Choice("units", ["mm", "inch"], default="mm",
                        description="Dimension display units for the drawing.")
_CONTENT = _inputs.Choice("content", ["full", "visible"], default="full",
                          description="Include the full assembly, or only currently-visible bodies/components.")
_SHEET_SIZE = _inputs.Choice("sheet_size", ["default", "a4", "a3", "a2", "a1", "a0", "a", "b", "c", "d", "e"],
                             default="default",
                             description="Sheet size. ISO sizes a4-a0 require standard=iso; ASME sizes a-e "
                                         "require standard=asme. 'default' lets Fusion pick (A3 ISO / A ASME).")
_ORIENTATION = _inputs.Choice("orientation", ["landscape", "portrait"], default="landscape",
                              description="Sheet orientation. Portrait is not available for the largest "
                                          "sheet (A0 ISO / E ASME).")
_SHEET_SCOPE = _inputs.Choice("sheet_scope", ["all_levels", "first_level"], default="all_levels",
                              description="Generate sheets for all hierarchy levels, or first-level "
                                          "components only (smaller file).")
_AUTO_DIMENSION = _inputs.Choice("auto_dimension", ["default", "off", "overall", "automatic", "baseline", "chain"],
                                 default="default",
                                 description="Auto-dimensioning: 'off' disables it; a strategy name enables "
                                             "it with that placement strategy; 'default' keeps Fusion's "
                                             "default (on, overall).")
_VIEW_STYLE = _inputs.Choice("view_style", ["default", "visible", "hidden", "shaded", "shaded_edges"],
                             default="default",
                             description="Rendering style for generated views. 'default' keeps visible-edges.")


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


def _apply_input_settings(di, cfg):
    """Best-effort configuration of the CreateDrawingInput + its automationPreferences tree. Each setter
    is wrapped in safe() (a property missing on this Fusion version must not sink the create); the
    requested values are echoed to the caller as 'settings_requested' rather than read back."""
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
    if cfg["sheet_size"] != "default":
        member = _SHEET_SIZE_MAP[cfg["sheet_size"]][1]
        safe(lambda m=member: setattr(di, "sheetSize", getattr(d.SheetSizes, m)))
    safe(lambda: setattr(di, "orientationType",
         d.SheetOrientationTypes.PortraitSheetOrientationType if cfg["orientation"] == "portrait"
         else d.SheetOrientationTypes.LandscapeSheetOrientationType))
    safe(lambda: setattr(di, "sheetCreationType",
         d.SheetCreationTypes.FirstLevelOnlySheetCreationType if cfg["sheet_scope"] == "first_level"
         else d.SheetCreationTypes.AllLevelsSheetCreationType))

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
            member = _DIM_STRATEGY_MAP[cfg["auto_dimension"]]
            safe(lambda m=member: setattr(
                di.automationPreferences.componentPreferences.autoDimensionPreferences,
                "dimensionStrategyType", getattr(d.DimensionStrategyTypes, m)))
        safe(lambda: setattr(gp, "isDetectAndOmitFasteners", bool(cfg["omit_fasteners"])))
        if cfg["fastener_keywords"]:
            safe(lambda: setattr(gp, "omitComponentsWithKeywords", cfg["fastener_keywords"]))

    if cfg["view_style"] != "default":
        member = _VIEW_STYLE_MAP[cfg["view_style"]]
        for path in ("componentPreferences", "mainAssemblyPreferences", "subAssemblyPreferences"):
            safe(lambda p=path, m=member: setattr(
                getattr(di.automationPreferences, p).drawingViewPreferences,
                "style", getattr(d.DrawingViewStyleTypes, m)))

    # Optional isometric view alongside the orthographic set on each component sheet.
    safe(lambda: setattr(
        di.automationPreferences.componentPreferences.sheetViewPreferences,
        "isIsometricViewAdded", bool(cfg["isometric"])))


def handler(standard: str = "iso", units: str = "mm", content: str = "full", isometric: bool = True,
            sheet_size: str = "default", orientation: str = "landscape", sheet_scope: str = "all_levels",
            sheet_types=None, auto_dimension: str = "default", omit_fasteners: bool = False,
            fastener_keywords: str = "", view_style: str = "default") -> dict:
    """Create an automatic 2D drawing from the active design, configuring the auto-generator."""
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

    # Guard the two real constraints the API silently ignores rather than reports.
    if size_v != "default":
        need_std = _SHEET_SIZE_MAP[size_v][0]
        if need_std != std:
            fam = [k for k, v in _SHEET_SIZE_MAP.items() if v[0] == std]
            return error(f"sheet_size '{size_v}' is a {need_std.upper()} size but standard is '{std}'. "
                         f"Use an {std.upper()} size ({', '.join(fam)}) or switch the standard.")
        if orient_v == "portrait" and (std, size_v) in _NO_PORTRAIT:
            return error(f"portrait orientation is not supported for the largest {std.upper()} sheet "
                         f"('{size_v}'); use landscape or a smaller sheet.")

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
    }
    _apply_input_settings(di, cfg)

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
    "Create a 2D DRAWING document from the active design - automatic drawing creation (the only mode the "
    "API supports). Configures the auto-generator: 'standard' (ISO first-angle / ASME third-angle), "
    "'units', 'content' (full assembly or visible-only), 'sheet_size', 'orientation', 'sheet_scope' "
    "(first-level components only or all levels), 'sheet_types' (which sheet kinds to generate), "
    "'auto_dimension' (off or a placement strategy), 'omit_fasteners' (+ 'fastener_keywords'), "
    "'view_style', and 'isometric' (add an iso view). The source design MUST be saved to the cloud (it "
    "is drawn from its DataFile) - an unsaved design is refused. The drawing is created as a CLOUD file "
    "and is NOT opened; its file_id (lineage URN) is returned. IMPORTANT: opening a never-reviewed "
    "auto-drawing surfaces an interactive view pane that blocks a headless open - open it ONCE in the "
    "Fusion UI to review and save, after which doc_open / drawing_export work headlessly. Per-view "
    "orientation/scale and hand-placed views are NOT API-controllable. Generation blocks and can exceed "
    "a normal call, so this tool waits it out instead of false-failing on a timeout. WRITES a new "
    "drawing document."
)

FULL_DESCRIPTION = TOOL_DESCRIPTION + "\n" + _outputs.produces_block(RETURNS)

tool = (
    Tool.create_simple(name="drawing_create", description=FULL_DESCRIPTION)
    .add_input_property(*_STANDARD.as_property())
    .add_input_property(*_UNITS.as_property())
    .add_input_property(*_CONTENT.as_property())
    .add_input_property("isometric", {"type": "boolean",
            "description": "Add an isometric view alongside the orthographic views (default true)."})
    .add_input_property(*_SHEET_SIZE.as_property())
    .add_input_property(*_ORIENTATION.as_property())
    .add_input_property(*_SHEET_SCOPE.as_property())
    .add_input_property("sheet_types", {"type": "array",
            "items": {"type": "string", "enum": list(_SHEET_TYPE_ATTR)},
            "description": "Which sheet kinds to generate; only the listed kinds are enabled and the "
                           "rest disabled. Omit to keep Fusion defaults (all except animation)."})
    .add_input_property(*_AUTO_DIMENSION.as_property())
    .add_input_property("omit_fasteners", {"type": "boolean",
            "description": "Auto-detect and omit fastener components from the drawing (default false)."})
    .add_input_property("fastener_keywords", {"type": "string",
            "description": "Comma-separated name keywords for fastener omission (used with "
                           "omit_fasteners). Empty keeps Fusion's default set (Bolt,Screw,Nut,Washer)."})
    .add_input_property(*_VIEW_STYLE.as_property())
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
