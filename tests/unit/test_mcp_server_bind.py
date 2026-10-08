"""The server's port bind: a reload rebinds past TIME_WAIT, and a live listener is still refused."""

import errno
import os
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler

import pytest

from conftest import load_mcp_server

posix_only = pytest.mark.skipif(os.name == "nt", reason="Windows binds past TIME_WAIT already")


@pytest.fixture(scope="module")
def mcp():
    return load_mcp_server()


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@posix_only
def test_a_rebind_right_after_serving_succeeds(mcp):
    # the server closes each answered connection first, leaving it in TIME_WAIT on the port.
    port = _free_port()
    first = mcp.ThreadedHTTPServer(("127.0.0.1", port), _Ok)
    thread = threading.Thread(target=first.serve_forever, daemon=True)
    thread.start()
    for _ in range(3):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read()
    first.shutdown()
    first.server_close()
    thread.join(5)
    second = mcp.ThreadedHTTPServer(("127.0.0.1", port), _Ok)
    try:
        assert second.server_address == ("127.0.0.1", port)
    finally:
        second.server_close()


@posix_only
def test_a_wildcard_listener_on_the_port_is_refused(mcp):
    port = _free_port()
    other = socket.socket()
    other.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    other.bind(("0.0.0.0", port))
    other.listen()
    try:
        with pytest.raises(OSError) as raised:
            mcp.ThreadedHTTPServer(("127.0.0.1", port), _Ok)
        assert raised.value.errno == errno.EADDRINUSE
    finally:
        other.close()
