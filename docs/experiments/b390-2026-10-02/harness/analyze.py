#!/usr/bin/env python3
"""Aggregate B390 raw records into summary.json and evaluate the #390 acceptance (A1-A5).

Usage: analyze.py <raw-dir> <summary.json>
<raw-dir> layout (scrubbed copy of the lane outputs):
  online/ offline/ workers/ patched/   rows.jsonl, notes.jsonl, receipts/, *-outer.json, worker*/rows.jsonl
  cli/<run>/                           cli.meta, cli.stdout, netns.dev, receipt.json
Pure standard library plus harness/fixtures.py; never imports the plugin.
"""
from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixtures as F  # noqa: E402

PACKAGES, IDS, _, PROJECTS, CLOSURE = F.build()


def expected_identity(project, name_override=None):
    closure = CLOSURE[project]
    keys, names = list(closure["packages"]), dict(closure["names"])
    if name_override:
        names, keys = dict(name_override), []
        for key in names.values():
            keys.append(key)
            for other, pid in IDS.items():
                if pid in PACKAGES[key]["Main.bend"].decode() and other not in keys:
                    keys.append(other)
    dep = {IDS[k] + "/" + rel: data for k in keys for rel, data in PACKAGES[k].items()}
    dep.update({"names/" + nv: (IDS[k] + "\n").encode() for nv, k in names.items()})
    combined = {**{"project/" + k: v for k, v in PROJECTS[project].items()},
                **{"dependencies/" + k: v for k, v in dep.items()}}
    return {"dependency_packages": {IDS[k]: F.sha(F.manifest(PACKAGES[k])) for k in keys},
            "dependency_names": {nv: IDS[k] for nv, k in names.items()},
            "dependency_manifest_sha256": F.input_manifest(dep),
            "input_manifest_sha256": F.input_manifest(combined)}


REBOUND = {"O8d_binding_rebound_fresh_verify", "O13a_replay_hub_rebound_refetch"}
FORGE = {"O5_mutated_forging", "F5_mutated_forging_offline"}


def project_for(row):
    if row["cell"] in FORGE:
        return {"H": "HF", "N": "NF"}[row["kind"]]
    return row["kind"]


def override_for(row):
    if row["cell"] in REBOUND and row["step"] in ("verify", "fresh_verify_control"):
        return {"sample@1.0.0.0": "Q_alt"}
    return None


def load_rows(raw: Path):
    rows = []
    for path in sorted(raw.glob("*/rows.jsonl")) + sorted(raw.glob("*/worker*/rows.jsonl")):
        base = path.parent
        for line in path.read_text().splitlines():
            row = json.loads(line)
            row["_receipt"] = json.loads((base / row["receipt_file"]).read_text())
            row["_dir"] = base.relative_to(raw).as_posix()
            rows.append(row)
    return rows


def load_cli(raw: Path):
    runs = []
    for run in sorted((raw / "cli").glob("*")):
        meta = dict(line.split("=", 1) for line in (run / "cli.meta").read_text().split())
        parts = run.name.split("-")          # cli-<mode>-<kind>-<nn>
        mode, kind = parts[1], parts[2]
        try:
            out = json.loads((run / "cli.stdout").read_text())
        except (OSError, ValueError):
            out = {"success": False, "code": "unparseable_stdout"}
        lo_only = [l.split(":")[0].strip() for l in (run / "netns.dev").read_text().splitlines()[2:] if l.strip()] == ["lo"]
        runs.append({"run": run.name, "mode": mode, "kind": kind, "rc": int(meta.get("cli_rc", -1)),
                     "outcome": out.get("verdict") or ("error:" + str(out.get("code"))),
                     "success": bool(out.get("success")), "replay_match": out.get("replay_match"),
                     "kernel_strategy": out.get("kernel_strategy"), "kernel_sha256_after": out.get("kernel_sha256_after"),
                     "receipt_file_written": (run / "receipt.json").exists(), "netns_lo_only": lo_only,
                     "_receipt": out})
    return runs


def identity_check(receipt, project, override=None):
    exp = expected_identity(project, override)
    return [k for k in exp if receipt.get(k) != exp[k]]


def main(raw: Path, summary_path: Path):
    rows = load_rows(raw)
    cli = load_cli(raw)
    cells = defaultdict(lambda: {"n": 0, "outcomes": Counter(), "as_expected": 0, "successes": 0,
                                 "identity_mismatches": 0, "expect": None})
    deviations, a1_fail, success_rows = [], [], 0
    for row in rows:
        key = f"{row['cell']}|{row['kind']}|{row['step'] if not row['step'].startswith(('attempt', 'worker')) else row['step'].rstrip('0123456789')}"
        cell = cells[key]
        cell["n"] += 1
        cell["outcomes"][row["outcome"]] += 1
        cell["as_expected"] += int(row["as_expected"])
        cell["successes"] += int(row["success"])
        cell["expect"] = row["expect"]
        if not row["as_expected"]:
            deviations.append({k: row.get(k) for k in ("_dir", "cell", "kind", "run", "step", "outcome", "expect", "receipt_file", "identity")})
        plugin_success = row["success"] and row["step"] != "raw_bend_control" and row["kind"] in ("H", "N")
        if plugin_success:
            success_rows += 1
            missing = identity_check(row["_receipt"], project_for(row), override_for(row))
            if missing:
                cell["identity_mismatches"] += 1
                a1_fail.append({"cell": row["cell"], "kind": row["kind"], "run": row["run"], "step": row["step"], "mismatched": missing})
    for run in cli:
        key = f"D_cli_{run['mode']}|{run['kind']}|cli"
        cell = cells[key]
        expect_ok = run["rc"] == 0 and run["outcome"] == "pass" and (run["mode"] == "verify" or run["replay_match"] is True)
        cell["n"] += 1
        cell["outcomes"][run["outcome"]] += 1
        cell["as_expected"] += int(expect_ok)
        cell["successes"] += int(run["success"])
        cell["expect"] = ["pass (exit 0" + (", replay_match)" if run["mode"] == "replay" else ")")]
        if not expect_ok:
            deviations.append({"cell": key, "run": run["run"], "outcome": run["outcome"], "rc": run["rc"]})
        if run["success"]:
            success_rows += 1
            missing = identity_check(run["_receipt"], run["kind"])
            if missing:
                cell["identity_mismatches"] += 1
                a1_fail.append({"cell": key, "run": run["run"], "mismatched": missing})

    def plugin_rows(names, steps=("verify", "replay")):
        return [r for r in rows if r["cell"] in names and r["step"] in steps]

    a2_cells = {"O13a_replay_hub_rebound_refetch", "O13b_replay_local_rebound", "O14_replay_race_injected",
                "F8_replay_offline_local_rebound", "E2_patched_replay_official_receipt"}
    a2 = plugin_rows(a2_cells, ("replay",))
    a3_cells = {"O4_mutated_benign", "O5_mutated_forging", "O6_extra_file", "O7_partial_package", "O8b_binding_garbage",
                "O11_replay_cache_mutated", "O15_mutate_cache_during_verdict", "O16_mutate_snapshot_during_verdict",
                "F4_mutated_benign_offline", "F5_mutated_forging_offline", "F9_mutate_cache_during_verdict_offline"}
    a3 = plugin_rows(a3_cells)
    controls = [r for r in rows if r["step"] == "raw_bend_control"]
    landed = [r for r in rows if r["cell"].startswith(("O15", "O16", "F9"))]
    f6 = [r for r in rows if r["cell"] == "F6_replay_offline"]
    cli_replay = [r for r in cli if r["mode"] == "replay"]
    online_calls = [r for r in rows if r["_dir"] == "online" and "hub_requests" in r]
    outer = {p: json.loads((raw / p / "offline-outer.json").read_text())
             for p in ("offline", "workers", "patched") if (raw / p / "offline-outer.json").exists()}
    netprobes = []
    for p in ("offline", "patched"):
        notes = raw / p / "notes.jsonl"
        if notes.exists():
            for line in notes.read_text().splitlines():
                note = json.loads(line)
                if note.get("kind") == "netprobe":
                    ifaces = [l.split(":")[0].strip() for l in note["proc_net_dev"] if l.strip()]
                    netprobes.append({"phase": p, "interfaces": ifaces, "hub_connect": note.get("hub_connect")})
    offline_success = [r for r in rows if r["_dir"] in ("offline", "patched") or r["_dir"].startswith("workers")]

    acceptance = {
        "A1": {"plugin_success_receipts": success_rows, "identity_mismatches": len(a1_fail), "failures": a1_fail,
               "pass": success_rows > 0 and not a1_fail},
        "A2": {"rows": len(a2), "successes": sum(r["success"] for r in a2),
               "outcomes": dict(Counter(f"{r['cell']}|{r['kind']}|{r['outcome']}" for r in a2)),
               "pass": len(a2) > 0 and not any(r["success"] for r in a2) and all(r["as_expected"] for r in a2)},
        "A3": {"rows": len(a3), "plugin_successes": sum(r["success"] for r in a3),
               "raw_bend_controls": len(controls), "raw_bend_control_passes": sum(r["success"] for r in controls),
               "during_verdict_rows": len(landed), "during_verdict_landed": sum(bool(r.get("landed")) for r in landed),
               "pass": len(a3) > 0 and not any(r["success"] for r in a3) and all(r["success"] for r in controls)},
        "A4": {"F6_rows": len(f6), "F6_pass_match": sum(r["success"] and r.get("replay_match") is True for r in f6),
               "cli_replay_runs": len(cli_replay), "cli_replay_pass_match": sum(r["success"] and r["replay_match"] is True and r["rc"] == 0 for r in cli_replay),
               "pass": len(f6) >= 10 and all(r["success"] and r.get("replay_match") for r in f6)
                       and len(cli_replay) >= 10 and all(r["success"] and r["replay_match"] for r in cli_replay)},
        "A5": {"online_plugin_calls_counted": len(online_calls),
               "online_plugin_calls_with_hub_requests": sum(1 for r in online_calls if r["hub_requests"] and not r["step"].startswith(("fetch",))),
               "offline_outer_hub_requests_during_inner": {p: o.get("hub_requests_during_inner") for p, o in outer.items()},
               "offline_netprobes": netprobes,
               "cli_netns_lo_only": sum(r["netns_lo_only"] for r in cli), "cli_runs": len(cli),
               "offline_phase_successes": sum(r["success"] for r in offline_success)},
    }
    a5 = acceptance["A5"]
    a5["pass"] = (a5["online_plugin_calls_counted"] > 0 and a5["online_plugin_calls_with_hub_requests"] == 0
                  and all(v == 0 for v in a5["offline_outer_hub_requests_during_inner"].values())
                  and len(outer) == 3
                  and all(p["interfaces"] == ["lo"] and "Refused" in str(p["hub_connect"]) for p in netprobes)
                  and a5["cli_netns_lo_only"] == a5["cli_runs"] > 0)
    summary = {
        "schema": "bend_native.b390.summary.v1",
        "rows": len(rows), "cli_runs": len(cli),
        "as_expected": sum(r["as_expected"] for r in rows) + sum(c["n"] for k, c in cells.items() if k.startswith("D_cli") and c["n"] == c["as_expected"]),
        "deviations": deviations,
        "acceptance": acceptance,
        "cells": {k: {**v, "outcomes": dict(v["outcomes"])} for k, v in sorted(cells.items())},
        "workers_outer": json.loads((raw / "workers" / "workers-outer.json").read_text()) if (raw / "workers" / "workers-outer.json").exists() else None,
        "kernel_hashes": sorted({r["kernel_sha256_after"] for r in rows if r.get("kernel_sha256_after")}
                                | {r["kernel_sha256_after"] for r in cli if r.get("kernel_sha256_after")}),
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=False) + "\n")
    print(json.dumps({"rows": summary["rows"], "cli_runs": summary["cli_runs"], "deviations": len(deviations),
                      **{k: v["pass"] for k, v in acceptance.items()}}, indent=2))


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
