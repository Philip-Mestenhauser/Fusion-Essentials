"""Unit tests for ``open_document.py`` identifier parsing.

``_b64url_decode`` and ``_urn_candidates`` turn whatever the user pastes — a
bare URN, a URN with a ``?version=`` suffix, or a full Fusion web URL with the
lineage URN base64url-encoded into a path segment — into the list of URN
candidates to try. This is pure string/base64 work and a rich bug surface
(padding restoration, the - / _ alphabet, picking the urn:adsk segment out of a
URL), so it gets thorough coverage. No live Fusion needed.

The encoded URN below is real: base64url(urn:adsk.wipprod:dm.lineage:abc123XYZ).
"""

from conftest import load_tool

od = load_tool("open_document")

URN = "urn:adsk.wipprod:dm.lineage:abc123XYZ"
URN_B64URL = "dXJuOmFkc2sud2lwcHJvZDpkbS5saW5lYWdlOmFiYzEyM1hZWg"
WEB_URL = f"https://myhub.autodesk360.com/g/projects/proj/data/{URN_B64URL}/"


# ── _b64url_decode ─────────────────────────────────────────────────────────

class TestB64UrlDecode:
    def test_decodes_real_urn_segment(self):
        assert od._b64url_decode(URN_B64URL) == URN

    def test_restores_missing_padding(self):
        # The segment above has no '=' padding; decode must still succeed.
        assert od._b64url_decode(URN_B64URL).startswith("urn:adsk")

    def test_invalid_base64_returns_none(self):
        assert od._b64url_decode("!!!not base64!!!") is None


# ── _urn_candidates ────────────────────────────────────────────────────────

class TestUrnCandidates:
    def test_bare_urn_is_first_candidate(self):
        out = od._urn_candidates(URN)
        assert out[0] == URN

    def test_extracts_urn_from_web_url(self):
        out = od._urn_candidates(WEB_URL)
        assert URN in out

    def test_urn_with_version_suffix_kept_as_is(self):
        raw = f"{URN}?version=3"
        out = od._urn_candidates(raw)
        # The raw value (with suffix) is always tried first...
        assert out[0] == raw
        # ...and the clean inline urn is also surfaced as a candidate.
        assert URN in out

    def test_candidates_are_deduped(self):
        out = od._urn_candidates(URN)
        assert len(out) == len(set(out))

    def test_plain_garbage_yields_only_itself(self):
        out = od._urn_candidates("just-some-text")
        assert out == ["just-some-text"]
