# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The measurement harness's cloud pre-flight: the gate an unconfigured machine meets first.

Some rows find the operator's project BY NAME, so an unconfigured run would search for a project
named '' and fail - and the all-PASS gate reads a failed row as a broken API contract. The gate
refuses before any read of the session, which makes it decidable offline.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "live"))
import measure_api  # noqa: E402


@pytest.fixture
def no_session(monkeypatch):
    """Every door out of this process, shut. run_measurements otherwise talks to a LIVE Fusion -
    _fusion_version() health-gates and calls workspace_orient over HTTP, and the step after it opens
    a scratch DOCUMENT in the operator's session. A unit test reaches neither: each stub raises if
    the call order ever puts it before the pure gate under test."""
    def _no(*_a, **_kw):
        raise AssertionError("a unit test reached the live Fusion session")

    for name in ("_fusion_version", "health_gate", "call", "registered_tools"):
        monkeypatch.setattr(measure_api, name, _no)
    return monkeypatch


class TestTheCloudPreflight:
    """Three rows find the operator's project BY NAME. Unconfigured, each would search for a project
    named '' and FAIL - and the all-PASS gate reads a failed row as a broken API contract, so an
    unconfigured machine would look like a platform regression."""

    def test_the_cloud_rows_are_derived_from_the_bodies_not_a_list(self):
        # Derived, so a new cloud row joins the pre-flight by reading CLOUD_PROJECT. The three known
        # ones must be in it, or the refusal below covers nothing.
        found = measure_api.cloud_rows()
        assert set(found) >= {"shape-dump-data-world", "shape-dump-data-cloud-collections",
                              "shape-dump-drawing-world"}
        for row_id in found:
            row = next(r for r in measure_api.ROWS if r["id"] == row_id)
            assert "CLOUD_PROJECT" in (row["body_fn"]() if "body_fn" in row else row["body"])

    def test_an_unconfigured_run_refuses_naming_the_file_and_its_shape(self, no_session):
        no_session.setattr(measure_api, "CLOUD_PROJECT", "")
        with pytest.raises(SystemExit) as exc:
            measure_api.run_measurements(write_json=False)
        message = str(exc.value)
        # the refusal has to be ACTIONABLE: the file to write and the keys it holds
        assert measure_api.cloud_config.CONFIG_NAME in message
        assert '"hub"' in message and '"project"' in message and '"folder"' in message
        assert "shape-dump-data-world" in message
        # Reaching this line at all is the other half: the no_session stubs raise on any call out,
        # so the gate refused BEFORE the version read and before a scratch document was opened.
