# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.

"""The static MCP Resource this server publishes: the packaged guidance, rendered whole.

MCP's resource channel is application-controlled content a client reads by address, which is what
packaged guidance is. One entry, built from the same document ``sys_get_guidance`` answers from and
rendered through the same ``render.body``, so the two channels cannot serve different guidance.

``entry.py`` builds this catalog at startup and hands it to ``start_server``: the transport serves
the value it was given and reads no product file of its own.
"""

from . import loader
from . import render

# The address space this server names its packaged documents in. The id in the address is the
# document's own, so the URI a tool payload publishes and the URI the resource listing carries are
# one string built one way.
URI_PREFIX = "fusion-essentials://guidance/"

# Rendered guidance is Markdown - headings and rules, not a data structure to parse.
MIME_TYPE = "text/markdown"


def uri_for(guidance_id):
    """The resource URI naming one guidance document."""
    return f"{URI_PREFIX}{guidance_id}"


def catalog(path=None):
    """The resource entries this server can serve, each carrying its own rendered text.

    Raises ``loader.GuidanceUnavailable`` when the packaged document does not read: the caller then
    publishes nothing and advertises no resource capability, rather than an address that answers
    with an error."""
    doc, _sha256 = loader.load(path)
    text = render.body(doc)
    return [{
        "uri": uri_for(doc.get("guidance_id")),
        "name": doc.get("name"),
        "title": doc.get("title"),
        "description": doc.get("description"),
        "mimeType": MIME_TYPE,
        # Bytes of the text a read returns - the size the client is deciding whether to fetch.
        "size": len(text.encode("utf-8")),
        "text": text,
    }]
