"""Lint: a shared helper lives in exactly ONE module - its home (CLAUDE.md "Reuse before you write").

Fusion-Essentials keeps its shared conventions in a handful of ``_``-prefixed modules
(``_common``, ``_cam_common``, ``_data_common``, ``_export``, ...). A helper re-implemented locally
inside a tool file instead of imported from its home diverges silently the next time the home module
is fixed or extended - the local copy keeps stale (or wrong) behavior forever. This is a DENYLIST
lint: for each known shared symbol, its definition must appear in its home module and NOWHERE else
under ``tools/``. Growing this list is how a future consolidation locks itself in.
"""

import os
import re

from conftest import TOOLS_DIR

# symbol -> (home module, is a function def vs a plain module-level assignment)
# Each entry names the ONE file allowed to define it; every other tool module must import it from
# there instead of re-implementing it.
_DENYLIST = {
    "get_cam": ("_cam_common", "def"),
    "find_setup": ("_cam_common", "def"),
    "find_operation": ("_cam_common", "def"),
    "walk_operations": ("_cam_common", "def"),
    "setup_names": ("_cam_common", "def"),
    "_b64url_decode": ("_data_common", "def"),
    "_urn_candidates": ("_data_common", "def"),
    "sanitize": ("_export", "def"),
    "component_by_name": ("_export", "def"),
    "verify_written": ("_export", "def"),
    "ptxyz": ("_common", "def"),
    "target_sketch": ("_common", "def"),
    "timeline_health": ("_common", "def"),
    "result_bodies": ("_common", "def"),
    "resolve_entity_ref": ("_common", "def"),
    "CM_TO_UNIT": ("_common", "assign"),
    "OPERATIONS": ("_common", "assign"),
    "build_joint_geometry": ("_joints", "def"),
    "apply_motion": ("_joints", "def"),
    "find_joint": ("_joints", "def"),
    "all_joints": ("_joints", "def"),
    "current_joint_type": ("_joints", "def"),
}


def _pattern(symbol, kind):
    if kind == "def":
        return re.compile(r"^def " + re.escape(symbol) + r"\(", re.M)
    return re.compile(r"^" + re.escape(symbol) + r"\s*=", re.M)


def _all_tool_files():
    return [fn for fn in sorted(os.listdir(TOOLS_DIR))
            if fn.endswith(".py") and fn != "__init__.py"]


class TestHelperDefinedOnlyInItsHomeModule:
    def test_denylisted_symbols_have_exactly_one_definition(self):
        offenders = []
        for symbol, (home, kind) in _DENYLIST.items():
            home_file = home + ".py"
            pattern = _pattern(symbol, kind)
            defined_in = []
            for fn in _all_tool_files():
                src = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8").read()
                if pattern.search(src):
                    defined_in.append(fn)
            elsewhere = [fn for fn in defined_in if fn != home_file]
            if elsewhere:
                offenders.append(
                    f"'{symbol}' is defined outside its home module {home_file} in: "
                    f"{', '.join(elsewhere)} - import it from {home} instead of re-implementing it"
                )
            if home_file not in defined_in:
                offenders.append(
                    f"'{symbol}' is not defined in its declared home module {home_file} - "
                    f"the denylist entry is stale, fix it or move the definition back"
                )
        assert not offenders, "helper duplication:\n  " + "\n  ".join(offenders)
