# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""RICH READ: sys_get_preferences - the APPLICATION's preferences (app.preferences) by group.

Default: the settings that change how other tools behave (versioning, modelling orientation,
units, display precision); include=[...] pulls one group at a time. Some members RAISE on read on
a given build, so every member is read through safe() and an unreadable one is published null +
counted. This module also owns the TIER table sys_set_preferences enforces.
"""

from collections import namedtuple

import adsk.core
import adsk.fusion

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import ok, error, safe, iter_collection
from . import _outputs

app = adsk.core.Application.get()

# What this tool RETURNS (declared once; drives the PRODUCES: prose + the assert-present contract test).
RETURNS = [
    _outputs.ReturnsValue("preferences", "each group's members as {value, tier}",
                          consumers=["sys_set_preferences"]),
]

# The two tiers sys_set_preferences acts on. R is refused there, quoting the member's reason.
TIER_REFUSED = "R"
TIER_WRITABLE = "W"

# name    the API property on the group object
# tier    R (sys_set_preferences refuses it) or W
# reason  why an R member is refused - quoted verbatim in the refusal
# enum    the adsk enum family an int member decodes through (None for bool/int/float/str)
# by_name the member holds an OBJECT, published by its .name (there is no scalar to publish)
# minimum the smallest legal value the binding states for this member (None = unconstrained)
Member = namedtuple("Member", "name tier reason enum by_name minimum")
Member.__new__.__defaults__ = (TIER_WRITABLE, "", None, False, None)


def _family(name):
    """The enum family by name from adsk.core or adsk.fusion, or None when this build carries
    neither. Resolved defensively: a family this build lacks degrades to the raw int, rather than
    raising at import and taking the whole module's registration down with it."""
    return getattr(adsk.core, name, None) or getattr(adsk.fusion, name, None)


_HOST = ("it governs the scripting host this server runs in - a wrong value can leave the server "
         "unreachable, with no API left to undo it")
_DRIVER = ("a rendering driver this machine rejects can leave Fusion unrenderable, and the enum "
           "advertises drivers this machine rejects - the API cannot enumerate the legal subset")
_TRANSFER = ("it reroutes the cloud upload/download path the whole data_* and doc_* surface runs on")

# The app.preferences census behind this table is a dir() walk of every group with each member read
# once. What its committed record (the live census, in git history) holds is the group list - eleven
# groups, of which this table addresses ten - plus the three members that RAISE on read and the
# three that read non-scalar; the full member map stayed in that probe's script output. So the rows
# below are not backed one-by-one by a committed artifact, and the read does not rely on them being:
# a member this build does not carry is published as unknown_member (see read_member), never as an
# unreadable platform member, so a wrong row here can never masquerade as a fact about Fusion. The
# table is still the read's whole surface AND the write's tier authority, so both tools agree by
# construction.
FLAT_GROUPS = (
    ("general", "generalPreferences", (
        Member("isAutomaticVersioningEnabled"),
        Member("automateVersioningTimeInterval"),
        Member("isAutomaticSaveOnCloseEnabled"),
        Member("defaultModelingOrientation", enum=_family("DefaultModelingOrientations")),
        Member("defaultOrbit", enum=_family("DefaultOrbits")),
        Member("panZoomOrbitShortcuts", enum=_family("PanZoomOrbitShortcuts")),
        Member("userInterfaceTheme", enum=_family("UserInterfaceThemes")),
        Member("activeUserInterfaceTheme", TIER_REFUSED,
               "the binding exposes it read-only - set userInterfaceTheme instead",
               enum=_family("UserInterfaceThemes")),
        Member("graphicsDriver", TIER_REFUSED, _DRIVER, enum=_family("GraphicsDrivers")),
        Member("userLanguage", TIER_REFUSED,
               "it changes the UI language, and recovering means reading a UI the operator may not "
               "read", enum=_family("UserLanguages")),
        Member("areTooltipsShown"),
        Member("areTipsAndTricksShown"),
        Member("areInCommandErrorsAndWarningsShown"),
        Member("areAutodesk360NotificationsShown"),
        Member("isCommandPromptShown"),
        Member("isDefaultMeasureShown"),
        Member("isCameraPivotEnabled"),
        Member("isGestureBasedViewNavigationUsed"),
        Member("isZoomDirectionReversed"),
        Member("isSkipCreationWhenLiveUpdate"),
        Member("isHangDetectionEnabled"),
        Member("offlineCachePeriod"),
    )),
    ("display", "unitAndValuePreferences", (
        Member("generalPrecision"),
        Member("angularPrecision"),
        Member("isPeriodDecimalPoint"),
        Member("scientificNotationPrecision"),
        Member("isScientificNotationUsed"),
        Member("useScientficNotationAbove"),          # the binding's own spelling
        Member("useScientficNotationBelow"),
        Member("areTrailingZerosHidden"),
        Member("minimumPrecisionWhenHidingZeros"),
        Member("areAbbreviationsForUnitDisplayed"),
        Member("areSymbolsForUnitDisplayed"),
        Member("footAndInchDisplayFormat", enum=_family("FootAndInchDisplayFormats")),
        Member("degreeDisplayFormat", enum=_family("DegreeDisplayFormats")),
        Member("materialDisplayUnit", enum=_family("MaterialDisplayUnits")),
    )),
    # Measured raising members on this build: autoThrottleEffects, degradedSelectionDisplayStyle and
    # isLimitEffectsDuringNavigation all raise RuntimeError on READ, which is why every member read
    # here goes through safe() and reports unreadable rather than a value.
    ("graphics", "graphicsPreferences", (
        Member("graphicsPreset", enum=_family("GraphicsPresets")),
        Member("minimumFramesPerSecond"),
        Member("hiddenEdgeDimming"),
        Member("selectionDisplayStyle", enum=_family("SelectionDisplayStyles")),
        Member("degradedSelectionDisplayStyle", enum=_family("DegradedSelectionDisplayStyles")),
        Member("transparencyEffects", enum=_family("TransparencyDisplayEffects")),
        Member("autoThrottleEffects"),
        Member("isLimitEffectsDuringNavigation"),
        Member("isSurfaceNormalDisplayDisabled"),
        Member("isWoodBumpEnabled"),
        Member("isDynamic"),
        Member("isAnimateViewTransitions"),
        Member("isHighResolutionCanvasGraphicsEnabled"),
    )),
    ("compatibility", "compatibilityPreferences", (
        Member("recoverSaveScanFrequency", minimum=1),   # the binding states a value greater than 0
        Member("isEventPerformanceLogged"),
        Member("isLogHTTPRequestAndResponseBodies", TIER_REFUSED,
               "it writes the BODIES of the application's cloud HTTP traffic to a log, and that "
               "traffic carries the owner's credentials and tokens"),
        Member("isCacheGraphicsOnDocumentSave"),
        Member("isUseLatestSpaceMouseDriver", TIER_REFUSED,
               "it selects the driver for an attached 3D input device, which can leave the "
               "operator's hardware dead until a restart"),
        Member("isWindowParentingForPalettesEnforced"),
        Member("qtRenderingInterface", TIER_REFUSED, _DRIVER, enum=_family("GraphicsDrivers")),
        Member("chromiumGraphicsBackend", TIER_REFUSED, _DRIVER, enum=_family("GraphicsDrivers")),
        Member("isOverrideChromiumGPUWorkarounds", TIER_REFUSED,
               "it overrides the workarounds Fusion applies for known graphics-driver bugs"),
        Member("isHighDPIScaling", TIER_REFUSED,
               "it can render the UI unusable on a HiDPI display, which is not recoverable from here"),
        Member("isAcceleratedDataTransfer", TIER_REFUSED, _TRANSFER),
        Member("isCompatibleLegacyDataTransfer", TIER_REFUSED, _TRANSFER),
    )),
    ("api", "apiPreferences", (
        Member("defaultScriptLanguage", enum=_family("ProgrammingLanguages")),
        Member("defaultAddInLanguage", enum=_family("ProgrammingLanguages")),
        Member("defaultPathForScriptsAndAddIns", TIER_REFUSED,
               "it relocates where Fusion looks for add-ins - that is where this server lives"),
        Member("debuggingPort", TIER_REFUSED, _HOST),
        Member("isDeveloperToolsEnabled", TIER_REFUSED, _HOST),
    )),
    ("network", "networkPreferences", (
        Member("networkProxySetting", TIER_REFUSED,
               "it severs cloud connectivity - the whole data_* and doc_* cloud surface dies with it",
               enum=_family("NetworkProxySettings")),
        Member("proxyHost", TIER_REFUSED, "same as networkProxySetting - it severs cloud connectivity"),
        Member("proxyPort", TIER_REFUSED, "same as networkProxySetting - it severs cloud connectivity"),
    )),
    ("material", "materialPreferences", (
        Member("defaultMaterial", TIER_REFUSED,
               "it silently changes the material every NEW body in every future document is made of",
               by_name=True),
        Member("appearanceOverride", TIER_REFUSED,
               "it silently changes how every NEW body in every future document looks", by_name=True),
    )),
    ("grid", "gridPreferences", (
        Member("isLayoutGridLockEnabled"),
    )),
)

# The two COLLECTION groups: count/item/itemByName, one item per product, and the item carries the
# members. They are addressed '<group>.<item>.<member>' - the item name is part of the path.
COLLECTION_GROUPS = (
    ("products", "productPreferences", (
        Member("isFirstComponentGroundToParent"),
        Member("defaultDesignType", enum=_family("DefaultDesignTypeOptions")),
        Member("defaultWorkspace", enum=_family("DefaultWorkspaces")),
        Member("isActiveComponentVisibilityUsed"),
        Member("is3DSketchingAllowed"),
        Member("isGhostedResultBodyShown"),
        Member("isDimensionEditedWhenCreated"),
        Member("isAutoLookAtSketch2", enum=_family("AutoLookAtSketchSettings")),
        Member("isAutoLookAtSketch", TIER_REFUSED,
               "isAutoLookAtSketch2 is the same setting with more values - set that one, so the two "
               "cannot be given contradicting values"),
        Member("isAutoProjectGeometry"),
        Member("isAutoProjectEdgesOnReference"),
        Member("isAutoHideSketchOnFeatureCreation"),
        Member("isSketchScaledWithFirstDimension"),
        Member("isAllowReferencesDuringEditInPlace"),
        Member("isEnableArrangeAndSimplifyTools"),
        Member("isJointPreviewAnimated"),
    )),
    ("units_defaults", "defaultUnitsPreferences", (
        Member("defaultUnitSystem", enum=_family("UnitSystems")),
        Member("distanceDisplayUnits", enum=_family("DistanceUnits")),
        Member("massDisplayUnits", enum=_family("MassUnits")),
    )),
)

GROUPS = FLAT_GROUPS + COLLECTION_GROUPS
GROUP_KEYS = tuple(key for key, _attr, _members in GROUPS)
COLLECTION_KEYS = frozenset(key for key, _attr, _members in COLLECTION_GROUPS)
GROUP_ATTR = {key: attr for key, attr, _members in GROUPS}
GROUP_MEMBERS = {key: {m.name: m for m in members} for key, _attr, members in GROUPS}

# The default projection: the members that change how OTHER tools behave (what doc_save versions,
# which way is up, what units an expression is read in, whether a first component comes out
# grounded). Everything else needs include=.
_DEFAULT = {
    "general": ("isAutomaticVersioningEnabled", "automateVersioningTimeInterval",
                "isAutomaticSaveOnCloseEnabled", "defaultModelingOrientation"),
    "display": ("generalPrecision", "angularPrecision", "isPeriodDecimalPoint"),
    "products": ("isFirstComponentGroundToParent", "defaultDesignType"),
    "units_defaults": ("defaultUnitSystem", "distanceDisplayUnits", "massDisplayUnits"),
}

_UNREAD = object()      # a getter that RAISED - distinct from a member that reads None


def enum_names(enum_cls) -> dict:
    """{member name: int} read off the LIVE enum class. A hand-written copy would go stale against
    the platform, and an int alone is not decodable by the caller. {} when the family is not on
    this build."""
    out = {}
    if enum_cls is None:
        return out
    for attr in dir(enum_cls):
        if attr.startswith("_") or attr == "thisown":
            continue
        v = safe(lambda a=attr: getattr(enum_cls, a))
        if isinstance(v, int) and not isinstance(v, bool):
            out[attr] = v
    return out


def enum_member_name(enum_cls, value):
    """The enum member NAME for an int, or None (unknown int, or family not on this build)."""
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    for name, v in enum_names(enum_cls).items():
        if v == value:
            return name
    return None


def collection_items(prefs, key):
    """[(item name, item object)] for a COLLECTION group - the ONE walk both tools address an item
    through (the read publishes every item; the write resolves the one it was given).

    The name must be a non-empty STR: it becomes a JSON object KEY in the payload and part of the
    '<group>.<product>.<member>' address the write resolves, and json.dumps rejects a key that is
    not a str/int/float/bool/None - one object-valued name would sink the whole read."""
    coll = safe(lambda: getattr(prefs, GROUP_ATTR[key]))
    out = []
    if coll is None:
        return out
    for it in iter_collection(coll):
        name = safe(lambda it=it: it.name)
        if isinstance(name, str) and name:
            out.append((name, it))
    return out


_SCALARS = (bool, int, float, str)


def _wire_value(value):
    """(published value, non_scalar) for one member read. The payload is JSON-encoded whole, so an
    OBJECT-valued member would raise inside ok() and sink the read of every other member with it: a
    non-scalar is published by its .name, or by its type name when it reports none."""
    if value is None or isinstance(value, _SCALARS):
        return value, False
    name = safe(lambda: value.name)
    return (name if isinstance(name, str) and name else type(value).__name__), True


def _carries(group_obj, member_name):
    """True when this build's group object CARRIES the member, False when it does not, None when the
    question could not be asked.

    Read off dir(): it answers PRESENCE without invoking the getter, so asking costs nothing on a
    member that raises, and it is the same walk the census behind the table above was taken with -
    one reader, one notion of 'present'. (safe(hasattr) routes to the same answer on this build for
    all three cases - present-and-readable, present-and-raising, absent - so this is a choice of
    reader, not the thing that makes the split work. What makes the split work is that the raw READ
    already happened above: this is only asked once that read failed.)"""
    names = safe(lambda: dir(group_obj))
    if not names:
        return None
    return member_name in names


def read_member(group_obj, member):
    """One member as {value, tier}, plus 'enum' (the decoded member name), 'unreadable' /
    'unknown_member' and 'non_scalar' when they apply. A member that did not read reports null -
    never a guessed False/0, which would publish a measurement the read never took - and a member
    this build does not carry is flagged separately, so a typo in the table above is never published
    as a platform member that happens to raise."""
    raw = safe(lambda: getattr(group_obj, member.name), _UNREAD)
    if raw is _UNREAD:
        if _carries(group_obj, member.name) is False:
            return {"value": None, "tier": member.tier, "unknown_member": True}
        return {"value": None, "tier": member.tier, "unreadable": True}
    read = raw
    if member.by_name and raw is not None:
        # the declared shape: the member holds an object and its .name IS the published value. An
        # object that reports no name falls through to _wire_value, which flags what it publishes.
        named = safe(lambda: raw.name)
        read = named if isinstance(named, str) and named else raw
    value, non_scalar = _wire_value(read)
    rec = {"value": value, "tier": member.tier}
    if non_scalar:
        rec["non_scalar"] = True
    decoded = enum_member_name(member.enum, value)
    if decoded:
        rec["enum"] = decoded
    return rec


def _read_into(holder, key, wanted, unreadable, path, unknown=None):
    """{member: record} for one holder object, appending every unreadable member's full path - and
    every member this build does not carry to `unknown`, which is a defect in the table above, not a
    fact about the platform."""
    out = {}
    for name in wanted:
        rec = read_member(holder, GROUP_MEMBERS[key][name])
        if rec.get("unknown_member"):
            if unknown is not None:
                unknown.append(f"{path}.{name}")
        elif rec.get("unreadable"):
            unreadable.append(f"{path}.{name}")
        out[name] = rec
    return out


def _slice_group(prefs, key, wanted, unreadable, unknown=None):
    """One FLAT group's members as {member: record}."""
    group_obj = safe(lambda: getattr(prefs, GROUP_ATTR[key]))
    if group_obj is None:
        return None
    return _read_into(group_obj, key, wanted, unreadable, key, unknown)


def _slice_collection(prefs, key, wanted, unreadable, unknown=None):
    """One COLLECTION group as {item name: {member: record}} - one level deeper than a flat group,
    because the item name is part of every member's address."""
    items = collection_items(prefs, key)
    if not items:
        return None
    return {name: _read_into(obj, key, wanted, unreadable, f"{key}.{name}", unknown)
            for name, obj in items}


def _normalize_include(include):
    """Accept include as a list, a comma-string, or a single group name; -> a list of group names."""
    if include in (None, "", []):
        return []
    if isinstance(include, str):
        return [s.strip().lower() for s in include.split(",") if s.strip()]
    return [str(s).strip().lower() for s in include]


def handler(include=None) -> dict:
    """See TOOL_DESCRIPTION."""
    prefs = safe(lambda: app.preferences)
    if prefs is None:
        return error("app.preferences did not read - the application preferences are unavailable.")

    inc = _normalize_include(include)
    bad = [s for s in inc if s not in GROUP_KEYS]
    if bad:
        return error(f"Unknown include {bad}. Valid: {', '.join(GROUP_KEYS)}.")

    unreadable, unknown = [], []
    groups = {}
    for key in GROUP_KEYS:
        wanted = ([m.name for m in GROUP_MEMBERS[key].values()] if key in inc
                  else list(_DEFAULT.get(key, ())))
        if not wanted:
            continue
        slicer = _slice_collection if key in COLLECTION_KEYS else _slice_group
        payload = slicer(prefs, key, wanted, unreadable, unknown)
        if payload is not None:
            groups[key] = payload

    out = {"preferences": groups}
    if unreadable:
        # Named AND counted: a member the platform raises on is a fact about this build, not a gap
        # to hide. Its value is null above.
        out["unreadable"] = unreadable
        out["unreadable_count"] = len(unreadable)
    if unknown:
        # A DIFFERENT thing from unreadable: this build's object does not carry the member at all,
        # so the row asking for it is wrong. Published apart so it is never read as a platform fact.
        out["unknown_members"] = unknown

    remaining = [k for k in GROUP_KEYS if k not in inc]
    if remaining:
        out["note"] = (
            "Application preferences - they belong to the application, not to any document. "
            "Pull deeper with include=" + str(remaining) + " (one group per name). "
            + ", ".join(sorted(COLLECTION_KEYS)) + " nest one level further, by product name: "
            "sys_set_preferences addresses those members as '<group>.<product>.<member>'. "
            "tier 'W' = sys_set_preferences can set it; tier 'R' = refused there, with the reason.")
    return ok(out)


TOOL_DESCRIPTION = (
    "Read the APPLICATION's preferences (app.preferences - settings that belong to no document: "
    "versioning, modelling orientation, default units, number display, graphics, compatibility, "
    "paths). Default: the few that change how OTHER tools behave. 'include' pulls one group at a "
    "time (see the 'include' property for the group names); 'products' and 'units_defaults' nest "
    "by product name. Every key carries its tier - 'W' means sys_set_preferences can set it, 'R' "
    "means that tool refuses it and says why. A member whose getter raises on this build reports "
    "value null with unreadable true, never a guessed 0/false; a member this build does not carry "
    "at all reports unknown_member true instead - a different thing from a member that raises.\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="sys_get_preferences", description=TOOL_DESCRIPTION)
    .add_input_property("include", {"type": ["array", "string"],
            "description": "Groups to pull in full (a list or comma-string): general | display | "
                           "products | units_defaults | graphics | compatibility | api | network | "
                           "material | grid."})
    .strict_schema()
)
item = Item.create_tool_item(tool=tool, write="read", handler=handler, run_on_main_thread=True)


def register_tool():
    register(item)
