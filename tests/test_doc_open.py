"""Unit tests for ``doc_open.py`` identifier parsing.

``_b64url_decode`` and ``_urn_candidates`` turn whatever the user pastes — a
bare URN, a URN with a ``?version=`` suffix, or a full Fusion web URL with the
lineage URN base64url-encoded into a path segment — into the list of URN
candidates to try. This is pure string/base64 work and a rich bug surface
(padding restoration, the - / _ alphabet, picking the urn:adsk segment out of a
URL), so it gets thorough coverage. No live Fusion needed.

The encoded URN below is real: base64url(urn:adsk.wipprod:dm.lineage:abc123XYZ).
"""

from conftest import load_tool

od = load_tool("doc_open")

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


# ── CAM-template open guard + declare-intent default (refuse the silent crash) ──────────────────
#
# VERIFIED LIVE (three crashes): a freshly-copied multi-reference CAM doc cannot be opened OR even
# reference-inspected via the API without crashing Fusion. CAM-ness CANNOT be auto-detected
# (inspecting the file IS the crash), so the safe-vs-unsafe call MUST come from the caller. The
# third crash happened because is_cam_template defaulted false and a BARE doc_open silently took the
# API path. The fix removes that silent default: doc_open now REFUSES any open that hasn't DECLARED
# INTENT — either is_cam_template=true (UI open) or force_api_open=true (explicit API open). A bare
# call resolves/opens NOTHING. These pin that contract.

import json


class TestCamTemplateGuard:
    def test_refuses_api_open_and_does_not_resolve_or_open(self, monkeypatch):
        # The guard must short-circuit BEFORE _resolve_data_file or _open_document — touching the
        # file is itself the crash. So both must remain uncalled.
        touched = {"resolve": False, "open": False}
        monkeypatch.setattr(od, "_resolve_data_file",
                            lambda raw: touched.__setitem__("resolve", True) or (object(), raw, [raw]))
        monkeypatch.setattr(od, "_open_document",
                            lambda d: touched.__setitem__("open", True) or (object(), "x", None))
        res = od.handler(file_id="urn:cam", is_cam_template=True)
        payload = json.loads(res["content"][0]["text"])
        assert res["isError"] is False
        assert payload["refused_api_open"] is True
        assert payload["opened"] is False
        assert payload["file_id"] == "urn:cam"
        assert "UI" in payload["note"] or "Data Panel" in payload["note"]
        assert touched["resolve"] is False        # did NOT resolve the DataFile (would crash)
        assert touched["open"] is False           # did NOT attempt the open

    def test_normal_open_requires_force_api_open(self, monkeypatch):
        # A non-CAM doc opens normally — but ONLY when the caller declares force_api_open=true.
        class FakeDoc:
            name = "Plain"
        fake = FakeDoc()
        monkeypatch.setattr(od, "_resolve_data_file",
                            lambda raw: (type("DF", (), {"isConfiguredDesign": False, "name": "Plain"})(), raw, [raw]))
        monkeypatch.setattr(od, "_open_document", lambda d: (fake, "openUsingContext", None))
        od.app = type("A", (), {"activeDocument": fake})()
        res = od.handler(file_id="urn:plain", force_api_open=True)
        payload = json.loads(res["content"][0]["text"])
        assert payload["opened"] is True
        assert payload["document_name"] == "Plain"

    def test_bare_open_refuses_without_declaring_intent(self, monkeypatch):
        # The crash that bit us: a bare doc_open (no is_cam_template, no force_api_open) must NOT
        # silently take the API path. It refuses and resolves/opens NOTHING.
        touched = {"resolve": False, "open": False}
        monkeypatch.setattr(od, "_resolve_data_file",
                            lambda raw: touched.__setitem__("resolve", True) or (object(), raw, [raw]))
        monkeypatch.setattr(od, "_open_document",
                            lambda d: touched.__setitem__("open", True) or (object(), "x", None))
        res = od.handler(file_id="urn:something")
        assert res["isError"] is True
        # the refusal must name BOTH ways to declare intent (error text is in 'message')
        assert "is_cam_template" in res["message"]
        assert "force_api_open" in res["message"]
        assert touched["resolve"] is False        # never touched the file (could be a CAM crash)
        assert touched["open"] is False

    def test_cam_flag_wins_over_force(self, monkeypatch):
        # If a caller sets BOTH, the safe path wins: declaring it a CAM template refuses the API
        # open regardless of force_api_open (you can't force-crash through the CAM guard).
        touched = {"resolve": False}
        monkeypatch.setattr(od, "_resolve_data_file",
                            lambda raw: touched.__setitem__("resolve", True) or (object(), raw, [raw]))
        res = od.handler(file_id="urn:cam", is_cam_template=True, force_api_open=True)
        payload = json.loads(res["content"][0]["text"])
        assert payload["refused_api_open"] is True
        assert touched["resolve"] is False
