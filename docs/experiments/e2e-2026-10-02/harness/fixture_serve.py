"""Serve the unmodified jev-use loopback form fixture on an ephemeral port; write the port to a file.

usage: fixture_serve.py <jev-use fixture_server.py path> <port file>
"""
import importlib.util
import sys

spec = importlib.util.spec_from_file_location("fixture_server", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
server = mod.FixtureServer(("127.0.0.1", 0))
with open(sys.argv[2] + ".tmp", "w") as fh:
    fh.write(str(server.server_port))
import os

os.replace(sys.argv[2] + ".tmp", sys.argv[2])
try:
    server.serve_forever()
finally:
    server.server_close()
