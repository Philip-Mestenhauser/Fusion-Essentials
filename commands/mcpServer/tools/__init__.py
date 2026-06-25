# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""MCP tools package.

Safe, always-on tools self-register on import (get_session_info).
Gated / high-risk tools (execute_api_script) do NOT self-register; entry.py
imports them and calls their register_tool() only when the user has enabled them.

entry.start() imports this package after resetting the registry, so each enabled
tool registers exactly once per server start.
"""

from . import get_session_info  # noqa: F401  (self-registers)
from . import active_document  # noqa: F401  (entry.py registers; resolve active doc -> URN)
from . import reload_addin  # noqa: F401  (entry.py registers + installs its event)
from . import data_model  # noqa: F401  (entry.py registers; read-only)
from . import open_document  # noqa: F401  (entry.py registers; opens by UID)
from . import get_screenshot  # noqa: F401  (entry.py registers; viewport capture)
from . import workspaces  # noqa: F401  (entry.py registers; list/switch workspaces)
from . import cam_info  # noqa: F401  (entry.py registers; read CAM setups/operations)
from . import component_tree  # noqa: F401  (entry.py registers; assembly tree + X-refs)
from . import data_management  # noqa: F401  (entry.py registers; create project/folder, upload)
from . import parameters  # noqa: F401  (entry.py registers; read design parameters)
from . import timeline  # noqa: F401  (entry.py registers; read the design timeline)
from . import visibility  # noqa: F401  (entry.py registers; isolate/show/hide occurrences)
from . import cam_templates  # noqa: F401  (entry.py registers; navigate + apply toolpath templates)
from . import execute_api_script  # noqa: F401  (gated; entry.py registers if enabled)
