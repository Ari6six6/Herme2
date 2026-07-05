"""A diff-time reference responder over the recorded ground-truth samples.

This is NOT the twin. The twin is the real reconstructed software running in a
container on the sandbox host (see hermes/twin/deploy.py). This stdlib server only
replays the recorded request/response samples byte-for-byte (504 for anything it
hasn't seen), which is useful for *diffing* a candidate against ground truth
offline — never as the thing the agent builds against.

Pure standard library and no `hermes` imports on purpose: this file plus a model
directory can be copied anywhere and run with `python3 server.py <dir> <port>` —
nothing to install.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit


def request_key(method, path, query="", body=None):
    """Canonical request identity — MUST match hermes.twin.model.request_key."""
    method = (method or "GET").upper()
    split = urlsplit(path)
    p = split.path or "/"
    raw_query = query or split.query or ""
    qn = urlencode(sorted(parse_qsl(raw_query, keep_blank_values=True)))
    b = (body or "").strip()
    return f"{method} {p}?{qn}\n{b}"


def load_exchanges(model_dir):
    """Load the model's exchanges as {key: record} for O(1) lookup. Stdlib only."""
    path = Path(model_dir) / "exchanges.jsonl"
    table = {}
    if not path.exists():
        return table
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = request_key(rec.get("method", "GET"), rec.get("path", "/"),
                          rec.get("query", ""), rec.get("request_body"))
        table[key] = rec
    return table


class TwinHandler(BaseHTTPRequestHandler):
    table: dict = {}          # populated by make_server
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):  # keep the box quiet
        pass

    def _serve(self, method):
        split = urlsplit(self.path)
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length).decode("utf-8", "replace") if length else None
        key = request_key(method, split.path, split.query, body)
        rec = self.table.get(key)
        if rec is None:
            self._miss(method, split.path, split.query)
            return
        payload = (rec.get("response_body") or "").encode("utf-8")
        self.send_response(rec.get("status", 200))
        ctype = rec.get("content_type") or "application/octet-stream"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Twin", "exact")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def _miss(self, method, path, query):
        payload = json.dumps({
            "twin_miss": {"method": method, "path": path, "query": query},
            "detail": "The twin has no real captured response for this request. "
                      "It does not fabricate one. Grow the model to cover it.",
        }).encode("utf-8")
        self.send_response(504)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Twin", "miss")
        self.end_headers()
        if method != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        self._serve("GET")

    def do_HEAD(self):
        self._serve("HEAD")

    def do_POST(self):
        self._serve("POST")

    def do_PUT(self):
        self._serve("PUT")

    def do_DELETE(self):
        self._serve("DELETE")

    def do_PATCH(self):
        self._serve("PATCH")


def make_server(model_dir, port=0, host="127.0.0.1"):
    """Build (but don't start) a twin server for a model directory. port=0 picks
    a free port — read it back from server.server_address[1]."""
    handler = type("BoundTwinHandler", (TwinHandler,),
                   {"table": load_exchanges(model_dir)})
    return ThreadingHTTPServer((host, port), handler)


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("usage: server.py <model-dir> [port]", file=sys.stderr)
        return 2
    model_dir = argv[0]
    port = int(argv[1]) if len(argv) > 1 else 8900
    server = make_server(model_dir, port)
    bound = server.server_address[1]
    print(f"twin up: http://127.0.0.1:{bound} "
          f"({len(server.RequestHandlerClass.table)} exchanges)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
