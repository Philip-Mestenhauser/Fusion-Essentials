# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Read the packaged guidance document, and version it by the hash of what was read.

The JSON sits beside this module and is resolved RELATIVE TO IT, so the read answers the same in
any process working directory. A document that does not load raises ``GuidanceUnavailable`` naming
the file: a packaged asset that is not there is reported, never substituted.
"""

import hashlib
import json
import os

# The section order the document declares, named here so a caller can build a closed input over it
# without opening the file (and so a load failure still leaves a tool with a schema). It is a fact
# OF the JSON: test_sys_get_guidance.py holds this tuple against the shipped document's own ids.
SECTION_IDS = ("kernel", "plan", "sketch", "model", "assemble", "validate", "finish")

GUIDANCE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "parametric_cad_design.json")


class GuidanceUnavailable(Exception):
    """The packaged document did not load: it is missing, unreadable, or not JSON."""


def load(path=None):
    """(document, sha256) for the packaged guidance, or GuidanceUnavailable naming the file.

    The hash is taken over the BYTES that were read, so it versions exactly what is served rather
    than a re-serialization of the parsed object."""
    path = GUIDANCE_PATH if path is None else path
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise GuidanceUnavailable(
            f"The packaged guidance document did not read: {path} - {exc}. It ships beside the "
            "server code; until that file is restored there is no guidance to serve.")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise GuidanceUnavailable(
            f"The packaged guidance document is not readable JSON: {path} - {exc}.")
    return doc, hashlib.sha256(raw).hexdigest()


def section_ids(doc):
    """The section ids the DOCUMENT carries, in its own order - the reading order it declares."""
    return [sec.get("id") for sec in (doc.get("sections") or []) if isinstance(sec, dict)]


def find_section(doc, section_id):
    """The section carrying this id, or None. EXACT match: the ids are a closed set the caller
    already chose from, not a name space to search."""
    for sec in (doc.get("sections") or []):
        if isinstance(sec, dict) and sec.get("id") == section_id:
            return sec
    return None


def section_index(doc):
    """[{id, title, rule_count}] per section - the compact rows a no-argument read answers with,
    where the count says how much one section costs to ask for."""
    return [{"id": sec.get("id"), "title": sec.get("title"),
             "rule_count": len(sec.get("rules") or [])}
            for sec in (doc.get("sections") or []) if isinstance(sec, dict)]
