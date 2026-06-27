# Copyright (c) Fusion-Essentials contributors
# Dual-licensed under the MIT and Apache-2.0 licenses; see LICENSE-MIT and LICENSE-APACHE.
#
# Adapted from Autodesk's Fusion MCP add-in sample (MIT-licensed).

"""A dependency-free MCP server over HTTP that runs inside Fusion's Python.

Implements the MCP JSON-RPC methods we need (initialize, tools/list, tools/call,
resources/list, resources/read) by hand, so no external packages are required.

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

from ....lib import fusion360utils as futil
from ..mcp_primitives.item import Item
from .task_manager import TaskManager

# The MCP path served by Fusion's built-in server; we mirror it so clients
# configured for the well-known endpoint reach us unchanged.
MCP_PATH = '/mcp'

# MCP protocol version we implement (Streamable HTTP transport, 2025-03-26).
# We echo the client's requested version on initialize when it sends one.
PROTOCOL_VERSION = '2025-03-26'

# Header names (Streamable HTTP transport).
SESSION_HEADER = 'Mcp-Session-Id'
PROTOCOL_VERSION_HEADER = 'MCP-Protocol-Version'

# Our server's identifying name. Returned by GET /health and used by the
# post-start self-check to confirm WE are the server answering on the port
# (vs. Autodesk's built-in server, which reports "MCP HTTP Server").
SERVER_NAME = "Fusion-Essentials MCP Server"

# Result codes returned by start_server() so entry.py can react appropriately.
START_OK = 'ok'
START_PORT_IN_USE = 'port_in_use'
START_ERROR = 'error'


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """HTTP server that handles each request on its own daemon thread."""
    daemon_threads = True
    allow_reuse_address = False  # we WANT bind to fail loudly if 27182 is taken


class SimpleMCPServer:
    """Routes MCP JSON-RPC requests to registered tool/resource handlers."""

    def __init__(self, name: str = SERVER_NAME):
        self.name = name
        # Session id assigned at initialize and echoed back to the client on every
        # response. Generated lazily so each server instance has a stable id.
        self.session_id = uuid.uuid4().hex
        self.tools: Dict[str, Item] = {}
        self.resources: Dict[str, Item] = {}
        self.server_info = {"name": name, "version": "0.1.0"}

    def register(self, item: Item):
        if not isinstance(item, Item):
            raise ValueError("Can only register Item instances")
        item_type = item.get_type()
        if item_type == "tool":
            self.tools[item.primitive.name] = item
            futil.log(f"MCP tool registered: {item.primitive.name}")
        elif item_type == "resource":
            self.resources[item.primitive.uri] = item
            futil.log(f"MCP resource registered: {item.primitive.uri}")
        elif item_type == "prompt":
            futil.log(f"MCP prompt registered: {item.primitive.name} (not served yet)")
        else:
            raise ValueError(f"Unknown item type: {item_type}")

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        try:
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params", {})

            # Notifications (no "id") get no response body; caller returns 202.
            is_notification = "id" not in request
            if is_notification:
                # e.g. notifications/initialized, notifications/cancelled — accept silently.
                return None

            if method == "initialize":
                return self._handle_initialize(request_id, params)
            elif method == "ping":
                return {"jsonrpc": "2.0", "id": request_id, "result": {}}
            elif method == "tools/list":
                return self._handle_tools_list(request_id)
            elif method == "tools/call":
                return await self._handle_tools_call(request_id, params)
            elif method == "resources/list":
                return self._handle_resources_list(request_id)
            elif method == "resources/read":
                return await self._handle_resources_read(request_id, params)
            else:
                return self._error(request_id, -32601, f"Method not found: {method}")
        except Exception as e:
            return self._error(request.get("id"), -32603, str(e))

    def _handle_initialize(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        # Echo the client's requested protocol version when provided, else default.
        client_version = (params or {}).get("protocolVersion")
        protocol_version = client_version or PROTOCOL_VERSION
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {}, "resources": {"listChanged": False}},
                "serverInfo": self.server_info,
            },
        }

    def _handle_tools_list(self, request_id: Any) -> Dict[str, Any]:
        tools = [
            {
                "name": name,
                "description": item.primitive.description,
                "inputSchema": item.primitive.input_schema,
            }
            for name, item in self.tools.items()
        ]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": tools}}

    async def _handle_tools_call(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        tool_name = params.get("name")
        arguments = params.get("arguments", {})
        if tool_name not in self.tools:
            return self._error(request_id, -32601, f"Tool not found: {tool_name}")
        futil.log(f"MCP calling tool: {tool_name}")
        try:
            item = self.tools[tool_name]
            if item.run_on_main_thread:
                result = await self._execute_on_main_thread(
                    item.handler, arguments,
                    enforce_timeout=getattr(item, "enforce_timeout", True))
            else:
                result = item.handler(**arguments)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as e:
            futil.handle_error(f"MCP tool '{tool_name}'")
            return self._error(request_id, -32603, f"Tool execution error: {e}")

    async def _execute_on_main_thread(self, handler_func, arguments: Dict[str, Any],
                                      enforce_timeout: bool = True) -> Any:
        """Run handler_func(**arguments) on Fusion's main thread via TaskManager.

        enforce_timeout=False waits indefinitely for the callback to complete — for tools (e.g.
        execute_api_script) whose work cannot be interrupted and would still commit, so a timeout
        would only report a false failure for a change that actually applied.
        """
        import time

        result_container = {'result': None, 'exception': None, 'completed': False}
        result_lock = threading.Lock()

        def callback(data):
            try:
                result = handler_func(**data['arguments'])
                with result_lock:
                    result_container['result'] = result
                    result_container['completed'] = True
            except Exception as e:
                with result_lock:
                    result_container['exception'] = e
                    result_container['completed'] = True

        if not TaskManager.is_running():
            TaskManager.start()

        task_id = TaskManager.post(command="execute_handler", callback=callback, data={"arguments": arguments})
        if not task_id:
            raise Exception("Failed to post task to TaskManager")

        timeout = 30
        start_time = time.time()
        while enforce_timeout is False or (time.time() - start_time < timeout):
            with result_lock:
                if result_container['completed']:
                    if result_container['exception'] is not None:
                        raise result_container['exception']
                    return result_container['result']
            await asyncio.sleep(0.01)

        # Timed out. Try to cancel the still-pending task so it never runs after we've given up.
        # cancel() returns True ONLY if it removed a task that had NOT yet started — in that case
        # the operation truly never ran. If it returns False the callback was already CLAIMED by
        # the main thread: it is running (or finished) and CANNOT be interrupted, so its side
        # effect (e.g. a cloud write or a committed design edit) may already be applying. We must
        # not lie that it was "cancelled before running" — that invites a blind retry → double-apply.
        cancelled = TaskManager.cancel(task_id)
        with result_lock:
            if result_container['completed']:
                # Finished in the cancel window — honor the real result, don't fake a timeout.
                if result_container['exception'] is not None:
                    raise result_container['exception']
                return result_container['result']
        if cancelled:
            raise Exception(
                f"Handler execution timed out ({timeout}s); the operation was cancelled before it "
                "started running. No change was made — safe to retry.")
        raise Exception(
            f"Handler is still running after {timeout}s and could NOT be cancelled (an in-flight "
            "main-thread operation cannot be interrupted). It may still COMMIT its result. Do NOT "
            "blindly retry — re-check the design/document state first, then retry only if the "
            "change did not take effect. (For long operations, prefer a fire-and-poll tool.)")

    def _handle_resources_list(self, request_id: Any) -> Dict[str, Any]:
        resources = [
            item.primitive.to_dict()
            for item in self.resources.values()
            if item.primitive.uri
        ]
        return {"jsonrpc": "2.0", "id": request_id, "result": {"resources": resources}}

    async def _handle_resources_read(self, request_id: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        uri = params.get("uri")
        item = self.resources.get(uri)
        if not item:
            return self._error(request_id, -32601, f"Resource not found: {uri}")
        try:
            handler_args = {k: v for k, v in params.items() if k != "uri"}
            if item.run_on_main_thread:
                result = await self._execute_on_main_thread(item.handler, handler_args)
            else:
                result = item.handler(**handler_args)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "contents": [{
                        "uri": uri,
                        "mimeType": item.primitive.mime_type or "application/json",
                        "text": result,
                    }]
                },
            }
        except Exception as e:
            futil.handle_error(f"MCP resource '{uri}'")
            return self._error(request_id, -32603, f"Resource read error: {e}")

    def _error(self, request_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class MCPHandler(BaseHTTPRequestHandler):
    """HTTP request handler bridging HTTP <-> the MCP JSON-RPC server."""

    mcp_server: Optional[SimpleMCPServer] = None  # set on the subclass per server

    # ---- Streamable HTTP transport (MCP 2025-03-26) ----

    def _origin_ok(self) -> bool:
        """Reject cross-origin (DNS-rebinding) requests; allow no-Origin local tools.

        Per the spec security note, validate Origin. Local CLI clients (and our own
        probes) typically send no Origin header, which we allow; browsers send one,
        which must be a loopback origin.
        """
        origin = self.headers.get('Origin')
        if not origin:
            return True
        return ('127.0.0.1' in origin) or ('localhost' in origin) or origin == 'null'

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
            self._send_json({"status": "healthy", "server": self.mcp_server.name})
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
