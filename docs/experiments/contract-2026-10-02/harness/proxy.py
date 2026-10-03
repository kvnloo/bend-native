"""Loopback capture proxy in front of the local model server (contract lane harness).

Hermes talks to http://127.0.0.1:<listen>/v1 exactly as it would to the model server. For every request the
proxy stores the ORIGINAL request bytes Hermes sent (that is what the paired comparison hashes), then forwards
the request to the upstream server. For POST .../chat/completions it sets the per-pair sampling seed and
temperature 0 on the forwarded copy only (harness determinism control, identical in every arm); the stored
body is the unmodified Hermes payload. Responses (including SSE streams) are relayed byte for byte and also
stored, so the assistant tool calls can be reconstructed. Nothing here is plugin code.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOP = {"connection", "keep-alive", "proxy-connection", "transfer-encoding", "te", "trailer", "upgrade",
       "content-length", "host"}


class Capture:
    """Mutable capture target, switched by the driver between runs (runs are sequential)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.dir: Path | None = None
        self.seed: int | None = None
        self.n = 0

    def start(self, directory: Path, seed: int):
        with self.lock:
            directory.mkdir(parents=True, exist_ok=True)
            self.dir, self.seed, self.n = directory, seed, 0

    def stop(self):
        with self.lock:
            self.dir = None

    def next(self):
        with self.lock:
            self.n += 1
            return self.n, self.dir, self.seed


def make_handler(capture: Capture, upstream_host: str, upstream_port: int):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _relay(self):
            t0 = time.time()
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            n, directory, seed = capture.next()
            forwarded = body
            injected = None
            if self.command == "POST" and self.path.rstrip("/").endswith("/chat/completions") and body:
                try:
                    payload = json.loads(body)
                    injected = {"hermes_sent": {k: payload.get(k) for k in ("temperature", "seed", "top_p")}}
                    payload["temperature"] = 0
                    if seed is not None:
                        payload["seed"] = seed
                    injected["forwarded"] = {"temperature": 0, "seed": seed}
                    forwarded = json.dumps(payload).encode()
                except ValueError:
                    injected = {"error": "request body not JSON; forwarded unchanged"}
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            headers["Content-Length"] = str(len(forwarded))
            status, out_headers, chunks, error = None, [], [], None
            try:
                conn = http.client.HTTPConnection(upstream_host, upstream_port, timeout=900)
                conn.request(self.command, self.path, body=forwarded if forwarded else None, headers=headers)
                resp = conn.getresponse()
                status = resp.status
                out_headers = [(k, v) for k, v in resp.getheaders() if k.lower() not in HOP]
                self.send_response(status)
                for k, v in out_headers:
                    self.send_header(k, v)
                self.send_header("Connection", "close")
                self.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    self.wfile.write(chunk)
                    self.wfile.flush()
                conn.close()
            except Exception as exc:  # recorded, the client sees a 502
                error = f"{type(exc).__name__}: {exc}"
                if status is None:
                    try:
                        self.send_response(502)
                        self.send_header("Connection", "close")
                        self.end_headers()
                    except Exception:
                        pass
            self.close_connection = True
            if directory is not None:
                stem = f"{n:04d}"
                if body:
                    (directory / f"{stem}.req").write_bytes(body)
                raw = b"".join(chunks)
                (directory / f"{stem}.resp").write_bytes(raw)
                row = {"n": n, "method": self.command, "path": self.path, "status": status, "error": error,
                       "req_bytes": len(body), "req_sha256": hashlib.sha256(body).hexdigest() if body else None,
                       "resp_bytes": len(raw), "resp_sha256": hashlib.sha256(raw).hexdigest(),
                       "injected": injected, "t_start": t0, "t_end": time.time()}
                with (directory / "index.jsonl").open("a") as fh:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")

        do_GET = do_POST = do_HEAD = do_DELETE = do_PUT = _relay

    return Handler


def serve(listen_port: int, upstream_port: int, capture: Capture) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", listen_port), make_handler(capture, "127.0.0.1", upstream_port))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="capture-proxy", daemon=True).start()
    return server
