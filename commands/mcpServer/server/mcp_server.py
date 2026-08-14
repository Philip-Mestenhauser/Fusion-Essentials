# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""A dependency-free MCP server over HTTP that runs inside Fusion's Python.

Implements the MCP JSON-RPC methods we need (initialize, tools/list, tools/call)
by hand, so no external packages are required.

Differences from the sample this was adapted from:
  - The MCP endpoint is served on the path **/mcp** (to match Fusion's built-in
    well-known endpoint) in addition to "/".
  - Logging goes through fusion360utils (futil), not raw app.log/print.
  - start_server() distinguishes a port-already-in-use bind failure (EADDRINUSE)
    from other errors and reports it via a structured result, so the caller can
    tell the user to disable Autodesk's built-in MCP server.
"""

import asyncio
import errno
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from ....lib import fusion360utils as futil
from ..mcp_primitives.item import Item
from ..version import __version__
from .task_manager import TaskManager

# The MCP path served by Fusion's built-in server; we mirror it so clients
# configured for the well-known endpoint reach us unchanged.
MCP_PATH = '/mcp'

# Every protocol revision this server actually understands (Streamable HTTP transport). On
# initialize we honor the client's requested protocolVersion ONLY if it appears here; otherwise
# we respond with our newest supported revision instead of echoing semantics we do not implement.
SUPPORTED_PROTOCOL_VERSIONS = ('2025-03-26',)
PROTOCOL_VERSION = SUPPORTED_PROTOCOL_VERSIONS[-1]    # the default we offer: newest supported

# Server-level instructions, returned on `initialize` (the MCP spec field). This is the ONLY server text
# a client sees BEFORE it fetches any tool schema - so it's the one place a cold agent is guaranteed to
# read. When tools are deferred (names only until searched), a per-tool "call FIRST" instruction is
# invisible at cold start; this routes the agent to the two orientation tools before it fishes blindly.
INSTRUCTIONS = (
    "Autodesk Fusion control. COLD START: before reaching for specific tools, call two orientation reads "
    "first - sys_capability_map (what this server can do: the tool families + each one's entry tool) "
    "and workspace_orient (what's in front of you: the active document, its health, contents, and "
    "pointers to the right deep tool). Then drill with the family's tools. Searching by keyword for an "
    "existing capability/input-kind: sys_find_tool. Most reads are RICH: a <domain>_get (cam_get, "
    "design_get, doc_get, data_get) gives a light default plus include=[...] for depth. Every write tool "
    "accepts expect_document (a doc name or URN from a prior read): if the active document changed since "
    "that read, the write is REFUSED as active_document_changed - switch back with doc_activate and retry."
)

# Header names (Streamable HTTP transport).
SESSION_HEADER = 'Mcp-Session-Id'

# Our server's identifying name. Returned by GET /health and used by the
# post-start self-check to confirm WE are the server answering on the port
# (vs. Autodesk's built-in server, which reports "MCP HTTP Server").
SERVER_NAME = "Fusion-Essentials MCP Server"

# Seconds a main-thread tool task may run before _execute_on_main_thread reports "still running".
# Tools that legitimately run long (e.g. STEP export, cloud upload) opt out via enforce_timeout=False
# on their Item. This is a fixed budget, not per-request configurable.
MAIN_THREAD_TASK_TIMEOUT_S = 30

# Result codes returned by start_server() so entry.py can react appropriately.
START_OK = 'ok'
START_PORT_IN_USE = 'port_in_use'
START_ERROR = 'error'


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request on its own daemon thread."""
    daemon_threads = True
    allow_reuse_address = False  # we WANT bind to fail loudly if 27182 is taken


# Handler kwargs DELIBERATELY absent from a tool's wire schema: the handler accepts the key and
# answers with a targeted redirect, which beats a generic unknown-argument error. The argument
# gate lets these through on otherwise-strict tools. Shrink-only; the reason lives as a comment
# at the tool's own registration site.
_SCHEMA_OMITTED_ARGS = {
    'joint_edit': frozenset({'rotation_deg'}),   # posing is joint_drive's job; handler redirects
}


def _in_set(value: Any, allowed: frozenset) -> bool:
    """value in allowed, but an unhashable value (a list/dict where a string enum is expected) is
    simply not a member rather than a TypeError."""
    try:
        return value in allowed
    except TypeError:
        return False


def _enum_specs(schema: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Precompute the enum-constrained properties of one tool's input schema, so the per-call check is
    a dict lookup + set membership (never a re-walk). Returns {prop_name: {"values": [...], "set":
    frozenset, "array": bool}}. A property carries an enum either directly ({type:string, enum:[...]})
    or as array items ({type:array, items:{enum:[...]}}) - the latter validates each element."""
    specs: Dict[str, Dict[str, Any]] = {}
    props = (schema or {}).get("properties") or {}
    for pname, pschema in props.items():
        if not isinstance(pschema, dict):
            continue
        enum = pschema.get("enum")
        if isinstance(enum, list) and enum:
            specs[pname] = {"values": list(enum), "set": frozenset(enum), "array": False}
            continue
        if pschema.get("type") == "array":
            items = pschema.get("items")
            if isinstance(items, dict) and isinstance(items.get("enum"), list) and items["enum"]:
                specs[pname] = {"values": list(items["enum"]),
                                "set": frozenset(items["enum"]), "array": True}
    return specs


class SimpleMCPServer:
    """Routes MCP JSON-RPC requests to registered tool handlers."""

    def __init__(self, name: str = SERVER_NAME):
        self.name = name
        # Session id assigned at initialize and echoed back to the client on every
        # response. Generated lazily so each server instance has a stable id.
        self.session_id = uuid.uuid4().hex
        self.tools: Dict[str, Item] = {}
        # tool name -> {prop: enum spec}, precomputed at registration so enum validation is O(1) per arg.
        self._enum_specs: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.server_info = {"name": name, "version": __version__}

    def register(self, item: Item):
        if not isinstance(item, Item):
            raise ValueError("Can only register Item instances")
        item_type = item.get_type()
        if item_type != "tool":
            raise ValueError(f"Only Tool items can be registered, got type: {item_type}")
        self.tools[item.primitive.name] = item
        self._enum_specs[item.primitive.name] = _enum_specs(item.primitive.input_schema)
        futil.log(f"MCP tool registered: {item.primitive.name}")

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        # A JSON-RPC request MUST be a single object. A LIST body is a batch (this server does not
        # implement batching); any other non-object body is malformed. Either way return ONE
        # well-formed error object (id:null) instead of raising - the .get() calls below assume a
        # dict, and a raw raise here would surface as an opaque HTTP 500 (a list raised twice: once
        # here, again in the except handler's request.get).
        if not isinstance(request, dict):
            if isinstance(request, list):
                message = ("This server does not support JSON-RPC batching (an array of requests). "
                           "Send one request object per HTTP POST.")
            else:
                message = "Invalid Request: expected a single JSON-RPC request object."
            return self._error(None, -32600, message)
        try:
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params", {})

            # Notifications (no "id") get no response body; caller returns 202.
            is_notification = "id" not in request
            if is_notification:
                # e.g. notifications/initialized, notifications/cancelled - accept silently.
                return None

            if method == "initialize":
                return self._handle_initialize(request_id, params)
            elif method == "ping":
                return {"jsonrpc": "2.0", "id": request_id, "result": {}}
            elif method == "tools/list":
                return self._handle_tools_list(request_id)
            elif method == "tools/call":
                return await self._handle_tools_call(request_id, params)
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as e:
            # Defensive: only a dict body has an id to echo (the entry guard above already rejects a
            # non-dict, but never call .get on something that might not be a dict).
            request_id = request.get("id") if isinstance(request, dict) else None
            return self._error(request_id, -32603, str(e))

    def _handle_initialize(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        # Honor the client's requested protocol version only if we actually implement it;
        # otherwise respond with our own version rather than claiming support we don't have.
        client_version = (params or {}).get("protocolVersion")
        if client_version in SUPPORTED_PROTOCOL_VERSIONS:
            protocol_version = client_version
        else:
            protocol_version = PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}},
                "serverInfo": self.server_info,
                # Visible to the client BEFORE any tool schema is fetched - the cold-start front door
                # (routes a contextless agent to sys_capability_map / workspace_orient first).
                "instructions": INSTRUCTIONS,
            },
        }

    def _handle_tools_list(self, request_id: Any) -> Dict[str, Any]:
        tools = [item.primitive.to_dict() for item in self.tools.values()]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    async def _handle_tools_call(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name not in self.tools:
            # -32602 (Invalid params) is the spec's example code for an unknown tool name.
            return self._error(request_id, -32602, f"Tool not found: {tool_name}")
        item = self.tools[tool_name]

        # Validate BEFORE dispatch: an unknown/missing argument is a doomed call, so reject it
        # here rather than posting a main-thread task (or raising a raw TypeError from **kwargs).
        validation_error = self._validate_tool_arguments(tool_name, item, arguments)
        if validation_error is not None:
            return {"jsonrpc": "2.0", "id": request_id, "result": validation_error}

        futil.log(f"MCP calling tool: {tool_name}")
        try:
            if item.run_on_main_thread:
                result = await self._execute_on_main_thread(
                    item.handler, arguments,
                    enforce_timeout=item.enforce_timeout)
            else:
                result = item.handler(**arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as e:
            # A tool EXECUTION failure (including the curated timeout messages from
            # _execute_on_main_thread) is reported inside the result with isError=true, per the MCP
            # spec, so the calling agent can read it and self-correct. It is NOT a JSON-RPC protocol
            # error (-32603 is reserved for protocol-level failures) and carries no Python traceback.
            futil.handle_error(f"MCP tool '{tool_name}'")
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": self._tool_error_result(f"Tool '{tool_name}' failed: {e}"),
            }

    @staticmethod
    def _tool_error_result(message: str) -> Dict[str, Any]:
        return {
            "content": [{"type": "text", "text": message}],
            "isError": True,
            "message": message,
        }

    def _validate_tool_arguments(self, tool_name: str, item: Item,
                                 arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Check `arguments` against item.primitive.input_schema before dispatch.

        Argument NAMES are checked here, and any argument whose schema property declares an `enum`
        is checked against it: a permissive client that sends an out-of-enum value (e.g.
        find_geometry kind="faces") would otherwise get silent wrong behavior (match_count:0) instead
        of a correction. Unknown keys are rejected ONLY when the tool's wire schema declares
        additionalProperties=false - a lenient schema stays lenient, so the schema never promises what
        the server then refuses. Keys a handler deliberately accepts off-schema pass through
        (_SCHEMA_OMITTED_ARGS). Failures come back as isError TOOL results rather than JSON-RPC
        protocol errors - a deliberate deviation from the spec's invalid-params bucket: an in-band
        result is what a calling agent can actually read and self-correct from.
        """
        schema = item.primitive.input_schema or {}
        properties = schema.get("properties") or {}
        required = schema.get("required") or []

        # strictness is declared on the primitive (strict_schema() sets additional_properties=False;
        # to_dict() renders it as the wire schema's additionalProperties) - read the same source.
        if item.primitive.additional_properties is False:
            allowed_extra = _SCHEMA_OMITTED_ARGS.get(tool_name, frozenset())
            unknown = sorted(key for key in arguments
                             if key not in properties and key not in allowed_extra)
            if unknown:
                keys = ", ".join(f"'{key}'" for key in unknown)
                return self._tool_error_result(
                    f"Unknown argument for tool '{tool_name}': {keys}. "
                    f"Expected arguments: {sorted(properties.keys())}. Remove it and retry.")

        for name in required:
            if name not in arguments:
                return self._tool_error_result(
                    f"Missing required argument for tool '{tool_name}': '{name}'. "
                    f"Required: {required}.")

        enum_error = self._validate_enums(tool_name, arguments)
        if enum_error is not None:
            return enum_error

        return None

    def _validate_enums(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Reject an out-of-enum argument value against the precomputed enum sets. A scalar enum prop
        must equal one of its values; an array-of-enum prop must have every element in the set. The
        error names the property, the offending value, and the valid values so the agent can correct."""
        for pname, spec in self._enum_specs.get(tool_name, {}).items():
            if pname not in arguments:
                continue
            value = arguments[pname]
            if value is None:
                continue                          # absent/unset - the handler's default applies
            if spec["array"]:
                if not isinstance(value, list):
                    continue                      # a non-list for an array prop is a shape error, not ours
                bad = [v for v in value if not _in_set(v, spec["set"])]
                if bad:
                    return self._tool_error_result(
                        f"Invalid value(s) for '{pname}' in tool '{tool_name}': "
                        f"{', '.join(repr(v) for v in bad)}. Valid values: {spec['values']}.")
            elif not _in_set(value, spec["set"]):
                return self._tool_error_result(
                    f"Invalid value for '{pname}' in tool '{tool_name}': {value!r}. "
                    f"Valid values: {spec['values']}.")
        return None

    async def _execute_on_main_thread(self, handler_func, arguments: Dict[str, Any],
                                      enforce_timeout: bool = True) -> Any:
        """Run handler_func(**arguments) on Fusion's main thread via TaskManager.

        enforce_timeout=False waits indefinitely for the callback to complete - for tools (e.g.
        sys_execute_script) whose work cannot be interrupted and would still commit, so a timeout
        would only report a false failure for a change that actually applied.
        """
        import time

        result_holder = {'result': None, 'exception': None, 'completed': False}
        result_lock = threading.Lock()

        def callback(data):
            try:
                result = handler_func(**data['arguments'])
                with result_lock:
                    result_holder['result'] = result
                    result_holder['completed'] = True
            except Exception as e:
                with result_lock:
                    result_holder['exception'] = e
                    result_holder['completed'] = True

        if not TaskManager.is_running():
            TaskManager.start()

        task_id = TaskManager.post(command="execute_handler", callback=callback, data={"arguments": arguments})
        if not task_id:
            raise Exception("Failed to post task to TaskManager")

        timeout = MAIN_THREAD_TASK_TIMEOUT_S
        start_time = time.time()
        while enforce_timeout is False or (time.time() - start_time < timeout):
            with result_lock:
                if result_holder['completed']:
                    if result_holder['exception'] is not None:
                        raise result_holder['exception']
                    return result_holder['result']
            await asyncio.sleep(0.01)

        # Timed out. Try to cancel the still-pending task so it never runs after we've given up.
        # cancel() returns True ONLY if it removed a task that had NOT yet started - in that case
        # the operation truly never ran. If it returns False the callback was already CLAIMED by
        # the main thread: it is running (or finished) and CANNOT be interrupted, so its side
        # effect (e.g. a cloud write or a committed design edit) may already be applying. We must
        # not lie that it was "cancelled before running" - that invites a blind retry -> double-apply.
        cancelled = TaskManager.cancel(task_id)
        with result_lock:
            if result_holder['completed']:
                # Finished in the cancel window - honor the real result, don't fake a timeout.
                if result_holder['exception'] is not None:
                    raise result_holder['exception']
                return result_holder['result']
        if cancelled:
            raise Exception(
                f"Handler execution timed out ({timeout}s); the operation was cancelled before it "
                "started running. No change was made - safe to retry.")
        raise Exception(
            f"Handler is still running after {timeout}s and could NOT be cancelled (an in-flight "
            "main-thread operation cannot be interrupted). It may still COMMIT its result. Do NOT "
            "blindly retry - re-check the design/document state first, then retry only if the "
            "change did not take effect. (For long operations, prefer a fire-and-poll tool.)")

    def _error(self, request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


# The only origins a browser may present. Matched against the PARSED hostname of the Origin
# header, so a host that merely contains a loopback label (localhost.evil.com) is not a member.
# '::1' is what urlsplit().hostname yields for http://[::1]:port - it strips the brackets.
_ALLOWED_ORIGIN_SCHEMES = frozenset({'http', 'https'})
_ALLOWED_ORIGIN_HOSTS = frozenset({'127.0.0.1', 'localhost', '::1'})


class MCPHandler(BaseHTTPRequestHandler):
    """HTTP request handler bridging HTTP <-> the MCP JSON-RPC server."""

    mcp_server: Optional[SimpleMCPServer] = None  # set on the subclass per server

    # ---- Streamable HTTP transport (MCP 2025-03-26) ----

    def _origin_ok(self) -> bool:
        """Reject cross-origin (DNS-rebinding) requests; allow no-Origin local tools.

        Per the spec security note, validate Origin. Local CLI clients (and our own
        probes) typically send no Origin header, which we allow; browsers send one,
        which must PARSE to a loopback origin.
        """
        origin = self.headers.get('Origin')
        if not origin:
            return True
        try:
            parts = urlsplit(origin)
            hostname, _port = parts.hostname, parts.port   # .port raises on a bad port
        except ValueError:
            return False        # unparseable Origin (bad IPv6 literal / port) - refuse, never allow
        # Compare the parsed scheme + host, never the raw string: substring containment would
        # admit http://localhost.evil.com and http://127.0.0.1.evil.com, which resolve to an
        # attacker's server and are exactly the DNS-rebinding case this guard exists for.
        # Origin: null is REFUSED - it is what a sandboxed (attacker-controlled) iframe sends, so
        # allowing it reopens the hole. A local client that legitimately sends null can send no
        # Origin header at all instead, which is allowed above.
        return parts.scheme in _ALLOWED_ORIGIN_SCHEMES and hostname in _ALLOWED_ORIGIN_HOSTS

    def do_POST(self):
        # MCP endpoint on /mcp (well-known) and "/" (convenience).
        if self.path not in (MCP_PATH, '/'):
            self.send_error(404, "Not Found")
            return
        if not self._origin_ok():
            self.send_error(403, "Origin not allowed")
            return
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            request_data = json.loads(post_data.decode('utf-8'))
        except (ValueError, json.JSONDecodeError):
            self.send_error(400, "Invalid JSON")
            return

        try:
            response = asyncio.run(self.mcp_server.handle_request(request_data))
        except Exception as e:
            self.send_error(500, str(e))
            return

        # Notifications/responses (no id) -> 202 Accepted, no body (spec rule 4).
        if response is None:
            self.send_response(202)
            self.send_header(SESSION_HEADER, self.mcp_server.session_id)
            origin = self.headers.get('Origin')
            if origin and self._origin_ok():
                self.send_header('Access-Control-Allow-Origin', origin)
            self.end_headers()
            return

        # Requests -> single JSON object (we use application/json, not SSE; spec rule 5).
        self._send_json(response)

    def do_GET(self):
        if not self._origin_ok():
            self.send_error(403, "Origin not allowed")
            return
        # Convenience/diagnostic endpoints (not part of the MCP transport).
        if self.path == '/health':
            self._send_json({"status": "healthy", "server": self.mcp_server.name, "version": self.mcp_server.server_info["version"]})
            return
        if self.path == '/tools':
            self._send_json(self.mcp_server._handle_tools_list(1))
            return
        # GET on the MCP endpoint = client asking to open a server->client SSE stream.
        # We don't offer one, so 405 (spec-compliant; the client falls back to POST).
        if self.path in ('/', MCP_PATH):
            self.send_response(405, "Method Not Allowed")
            self.send_header('Allow', 'POST')
            self.end_headers()
            return
        self.send_error(404, "Not Found")

    def do_DELETE(self):
        # Client requesting explicit session termination; we don't support it -> 405.
        if self.path in ('/', MCP_PATH):
            self.send_response(405, "Method Not Allowed")
            self.send_header('Allow', 'POST')
            self.end_headers()
            return
        self.send_error(404, "Not Found")

    def _send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header(SESSION_HEADER, self.mcp_server.session_id)
        # No permissive CORS header: this is a loopback-only server, not a browser
        # API, and the Origin check already restricts who may call it. Echoing the
        # caller's loopback Origin keeps legitimate same-machine browser clients
        # working without opening it to arbitrary sites.
        origin = self.headers.get('Origin')
        if origin and self._origin_ok():
            self.send_header('Access-Control-Allow-Origin', origin)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # silence default stderr logging


def start_server(host: str, port: int, items=None):
    """Start the MCP HTTP server on host:port in a background thread.

    Returns a dict:
        {"status": START_OK, "mcp": ..., "http_server": ..., "thread": ...}
        {"status": START_PORT_IN_USE, "port": port}     # bind hit EADDRINUSE
        {"status": START_ERROR, "message": "..."}        # any other failure

    The caller (entry.start) is responsible for surfacing the port-in-use case to
    the user (likely Autodesk's built-in MCP server holding 27182).
    """
    try:
        mcp = SimpleMCPServer()
        for item in (items or []):
            mcp.register(item)

        # Per-server handler subclass carrying its own mcp instance.
        handler_cls = type('BoundMCPHandler', (MCPHandler,), {'mcp_server': mcp})

        try:
            http_server = ThreadedHTTPServer((host, port), handler_cls)
        except OSError as e:
            if e.errno in (errno.EADDRINUSE, errno.EACCES) or getattr(e, 'winerror', None) == 10048:
                futil.log(f"MCP server: port {port} already in use (likely Fusion's built-in MCP server)")
                return {"status": START_PORT_IN_USE, "port": port}
            raise

        thread = threading.Thread(
            target=http_server.serve_forever,
            daemon=True,
            name=f"FE-MCP-Server-{host}:{port}",
        )
        thread.start()
        futil.log(f"MCP server started on http://{host}:{port}{MCP_PATH}")
        return {"status": START_OK, "mcp": mcp, "http_server": http_server, "thread": thread}
    except Exception:
        futil.handle_error('mcp_server.start_server')
        return {"status": START_ERROR, "message": "Failed to start MCP server (see Text Commands log)"}


def verify_ownership(host: str, port: int, timeout: float = 2.0):
    """Probe GET http://host:port/health and check who is answering.

    Layer-2 collision check: even after a successful bind, confirm the
    server replying on the port is actually ours and not, say, Autodesk's built-in
    server that won an earlier race. Returns one of:
        "ours"      -> /health reports our SERVER_NAME (all good)
        "foreign"   -> something else answered (e.g. Autodesk's "MCP HTTP Server")
        "unreachable" -> nothing answered / error (treat as inconclusive)

    Runs from entry.start() on the main thread; our own server answers on its
    background thread, so this self-request does not deadlock. Kept short-timeout
    and fully defensive so it can never hang Fusion startup.
    """
    import json as _json
    import urllib.request

    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return "ours" if data.get("server") == SERVER_NAME else "foreign"
    except Exception:
        return "unreachable"


def stop_server(http_server, thread, timeout: float = 5) -> bool:
    """Shut down the HTTP server and join its thread. Safe to call with None."""
    try:
        if http_server:
            http_server.shutdown()
            http_server.server_close()
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
            return not thread.is_alive()
        return True
    except Exception:
        futil.handle_error('mcp_server.stop_server')
        return False
