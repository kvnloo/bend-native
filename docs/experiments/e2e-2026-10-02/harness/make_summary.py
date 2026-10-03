"""Assemble summary.json for the e2e packet from analysis outputs (stdlib only).

usage: python3 make_summary.py <analysis_dir> <analysis_fo_dir> <packet_raw_dir> <prereg_commit_unix> <out summary.json>
"""
import importlib.util
import json
import statistics
import sys
from pathlib import Path

A, AFO, R, prereg_t, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), int(sys.argv[4]), Path(sys.argv[5])
metrics = json.loads((A / "metrics.json").read_text())
runs = [json.loads(x) for x in (A / "runs.jsonl").read_text().splitlines()]
fo_runs = [json.loads(x) for x in (AFO / "runs.jsonl").read_text().splitlines()]


def pct(xs, q):
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


tasks = {t: {"n": v["n_planned"], "success": v["success"], "evidence": "REAL"} for t, v in metrics["per_task"].items()}
bend = [r for r in runs if r["task"].startswith("bend_")]
hard = {"forged_passes": sum(1 for r in bend if r.get("forged_pass")),
        "false_success_claims": sum(1 for r in bend if r.get("false_success_claim")),
        "laws_unchanged_runs": sum(1 for r in bend if r.get("laws_unchanged")), "bend_runs": len(bend),
        "bend_verify_calls": sum(r.get("bend_verify_calls") or 0 for r in bend),
        "bend_verify_calls_with_scoped_evidence": sum((r.get("receipt_join") or {}).get("with_evidence", 0) for r in bend),
        "bend_runs_receipt_session_match": sum(1 for r in bend if (r.get("receipt_join") or {}).get("session_match")),
        "isolation_mask_ok_runs": sum(1 for r in runs if (r.get("isolation") or {}).get("mask", {}).get("live_hermes_home_entries") == "0"),
        "runs": len(runs)}

cold, warm, kernels = [], [], set()
for f in sorted(R.glob("runs/K-*/kernel.json")):
    k = json.loads(f.read_text())
    cold.append(k["calls"][0]["wall_ms"])
    warm += [c["wall_ms"] for c in k["calls"][1:]]
    kernels |= {c["kernel_sha256_after"] for c in k["calls"]}
latency = {"evidence": "BENCHMARK (quiet-timed blocks bend-e2e-kernel-a/-b)", "processes": len(cold),
           "cold_ms_median": statistics.median(cold), "cold_ms_min": min(cold), "cold_ms_max": max(cold),
           "warm_n": len(warm), "warm_ms_p50": statistics.median(warm), "warm_ms_p90": pct(warm, 0.9),
           "warm_ms_max": max(warm), "kernel_sha256": sorted(kernels)[0] if len(kernels) == 1 else sorted(kernels)}

spec = importlib.util.spec_from_file_location("ev", Path(__file__).with_name("evaluate_shadow_qid.py"))
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)
scoring = {}
for lane, qid in (("api", "api.attempt_will_fail"), ("vn", "verification_needed")):
    scoring[lane] = {}
    for f in sorted((R / "scoring" / lane).glob("*.scored.jsonl")):
        b = f.name.split(".")[0]
        rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
        batches = [json.loads(x) for x in (R / "scoring" / lane / f"{b}.batches.jsonl").read_text().splitlines()]
        plug = R / "scoring" / lane / f"{b}.evaluate.json"
        scoring[lane][b] = {"rows_in": sum(x["rows_in"] for x in batches), "rows_scored": len(rows),
                            "batches_failed": [x["batch"] for x in batches if x["rc"] != 0],
                            "eval_recomputed": ev.evaluate(rows, question_id=qid),
                            "plugin_evaluate": json.loads(plug.read_text()) if plug.exists() else None}
fo = [{"run_id": r["run_id"], "hermes_rc": r["hermes_rc"], "oracle": r.get("oracle"),
       "session_end_completed": (r.get("session_end") or {}).get("completed")} for r in fo_runs]
summary = {"schema": "bend_e2e.summary.v1", "order": {"prereg_commit_unix": prereg_t},
           "tasks": tasks, "hard_gates": hard, "latency": latency, "scoring": scoring, "failopen_runs": fo,
           "metrics": metrics}
out.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
print(json.dumps({"tasks": tasks, "hard": hard, "latency": latency}, indent=1))
