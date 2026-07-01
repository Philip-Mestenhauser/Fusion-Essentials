"""Unit tests for the typed OUTPUT KINDS framework (_outputs.py).

This is the producer-side mirror of _inputs.py. Its value is that a tool DECLARES what it returns (the
payload key a consumer reads), so (a) the "returns X → consumed by Y" prose is generated once instead of
hand-written in the producer and paraphrased in every consumer, and (b) a test can assert the handler
actually mints the declared key — a renamed field fails the suite instead of silently lying to consumers.
Pinned: produces_note/produces_block generation, and the assert_present hook (top-level AND in-list).
"""

from conftest import load_tool

out = load_tool("_outputs")


class TestProducesNote:
    def test_handle_note_names_key_and_consumers(self):
        k = out.ReturnsHandle("handle", require="any",
                              consumers=["joint_at_geometry", "model_fillet"])
        note = k.produces_note()
        assert note.startswith("handle:")
        assert "entityToken" in note
        assert "joint_at_geometry" in note and "model_fillet" in note

    def test_note_without_consumers_omits_arrow(self):
        k = out.ReturnsValue("became_solid", "whether the stitch closed into a solid")
        note = k.produces_note()
        assert note.startswith("became_solid:")
        assert "→" not in note

    def test_urn_and_name_labels(self):
        assert "URN" in out.ReturnsUrn("document_id").produces_note()
        assert "occurrence name" in out.ReturnsName("occurrence_one", of="occurrence").produces_note()


class TestProducesBlock:
    def test_block_has_header_and_one_bullet_per_output(self):
        spec = [out.ReturnsHandle("handle", consumers=["joint_at_geometry"]),
                out.ReturnsValue("match_count", "how many matched")]
        block = out.produces_block(spec)
        assert block.startswith("PRODUCES:")
        assert block.count("\n- ") == 2          # one ASCII bullet line per output
        assert "handle:" in block and "match_count:" in block


class TestAssertPresentTopLevel:
    def test_present_returns_empty(self):
        k = out.ReturnsUrn("document_id")
        assert k.assert_present({"document_id": "urn:adsk.file:abc", "name": "Part"}) == ""

    def test_missing_returns_error_naming_key(self):
        k = out.ReturnsUrn("document_id")
        err = k.assert_present({"name": "Part"})
        assert "document_id" in err and "missing" in err

    def test_null_value_counts_as_missing(self):
        # A declared id that came back null is not "present" — a consumer can't use it.
        k = out.ReturnsUrn("document_id")
        assert k.assert_present({"document_id": None}) != ""


class TestAssertPresentInList:
    def test_handle_inside_a_matches_list_is_found(self):
        # find_geometry shape: the handle lives inside each item of a list, not at the top level.
        k = out.ReturnsHandle("handle", in_list=True)
        payload = {"match_count": 2, "matches": [
            {"handle": "tok1", "kind": "cylinder_face"},
            {"handle": "tok2", "kind": "planar_face"}]}
        assert k.assert_present(payload) == ""

    def test_in_list_but_no_item_has_the_key_errors(self):
        k = out.ReturnsHandle("handle", in_list=True)
        payload = {"matches": [{"kind": "cylinder_face"}]}   # items lack 'handle'
        assert k.assert_present(payload) != ""

    def test_empty_list_is_missing(self):
        k = out.ReturnsHandle("handle", in_list=True)
        assert k.assert_present({"matches": []}) != ""

    def test_top_level_key_not_treated_as_in_list_when_flag_off(self):
        # A non-list output must NOT be satisfied by a nested occurrence of the key.
        k = out.ReturnsName("occurrence_one", of="occurrence", in_list=False)
        payload = {"joints": [{"occurrence_one": "X"}]}      # only nested
        assert k.assert_present(payload) != ""
