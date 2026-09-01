# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""Packaged static guidance: the authored JSON document and the loader that reads it.

The document is DATA - ``gen_guidance.py`` renders the checked-in Claude skill from it and
``loader`` serves the same records to ``sys_get_guidance``, so both surfaces state what one file
says. Nothing here imports ``adsk``: this package knows about a file, not about Fusion.
"""
