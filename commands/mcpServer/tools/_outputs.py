# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed OUTPUT KINDS: the producer-side mirror of ``_inputs.InputKind``. A tool declares
``RETURNS = [_outputs.ReturnsHandle(...), ...]`` for each stable id/value it mints;
``assert_present(payload)`` is a test hook that fails if the handler's ``ok()`` payload doesn't
actually carry the declared key, so a renamed field breaks the suite instead of silently lying to
every consumer that reads it. See ``tools/CLAUDE.md`` and ``CONTRIBUTING.md`` ("Return the IDs the
next call needs") for the full rationale."""

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = ("RETURNS kinds (ReturnsHandle/Urn/Name/Value/Verdict) - declare a tool's stable "
             "outputs once")


class OutputKind:
    """One declared tool output. ``key`` = the payload field a consumer reads; ``label`` = the human
    "what it is"; ``consumers`` = the tool names that read it (for the generated prose); ``stable`` =
    deterministic / round-trippable id (vs a transient value); ``in_list`` = the key lands inside each
    item of a list (e.g. find_geometry's ``matches``) rather than at the payload top level."""

    def __init__(self, key, label, consumers=(), stable=True, in_list=False):
        self.key = key
        self.label = label
        self.consumers = list(consumers)
        self.stable = stable
        self.in_list = in_list

    def produces_note(self) -> str:
        who = (" -> " + ", ".join(self.consumers)) if self.consumers else ""
        return f"{self.key}: {self.label}{who}".rstrip()

    # ── the test hook ────────────────────────────────────────────────────────
    def _present_in(self, obj) -> bool:
        """True if self.key appears at obj's top level, or - when in_list - inside any list item."""
        if isinstance(obj, dict):
            if self.key in obj and obj[self.key] is not None:
                return True
            if self.in_list:
                for v in obj.values():
                    if isinstance(v, list) and any(
                            isinstance(it, dict) and it.get(self.key) is not None for it in v):
                        return True
        return False

    def assert_present(self, payload) -> str:
        """Return an error string if the (already-decoded) ok() payload doesn't carry self.key, else ''.
        ``payload`` is the dict a handler json.dumps into its ok() text content."""
        if self._present_in(payload):
            return ""
        where = "in any list item" if self.in_list else "at the payload top level"
        return f"declared output '{self.key}' is missing {where}"


class ReturnsHandle(OutputKind):
    """Mints a find_geometry-style entityToken handle - the producer counterpart to
    ``_inputs.GeometryHandle``. Resolves via ``findEntityByToken`` WHILE LIVE; tokens are short-lived
    (the same entity can return a different token on a later query), so consume promptly + re-find if
    stale. ``stable=False`` reflects that - it is not a durable id like a URN."""

    def __init__(self, key="handle", require="any", in_list=True, **kw):
        super().__init__(
            key,
            f"a {require} 'handle' (entityToken; short-lived - use promptly, re-find if stale)",
            stable=False, in_list=in_list, **kw)
        self.require = require


class ReturnsUrn(OutputKind):
    """Mints a data-model lineage/version URN (document_id / versionId / source_id / folder_id /
    project id) - consumed by the doc_*/data_* tools."""

    def __init__(self, key="document_id", **kw):
        super().__init__(key, "a data-model lineage URN", stable=True, **kw)


class ReturnsName(OutputKind):
    """Mints an EXACT name a consumer keys off (occurrence / setup / operation / joint / body).
    Stable for round-tripping within the session, but only as unique as the thing it names - prefer a
    fullPathName/handle where one exists."""

    def __init__(self, key, of="occurrence", in_list=False, **kw):
        super().__init__(key, f"the exact {of} name", stable=True, in_list=in_list, **kw)
        self.of = of


class ReturnsValue(OutputKind):
    """Mints a measured / computed value (extents, frame axes, cycle time, a health verdict) - a result
    to read, not a stable id to round-trip."""

    def __init__(self, key, label, **kw):
        super().__init__(key, label, stable=False, **kw)


class ReturnsVerdict(OutputKind):
    """The ASSERTION-READ contract: a named check that returns a boolean verdict WITH the evidence
    that justifies it - never a bare boolean. One shape across every assertion read
    (model_measure_relation, assembly_inspect_interference): 'relation' names the check, 'passed' is the
    verdict, 'measured' holds the observed numbers, 'tolerance_used' what it was judged against.
    assert_present enforces ALL FOUR keys, that 'passed' is a real bool, and - when the declaring
    tool names its relations - that 'relation' is one of them."""

    KEYS = ("relation", "passed", "measured", "tolerance_used")

    def __init__(self, relations=(), **kw):
        super().__init__(
            "passed",
            "the pass/fail verdict, always beside its evidence (relation / passed / measured / "
            "tolerance_used)",
            stable=False, **kw)
        self.relations = tuple(relations)

    def assert_present(self, payload) -> str:
        if not isinstance(payload, dict):
            return "verdict payload is not a dict"
        missing = [k for k in self.KEYS if k not in payload]
        if missing:
            return "verdict contract keys missing: " + ", ".join(missing)
        if not isinstance(payload["passed"], bool):
            return f"verdict 'passed' must be a real boolean, got {type(payload['passed']).__name__}"
        if self.relations and payload.get("relation") not in self.relations:
            return (f"verdict 'relation' is '{payload.get('relation')}' - not one of the declared: "
                    + ", ".join(self.relations))
        return ""


# ── prose generation (mirrors _inputs.contract_block) ─────────────────────────

def produces_block(spec, header="PRODUCES") -> str:
    """Assemble a tool's RETURNS spec into a description block - the producer-side counterpart of
    _inputs.contract_block. One canonical 'what this returns + who consumes it' line per output, so the
    chain is declared once here instead of hand-written in the producer AND paraphrased in each consumer."""
    lines = [f"{header}:"]
    for out in spec:
        lines.append(f"- {out.produces_note()}")
    return "\n".join(lines)
