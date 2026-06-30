# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Shared core for turning a solid model into a CAM TOOL HOLDER profile — the headless,
UI-free half of the "Add Tool Holder" command (commands/addHolder/entry.py).

WHY THIS EXISTS: the create-holder geometry is the reason many people cloned this repo. It used to
live entirely inside the Add Holder COMMAND, reachable only by a human clicking body + axis + end
face in a dialog. The math is pure, though — given a solid body, an axis of rotation, and a point on
that axis (the end datum), it reduces the body's rotation-coincident faces to a stack of
(height, lower-diameter, upper-diameter) segments and emits the holder's library JSON. None of that
needs the UI. Extracting it here lets BOTH the interactive command AND an MCP tool
(model_create_holder) drive the SAME code, so an agent can batch-convert many uploaded holders while
the dialog still serves one-at-a-time users.

This module is deliberately import-light: only ``adsk.*`` + stdlib, NO ``futil`` / ``config`` / UI
handlers. So it loads in the test harness the same way every other ``tools/`` helper does
(``load_tool("_holder")``), and the interactive command imports it without circularity.

The geometry routines are lifted VERBATIM from the original command (the hard-won cylindrical-
coordinate reduction, the duplicate/occlusion filtering, the chamfer-as-cone assumption) — this is a
move, not a rewrite. The only changes are dropping ``futil`` logging and adding docstrings. A
``_`` prefix keeps the tool auto-discovery sweep from treating it as a tool.

Grounded in adsk.core / adsk.cam (signatures as used by the original command):
  - adsk.core.InfiniteLine3D / Plane / Cone / Cylinder / Torus / Circle3D / Arc3D (geometry casts)
  - adsk.cam.Tool.createFromJson(jsonStr)  -> a holder Tool (type='holder')
  - adsk.cam.CAMManager.get().libraryManager.toolLibraries (library enumeration; reads only here)
"""

import json
import math
import random
import time
from typing import List

import adsk.core
import adsk.fusion
import adsk.cam

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "holder geometry: get_axis, get_tool_profile, build_holder_data, get_tooling_libraries"


# ── axis + end-datum resolution (from a face/edge/construction axis, and a normal datum) ──────────

def get_axis(axis_base):
    """An InfiniteLine3D for the holder's axis of rotation, from a cylindrical/conical/toroidal FACE,
    a linear EDGE, or a construction axis. None if the entity can't define an axis.

    (Verbatim from the Add Holder command — the same cast-by-surface/curve-type logic.)"""
    if isinstance(axis_base, adsk.fusion.BRepFace):
        face_type = axis_base.geometry.surfaceType
        if face_type == adsk.core.SurfaceTypes.ConeSurfaceType:
            cone = adsk.core.Cone.cast(axis_base.geometry)
            return adsk.core.InfiniteLine3D.create(cone.origin, cone.axis)
        elif face_type == adsk.core.SurfaceTypes.CylinderSurfaceType:
            cylinder = adsk.core.Cylinder.cast(axis_base.geometry)
            return adsk.core.InfiniteLine3D.create(cylinder.origin, cylinder.axis)
        elif face_type == adsk.core.SurfaceTypes.TorusSurfaceType:
            torus = adsk.core.Torus.cast(axis_base.geometry)
            return adsk.core.InfiniteLine3D.create(torus.origin, torus.axis)
    elif isinstance(axis_base, adsk.fusion.BRepEdge):
        edge_type = axis_base.geometry.curveType
        if edge_type == adsk.core.Curve3DTypes.Line3DCurveType:
            line = adsk.core.Line3D.cast(axis_base.geometry)
            return line.asInfiniteLine()
    elif isinstance(axis_base, adsk.fusion.ConstructionAxis):
        return axis_base.geometry
    return None


def is_valid_axial_datum(surface, axis):
    """The Point3D where the end datum meets the axis — for a planar face NORMAL to the axis, a linear
    edge perpendicular to it, or a vertex (projected onto the axis). None if the datum isn't valid for
    this axis. This is the 'end face' input: it pins where z=0 sits along the axis.

    (Verbatim from the Add Holder command.)"""
    if isinstance(surface, adsk.fusion.BRepFace):
        face_type = surface.geometry.surfaceType
        if face_type == adsk.core.SurfaceTypes.PlaneSurfaceType:
            plane = adsk.core.Plane.cast(surface.geometry)
            return plane.intersectWithLine(axis)
    elif isinstance(surface, adsk.fusion.BRepEdge):
        edge_type = surface.geometry.curveType
        normal, center = None, None
        if edge_type == adsk.core.Curve3DTypes.Line3DCurveType:
            line = adsk.core.Line3D.cast(surface.geometry)
            # the edge must be orthogonal to the axis
            if axis.direction.dotProduct(line.asInfiniteLine().direction) != 0:
                return None
            plane = adsk.core.Plane.create(line.startPoint, axis.direction)
            return plane.intersectWithLine(axis)
        elif edge_type == adsk.core.Curve3DTypes.NurbsCurve3DCurveType:
            return None
        elif edge_type == adsk.core.Curve3DTypes.Circle3DCurveType:
            circle = adsk.core.Circle3D.cast(surface.geometry)
            normal = circle.normal
            center = circle.center
        elif edge_type == adsk.core.Curve3DTypes.Ellipse3DCurveType:
            ellipse = adsk.core.Ellipse3D.cast(surface.geometry)
            normal = ellipse.normal
            center = ellipse.center
        elif edge_type == adsk.core.Curve3DTypes.Arc3DCurveType:
            arc = adsk.core.Arc3D.cast(surface.geometry)
            normal = arc.normal
            center = arc.center
        elif edge_type == adsk.core.Curve3DTypes.EllipticalArc3DCurveType:
            elliptical_arc = adsk.core.EllipticalArc3D.cast(surface.geometry)
            normal = elliptical_arc.normal
            center = elliptical_arc.center
        else:
            return None
        if not axis.direction.isParallelTo(normal):
            return None
        plane = adsk.core.Plane.create(center, axis.direction)
        return plane.intersectWithLine(axis)
    elif isinstance(surface, adsk.fusion.BRepVertex):
        point = adsk.core.Point3D.cast(surface.geometry)
        plane = adsk.core.Plane.create(point, axis.direction)
        return plane.intersectWithLine(axis)
    return None


# ── the profile reduction (body of revolution -> (height, r_lower, r_upper) segments) ─────────────

def get_tool_profile(body, axis, plane_intersect):
    """Reduce a holder body to a turned PROFILE: a list of [z0, z1, r0, r1] segments along the axis.

    Collects the body's faces that are coaxial with `axis` (cones/cylinders/tori — a torus is treated
    as a chamfer/cone), expresses their edges in cylindrical (r, z) coordinates about the axis with z=0
    at `plane_intersect`, de-duplicates and removes faces occluded by a larger coaxial face, and
    returns the ordered radial profile. (Verbatim from the Add Holder command — the load-bearing
    geometry; do not 'tidy' it without re-validating against real holders.)"""
    plane = adsk.core.Plane.create(plane_intersect, axis.direction)
    points = []
    for edge in body.edges:
        points.append(edge.startVertex.geometry)
        points.append(edge.endVertex.geometry)
    cylindrical_points = []
    for point in points:
        cylindrical_points.append(get_cylindrical_coordinates_point(point, axis, plane))

    # coaxial cone/cylinder/torus faces only (planar faces are inherently axis-parallel; skip them)
    useful_faces = []
    for face in body.faces:
        if face.geometry.surfaceType == adsk.core.SurfaceTypes.ConeSurfaceType:
            cone = adsk.core.Cone.cast(face.geometry)
            if axis.isColinearTo(adsk.core.InfiniteLine3D.create(cone.origin, cone.axis)):
                useful_faces.append(face)
        elif face.geometry.surfaceType == adsk.core.SurfaceTypes.CylinderSurfaceType:
            cylinder = adsk.core.Cylinder.cast(face.geometry)
            if axis.isColinearTo(adsk.core.InfiniteLine3D.create(cylinder.origin, cylinder.axis)):
                useful_faces.append(face)
        elif face.geometry.surfaceType == adsk.core.SurfaceTypes.TorusSurfaceType:
            torus = adsk.core.Torus.cast(face.geometry)
            if axis.isColinearTo(adsk.core.InfiniteLine3D.create(torus.origin, torus.axis)):
                useful_faces.append(face)

    # each face -> a (r_low, r_high, z_low, z_high) segment from its extreme coaxial edges
    face_segments = []
    for face in useful_faces:
        valid_edges = []
        for edge in face.edges:
            pt = get_cylindrical_coordinates_edge(edge, axis, plane)
            if pt is not None:
                valid_edges.append(pt)
        if len(valid_edges) >= 2:
            valid_edges.sort(key=lambda x: x[1])
            z_1 = valid_edges[0][1]
            z_2 = valid_edges[-1][1]
            r_1 = valid_edges[0][0]
            r_2 = valid_edges[-1][0]
            face_segments.append((r_1, r_2, z_1, z_2))

    # drop exact-duplicate segments
    ind_to_pop = []
    face_segments.sort(key=lambda x: x[2])
    for i in range(0, len(face_segments) - 1):
        if (abs(face_segments[i][0] - face_segments[i + 1][0]) < 1e-8
                and abs(face_segments[i][1] - face_segments[i + 1][1]) < 1e-8
                and abs(face_segments[i][2] - face_segments[i + 1][2]) < 1e-8
                and abs(face_segments[i][3] - face_segments[i + 1][3]) < 1e-8):
            ind_to_pop.append(i)
    for i in range(0, len(ind_to_pop)):
        face_segments.pop(ind_to_pop[i] - i)

    profile = []
    for seg in face_segments:
        profile.append((seg[0], seg[2], 1))
        profile.append((seg[1], seg[3], 0))
    profile.sort(key=lambda x: x[1])
    profile = filter_points(profile)

    # remove points occluded by a larger coaxial segment spanning the same z
    for segment in face_segments:
        ind_to_pop = []
        for i in range(0, len(profile)):
            if profile[i][1] >= segment[3] - 1e-8 or profile[i][1] <= segment[2] + 1e-8:
                continue
            elif profile[i][0] < (((segment[1] - segment[0]) / (segment[3] - segment[2]))
                                  * (segment[2] - profile[i][1]) + segment[0]):
                ind_to_pop.append(i)
        for i in range(0, len(ind_to_pop)):
            profile.pop(ind_to_pop[i] - i)

    profile_points = []
    for i in range(0, len(profile) - 1):
        if abs(profile[i][1] - profile[i + 1][1]) < 1e-8:
            continue
        profile_points.append([profile[i][1], profile[i + 1][1], profile[i][0], profile[i + 1][0]])

    return profile_points


def filter_points(points):
    """Group profile points by z (within 1e-8) and keep the two largest-radius per z (one at the
    lowest z), so coincident-z points collapse to a single radial pair. (Verbatim.)"""
    grouped_points = {}
    for x, y, z in points:
        rounded_y = round(y, 8)
        if rounded_y not in grouped_points:
            grouped_points[rounded_y] = []
        grouped_points[rounded_y].append((x, y, z))

    filtered_points = []
    min_key = min(grouped_points.keys())
    for key, group in grouped_points.items():
        sorted_group = sorted(group, key=lambda p: p[0], reverse=True)
        if key == min_key:
            filtered_points.append(sorted_group[0])
        else:
            filtered_points.extend(sorted_group[:2])
    filtered_points.sort(key=lambda p: (p[1], p[2]))
    return filtered_points


def get_cylindrical_coordinates_edge(edge, axis, plane):
    """(radius, z) of a circular/arc edge whose normal is coaxial with `axis`, else None. (Verbatim.)"""
    if edge.geometry is None:
        return None
    edge_type = edge.geometry.curveType
    if (edge_type != adsk.core.Curve3DTypes.Circle3DCurveType
            and edge_type != adsk.core.Curve3DTypes.Arc3DCurveType):
        return None
    point = edge.geometry.center
    normal = edge.geometry.normal
    if not axis.isColinearTo(adsk.core.InfiniteLine3D.create(point, normal)):
        return None
    line = adsk.core.InfiniteLine3D.create(point, axis.direction)
    intersect = plane.intersectWithLine(line)
    z = point.distanceTo(intersect)
    return (edge.geometry.radius, z)


def get_cylindrical_coordinates_point(point, axis, plane):
    """(radius from axis, z along axis from `plane`) for a world point. (Verbatim.)"""
    line = adsk.core.InfiniteLine3D.create(point, axis.direction)
    intersect = plane.intersectWithLine(line)
    z = point.distanceTo(intersect)
    r = point.distanceTo(axis.origin)
    return (r, z)


# ── holder JSON / library Tool ────────────────────────────────────────────────────────────────────

def build_holder_data(profile, desc, prodid="", prodlink="", prodvendor=""):
    """The holder library JSON dict (type='holder', millimeters) for a profile. Profile lengths are in
    cm (the API unit); segment heights/diameters are emitted in MM (×10 / ×20 for diameter), matching
    the original command. Returned as a dict so a tool can surface it without minting a Tool object.

    A fresh random guid/reference_guid + last_modified timestamp are generated (same as the command).
    Diameter = radius × 2; both rounded to 3 dp. Segment height = z1 − z0."""
    guid = "00000000-0000-0000-0000-" + str(random.randint(100000000000, 999999999999))
    data = {
        "description": desc,
        "guid": guid,
        "last_modified": math.ceil(time.time()),
        "product-id": prodid,
        "product-link": prodlink,
        "reference_guid": guid,
        "segments": [],
        "type": "holder",
        "unit": "millimeters",
        "vendor": prodvendor,
    }
    for segment in profile:
        seg = {
            "height": round((segment[1] - segment[0]) * 10, 3),
            "lower-diameter": round(segment[2] * 10 * 2, 3),
            "upper-diameter": round(segment[3] * 10 * 2, 3),
        }
        data["segments"].append(seg)
    return data


def generate_tool(profile, desc, prodid="", prodlink="", prodvendor=""):
    """An adsk.cam.Tool (type='holder') built from a profile + metadata, via Tool.createFromJson.
    (Verbatim behaviour from the command; delegates the JSON to build_holder_data so a tool can return
    the same dict without a live CAM Tool.)"""
    data = build_holder_data(profile, desc, prodid, prodlink, prodvendor)
    return adsk.cam.Tool.createFromJson(json.dumps(data))


# ── tool-library enumeration (READ only; library WRITES belong to the future library tool family) ──

def get_tooling_libraries() -> List:
    """URLs of every cloud + local + external tool library (read-only enumeration). The eventual
    library building-block family will own WRITES — note a tool brought into a document is a hard FORK
    of the library data, not a live link, so library writes need their own correct semantics."""
    camManager = adsk.cam.CAMManager.get()
    libraryManager = camManager.libraryManager
    toolLibraries = libraryManager.toolLibraries
    folder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.CloudLibraryLocation)
    libraries = _libraries_urls(toolLibraries, folder)
    folder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.LocalLibraryLocation)
    libraries = libraries + _libraries_urls(toolLibraries, folder)
    folder = toolLibraries.urlByLocation(adsk.cam.LibraryLocations.ExternalLibraryLocation)
    libraries = libraries + _libraries_urls(toolLibraries, folder)
    return libraries


def _libraries_urls(libraries, url) -> List:
    """Recursively collect library asset URLs under `url` (cloud/local/external folder). (Verbatim.)"""
    urls = []
    libs = libraries.childAssetURLs(url)
    for lib in libs:
        urls.append(lib.toString())
    for folder in libraries.childFolderURLs(url):
        urls = urls + _libraries_urls(libraries, folder)
    return urls


def format_library_names(libraries: List) -> List:
    """The trailing-segment display name of each library URL. (Verbatim.)"""
    return [library.split('/')[-1] for library in libraries]
