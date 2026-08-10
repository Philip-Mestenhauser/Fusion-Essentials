# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP building block: save the ACTIVE document as a NAMED MILESTONE version.

Document.saveMilestone on a MODIFIED document is a version-creating save - the new tip version IS
the milestone. On a CLEAN document it returns true and creates nothing (measured), so a document
with no unsaved changes is refused here rather than reported as a false success.
"""

from itertools import islice

import adsk.core

from ..mcp_primitives.tool import Tool
from ..mcp_primitives.item import Item
from ..mcp_primitives.registry import register
from ._common import iter_collection, ok, error, safe
from ._data_common import _agent_description
from . import _assert
from . import _export
from . import _outputs

app = adsk.core.Application.get()

# doc_save_milestone PRODUCES the milestoned document's lineage URN.
RETURNS = [
    _outputs.ReturnsUrn("document_id", consumers=["doc_open", "data_get"]),
]

# Both cloud facts lag the call: a FRESH findFileById reports the new tip within seconds, and the
# MILESTONE mark becomes readable later still. The version pump waits for the tip. The milestone is
# deliberately NOT pumped for - holding the main thread that long freezes Fusion and eats the
# server's task budget - so it is reported pending and the caller re-reads.
_VERSION_DEADLINE_S = 8.0
_POLL_SLEEP = 0.5
_MILESTONE_WALK_CAP = 200          # this tool's bound on the milestone census, not a platform limit


def _refetch(lineage):
    """A FRESH DataFile for the lineage URN, or None.

    The handle the save was issued on is never re-read: measured, its versionNumber / isMilestone /
    milestones.count keep their PRE-save values while a fresh fetch already reports the new tip."""
    return safe(lambda: app.data.findFileById(lineage)) if lineage else None


def _confirm_new_version(lineage, latest_before):
    """Re-fetch by lineage URN until the tip version passes latest_before, bounded by
    _VERSION_DEADLINE_S. Returns (fresh_datafile_or_None, latest_after_or_None) - the LAST reading
    either way, so a tip that never advanced is reported as read, not as a failure to read.

    The bounded pump loop is _export.pump_until; the tip-advanced signal is this tool's own."""
    def probe():
        fresh = _refetch(lineage)
        latest_after = safe(lambda: fresh.latestVersionNumber) if fresh is not None else None
        advanced = (latest_before is None
                    or (latest_after is not None and latest_after > latest_before))
        return advanced, (fresh, latest_after)

    _advanced, reading = _export.pump_until(probe, _VERSION_DEADLINE_S, _POLL_SLEEP)
    return reading


def _milestone_facts(fresh, name):
    """Milestone state off a FRESH DataFile: (is_milestone, milestone_count, name_present).

    Measured: on the handle the save was issued on these never change, and on a fresh fetch the
    milestone mark lags the new version. So a False flag read right after the save is 'not visible
    yet', and a field is None only when it could not be read at all.

    The name is matched over the ENUMERATION, never Milestones.itemByName: a name the collection does
    not hold RAISES '3 : invalid argument name' (measured live) instead of returning
    null as the binding documents, and safe() would flatten that raise into the same None an
    unreadable collection gives - so itemByName cannot tell a miss from an unreadable read. Walking
    the entries never provokes the raise. The walk is BOUNDED: it runs on the main thread against a
    cloud collection with no known ceiling, so name_present is only decided over the entries actually
    read - past the cap a miss is reported as unknown (None), never as absent."""
    if fresh is None:
        return None, None, None
    is_milestone = safe(lambda: fresh.isMilestone)
    coll = safe(lambda: fresh.milestones)
    count = safe(lambda: coll.count) if coll is not None else None
    name_present = None
    if count is not None:
        limit = min(count, _MILESTONE_WALK_CAP)
        seen = [safe(lambda m=m: m.name) for m in islice(iter_collection(coll), limit)]
        if name in seen:
            name_present = True
        elif limit >= count:
            name_present = False
    return is_milestone, count, name_present


def _unconfirmed(is_milestone, count, name_present, name):
    """The OBSERVED reason(s) the milestone is not yet confirmed - what was actually read, never a
    guess about which of them is lagging."""
    reasons = []
    if is_milestone is None:
        reasons.append("the new version's isMilestone flag could not be read")
    elif is_milestone is not True:
        reasons.append("the new version's isMilestone flag reads FALSE")
    if count is None:
        reasons.append("the document's Milestones collection could not be read")
    elif name_present is None:
        reasons.append(f"the document's Milestones collection holds {count} entries, more than the "
                       f"{_MILESTONE_WALK_CAP} this call walks, so whether it names '{name}' is "
                       "unknown here")
    elif name_present is not True:
        reasons.append(f"the Milestones collection ({count} entr"
                       f"{'y' if count == 1 else 'ies'} read) holds no entry named '{name}'")
    return reasons


def handler(milestone_name: str = "", description: str = "") -> dict:
    """See TOOL_DESCRIPTION."""
    name = (milestone_name or "").strip()
    if not name:
        return error("Provide 'milestone_name'. Fusion accepts an empty name and invents one, but an "
                     "unnamed milestone cannot be found by name in the version history afterwards.")

    doc = safe(lambda: app.activeDocument)
    if not doc:
        return error("No active document to milestone.")
    df = safe(lambda: doc.dataFile)
    if df is None:
        return error("The active document has never been saved to the cloud (no DataFile), and "
                     "saveMilestone cannot create one. Save it first with doc_save_as, then "
                     "milestone the next change.")

    # A CLEAN document is REFUSED: saveMilestone returns true on one while creating no version, no
    # milestone and no description (measured) - reporting that as success would be a false ok.
    if not safe(lambda: doc.isModified, True):
        return error(f"'{safe(lambda: doc.name)}' has no unsaved changes. On an unmodified document "
                     "saveMilestone reports success but creates NO version and NO milestone, so this "
                     "call is refused instead of returning a false success. This tool only creates a "
                     "NEW milestone version - make the change you want milestoned and call again.")

    lineage = safe(lambda: df.id)
    version_before = safe(lambda: df.versionNumber)
    latest_before = safe(lambda: df.latestVersionNumber)
    desc = _agent_description(description)

    # The mutation is NOT wrapped in safe - a raised failure must surface, never a false ok.
    try:
        # adsk.core: Document.saveMilestone(milestoneName, versionDescription)
        did = doc.saveMilestone(name, desc)
    except Exception as e:
        return error(f"saveMilestone raised saving '{safe(lambda: doc.name)}' as milestone "
                     f"'{name}': {e}")
    if not did:
        return error(f"saveMilestone returned false for milestone '{name}'; no version and no "
                     "milestone were created.")

    # A save can move the document onto a NEW lineage URN (measured: the first save after a
    # configured-design conversion forks the file, restarting its versions at 1). Confirming
    # against the pre-save lineage then watches the WRONG version stream, so the check re-anchors
    # on the lineage the document actually holds now.
    lineage_now = safe(lambda: doc.dataFile.id)
    forked = (isinstance(lineage, str) and isinstance(lineage_now, str)
              and lineage.startswith("urn:") and lineage_now.startswith("urn:")
              and lineage_now != lineage)
    confirm_lineage = lineage_now if forked else lineage
    # The wait settles on a COMPARISON, so it needs a baseline: a fork restarts the version stream,
    # and an unreadable pre-save tip leaves nothing to compare against. In both cases the confirming
    # read runs once and the payload reports what it read instead of a verdict it cannot support.
    comparable = not forked and latest_before is not None
    fresh, latest_after = _confirm_new_version(confirm_lineage,
                                               latest_before if comparable else None)
    is_milestone, count_after, name_present = _milestone_facts(fresh, name)
    # The verdict needs the comparison to have RUN. A fork restarts the version stream, so the
    # pre-save number is no baseline for the new lineage's - with no comparable baseline the version
    # is simply NOT confirmed (and the note below says the check could not be made). Reading "some
    # tip number came back on the new lineage" as confirmation claims a check that never happened.
    cloud_tip_advanced = bool(comparable and latest_after is not None
                             and latest_after > latest_before)
    milestone_confirmed = (is_milestone is True) and (name_present is True)

    result = {
        "save_call_returned_true": True,
        "milestone_name": name,
        "document_name": safe(lambda: doc.name),
        "document_id": confirm_lineage,
        "description": desc,
        "version_before": version_before,
        "latest_version_before": latest_before,
        "latest_version_after": latest_after,
        # NOT 'version_confirmed': that key belongs to the _assert.VersionAdvanced postcondition,
        # which reports a different reading (the document is no longer modified). Two meanings under
        # one key means the kernel's setdefault silently drops one of them.
        "cloud_tip_advanced": cloud_tip_advanced,
        "milestone_confirmed": milestone_confirmed,
        "milestone_count_after": count_after,
    }
    if forked:
        result["lineage_changed"] = {"from": lineage, "to": lineage_now}
    if not cloud_tip_advanced:
        seen = latest_after if latest_after is not None else "unreadable"
        result["pending"] = True
        if not comparable:
            # With no usable BASELINE the wait has nothing to settle against and returns on its
            # first read: no duration was spent and non-advancement was never observed. Report the
            # missing baseline and the tip that was read - a "versioned nothing" verdict here would
            # diagnose a comparison that did not happen.
            why = ("the save moved the document onto a NEW lineage, whose version stream does not "
                   f"continue the pre-save number ({latest_before})" if forked else
                   "the document's latestVersionNumber could not be read BEFORE the save")
            result["note"] = (f"saveMilestone returned true, but {why} - so whether a NEW version "
                              f"was created is not decidable here (the fresh read after the save "
                              f"reports {seen}). Read the history back with "
                              "doc_get include=['versions'].")
        else:
            # Not lag: a real save's new tip arrives on a fresh fetch well inside the pump, so a tip
            # that has not moved by the deadline is the signature of a saveMilestone that versioned
            # NOTHING - measured on a clean document, where the call still returns true.
            result["note"] = (f"saveMilestone returned true but the cloud tip has NOT advanced "
                              f"after {_VERSION_DEADLINE_S:.0f}s of re-fetching (latest reads "
                              f"{seen}, was {latest_before}) - the signature of a save that "
                              "versioned nothing, the same result an unmodified document gives. "
                              "Check doc_get include=['versions'] before calling again.")
    elif milestone_confirmed:
        result["note"] = (f"Version {latest_after} was created and IS the milestone '{name}' "
                          "(confirmed on a fresh read of the cloud file). Read the history back with "
                          "doc_get include=['versions'].")
    else:
        result["pending"] = True
        result["note"] = (f"Version {latest_after} was created and the document is no longer "
                          "modified, but " + " and ".join(
                              _unconfirmed(is_milestone, count_after, name_present, name))
                          + " yet. The milestone mark becomes readable some seconds AFTER the version "
                          "does, so this is NOT evidence that no milestone was created. Re-read "
                          "doc_get include=['versions'] to confirm the milestone row.")
    if forked:
        result["note"] += (" THIS SAVE ALSO MOVED THE DOCUMENT TO A NEW LINEAGE URN - address the "
                           "file by lineage_changed.to from now on; lineage_changed.from opens the "
                           "file this one forked from. The milestone check above ran on the NEW "
                           "lineage; in the one measured fork the mark never became readable there, "
                           "so treat an unconfirmed milestone after a fork as NOT applied and "
                           "milestone the next change instead of re-reading.")
    return ok(result)


TOOL_DESCRIPTION = (
    "Save the ACTIVE document as a NAMED MILESTONE - a version marked in the data panel and the "
    "Fusion web client, findable by name later. It is a real save: it creates a NEW cloud version "
    "and that version IS the milestone (it does not tag an existing one), so the document must have "
    "unsaved changes - an unmodified one is REFUSED, because Fusion reports success on it while "
    "creating nothing. The doc must already exist in the cloud (doc_save_as first). The new version "
    "is confirmed before returning; the milestone MARK lags it by several seconds more, so the "
    "result usually reports 'pending' - re-read with doc_get include=['versions'].\n"
    + _outputs.produces_block(RETURNS)
)

tool = (
    Tool.create_simple(name="doc_save_milestone", description=TOOL_DESCRIPTION)
    .add_input_property("milestone_name", {"type": "string",
            "description": "Milestone name as shown in the data panel (required, non-empty)."})
    .add_input_property("description", {"type": "string",
            "description": "Optional version description (the AI-agent marker is prepended)."})
    .add_required_input("milestone_name")
    .strict_schema()
)

item = Item.create_tool_item(tool=tool, write="write", handler=handler, run_on_main_thread=True,
                             postconditions=[_assert.VersionAdvanced()])


def register_tool():
    register(item)
