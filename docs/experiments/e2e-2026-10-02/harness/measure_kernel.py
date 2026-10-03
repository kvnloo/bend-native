"""Cold-kernel bootstrap vs warm verdict latency through the INSTALLED bend plugin, in one Hermes process.

usage (inside the e2e launcher, under quiet-timed): python measure_kernel.py <project_dir> <n_warm> <out.json>
The plugin is discovered from the run's private HERMES_HOME exactly as Hermes loads it (PluginManager), and every
call goes through the registered bend_verify tool (tools.registry.dispatch). The first call builds the plugin's
private session kernel (kernel_strategy session-bootstrap); later calls reuse it (session-pinned).
Timing is time.perf_counter() around each dispatch; the receipt's own duration_ms is recorded but not used.
"""
import json
import sys
import time

project, n_warm, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
t0 = time.perf_counter()
from hermes_cli.plugins import PluginManager  # noqa: E402
from tools.registry import registry  # noqa: E402

manager = PluginManager()
manager.discover_and_load()
assert manager._plugins["bend"].error is None, manager._plugins["bend"].error
load_ms = (time.perf_counter() - t0) * 1000
calls = []
for i in range(1 + n_warm):
    t = time.perf_counter()
    receipt = json.loads(registry.dispatch("bend_verify", {"project_dir": project}))
    wall_ms = (time.perf_counter() - t) * 1000
    calls.append({"i": i, "wall_ms": round(wall_ms, 3), "verdict": receipt.get("verdict"),
                  "success": receipt.get("success"), "kernel_strategy": receipt.get("kernel_strategy"),
                  "kernel_cache_state": receipt.get("kernel_cache_state"),
                  "kernel_sha256_after": receipt.get("kernel_sha256_after"),
                  "verdict_timeout_seconds": receipt.get("verdict_timeout_seconds"),
                  "receipt_duration_ms": receipt.get("duration_ms"), "receipt_id": receipt.get("receipt_id"),
                  "input_manifest_sha256": receipt.get("input_manifest_sha256"),
                  "bend_sha256": receipt.get("bend_sha256")})
with open(out, "x") as fh:
    json.dump({"schema": "bend_e2e.kernel_latency.v1", "plugin_load_ms": round(load_ms, 3), "calls": calls}, fh,
              indent=1)
print(json.dumps({"cold_ms": calls[0]["wall_ms"], "warm_ms": [c["wall_ms"] for c in calls[1:]],
                  "strategies": sorted({c["kernel_strategy"] for c in calls})}))
