# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Typed OUTPUT KINDS — the producer-side mirror of ``_inputs.InputKind``.

When tool A mints a stable id (a find_geometry ``handle`` entityToken, a data-model URN, an exact
occurrence/setup/operation name, a measured value) that tool B consumes by name, that relationship was
stated TWICE in English: once in A's "returns X" prose and again in B's "needs an X from A" input note.
``_inputs.py`` already made the CONSUMER side declarative (schema+resolve+validate+contract in one
place); there was no symmetric mechanism for the PRODUCER side, and nothing asserted that A actually
returns a field named ``handle`` — so renaming it would silently break every consumer's note with no
test catching it (the architecture §10 "convention, not enforced" gap).

An ``OutputKind`` declares ONE field a tool RETURNS: the payload key it lands under, a human label, the
tools that consume it, and whether the value is a stable/round-trippable id. From that one declaration:
  * ``produces_block(spec)`` generates the tool's "PRODUCES:" description line (mirrors
    ``_inputs.contract_block``) — the producer prose is generated, not hand-typed-then-paraphrased.
  * ``OutputKind.assert_present(payload)`` is a TEST HOOK: it fails if the handler's ``ok()`` payload
    doesn't actually carry the declared key (top-level OR inside a list of items, like
    ``find_geometry.matches[].handle``). A renamed field fails the suite instead of lying to 18 consumers.

Tools declare ``RETURNS = [ _outputs.ReturnsHandle(...), ... ]`` the way they declare an inputs spec.
"""

# One-line "what to reuse from here" for the generated CLAUDE.md helper map (see tests/gen_manifest.py).
MAP_BLURB = "RETURNS kinds (ReturnsHandle/Urn/Name/Value) — declare a tool's stable outputs once"


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
        who = (" → " + ", ".join(self.consumers)) if self.consumers else ""
        return f"{self.key}: {self.label}{who}".rstrip()

    # ── the test hook ────────────────────────────────────────────────────────
    def _present_in(self, obj) -> bool:
        """True if self.key appears at obj's top level, or — when in_list — inside any list item."""
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
    """Mints a find_geometry-style entityToken handle — the producer counterpart to
    ``_inputs.GeometryHandle``. Resolves via ``findEntityByToken`` WHILE LIVE; tokens are short-lived
    (the same entity can return a different token on a later query), so consume promptly + re-find if
    stale. ``stable=False`` reflects that — it is not a durable id like a URN."""

    def __init__(self, key="handle", require="any", in_list=True, **kw):
        super().__init__(
            key,
            f"a {require} 'handle' (entityToken; short-lived — use promptly, re-find if stale)",
            stable=False, in_list=in_list, **kw)
        self.require = require


class ReturnsUrn(OutputKind):
    """Mints a data-model lineage/version URN (document_id / versionId / source_id / folder_id /
    project id) — consumed by the doc_*/data_* tools."""

    def __init__(self, key="document_id", **kw):
        super().__init__(key, "a data-model lineage URN", stable=True, **kw)


class ReturnsName(OutputKind):
    """Mints an EXACT name a consumer keys off (occurrence / setup / operation / joint / body).
    Stable for round-tripping within the session, but only as unique as the thing it names — prefer a
    fullPathName/handle where one exists."""

    def __init__(self, key, of="occurrence", in_list=False, **kw):
        super().__init__(key, f"the exact {of} name", stable=True, in_list=in_list, **kw)
        self.of = of


class ReturnsValue(OutputKind):
    """Mints a measured / computed value (extents, frame axes, cycle time, a health verdict) — a result
    to read, not a stable id to round-trip."""

    def __init__(self, key, label, **kw):
        super().__init__(key, label, stable=False, **kw)


# ── prose generation (mirrors _inputs.contract_block) ─────────────────────────

def produces_block(spec, header="PRODUCES") -> str:
    """Assemble a tool's RETURNS spec into a description block — the producer-side counterpart of
    _inputs.contract_block. One canonical 'what this returns + who consumes it' line per output, so the
    chain is declared once here instead of hand-written in the producer AND paraphrased in each consumer."""
    lines = [f"{header}:"]
    for out in spec:
        lines.append(f"• {out.produces_note()}")
    return "\n".join(lines)
