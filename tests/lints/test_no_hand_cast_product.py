# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Lint: acquire the active Design / CAM product through the resolver, not a hand cast.

`adsk.fusion.Design.cast(app.activeProduct)` returns None when the active product is NOT a design
(e.g. a CAM product is in front while a design is open behind it). `_common.design()` handles that -
it falls back to the active document's DesignProductType - so a hand cast silently breaks features
(fit_to, active-component notes) whenever CAM is active. Likewise `adsk.cam.CAM.cast(...)` is what
`_cam_common.get_cam()` already does once, in one place. Banned in tool modules:
  - `Design.cast(...activeProduct...)` outside _common  -> call `_common.design()`;
  - `CAM.cast(...)` outside _cam_common                 -> call `_cam_common.get_cam()`.
"""

import os
import re

import _corpus
from conftest import TOOLS_DIR

_DESIGN_CAST = re.compile(r"Design\.cast\([^)]*activeProduct")
_CAM_CAST = re.compile(r"\bCAM\.cast\(")


def _scan(regex, home):
    offenders = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn == home:
            continue
        src = _corpus.text(os.path.join(TOOLS_DIR, fn))
        for i, line in enumerate(src.splitlines(), 1):
            if regex.search(line):
                offenders.append(f"{fn}:{i}: {line.strip()}")
    return offenders


class TestNoHandCastProduct:
    def test_no_design_cast_of_active_product(self):
        offenders = _scan(_DESIGN_CAST, "_common.py")
        assert not offenders, (
            "hand cast of the active product to a Design - call `_common.design()` (it falls back "
            "when a CAM product is active, which Design.cast(activeProduct) does not):\n  "
            + "\n  ".join(offenders))

    def test_no_manual_cam_cast(self):
        offenders = _scan(_CAM_CAST, "_cam_common.py")
        assert not offenders, (
            "hand cast to a CAM product - call `_cam_common.get_cam()` (the one shared CAM resolver):\n  "
            + "\n  ".join(offenders))

    def test_the_lint_bites(self):
        # prove the regex catches the banned activeProduct cast and skips the sanctioned fallback
        # shape _common.design() itself uses (cast off doc.products, not app.activeProduct).
        assert _DESIGN_CAST.search("d = adsk.fusion.Design.cast(app.activeProduct)")
        assert not _DESIGN_CAST.search(
            "d = adsk.fusion.Design.cast(app.activeDocument.products.itemByProductType('DesignProductType'))")
