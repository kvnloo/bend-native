#!/usr/bin/env python3
"""Check the AODL packet against PREREG.json from raw/ alone (stdlib only, no Bend or Hermes needed).

usage: verify_artifacts.py            check raw/ against PREREG.json and summary.json; exit 1 on any mismatch
       verify_artifacts.py --write    (re)derive summary.json from raw/
       AODL_MIRROR=<dir> verify_artifacts.py   also check raw/originals.sha256 against the unredacted mirror
       AODL_SCAN_EXTRA=<w1,w2> verify_artifacts.py   also scan for these words (e.g. the host name)
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
RAW = HERE / "raw"
PREREG = json.loads((HERE / "PREREG.json").read_text())
IDS = PREREG["identities"]
PRISTINE_MANIFEST = "04e7c7c5fe8bb5740a270facdffd03f21a57340b15bf9faa4b0428b9b5c423b0"
PROOF_SHA = PREREG["fixtures"]["pristine"]["PROOF.bend"]
HERMES_HEAD = "ad31bbf079f0ee559ce1b61c5f85178c4c3d9396"
BUILD = {"official": IDS["bend_official"], "patched": IDS["bend_patched"]}
STALE_INPUTS = "Project inputs differ from the recorded receipt; run a fresh verification"
STALE_RUNTIME = "Bend installation differs from the recorded receipt"
FORBIDDEN = re.compile(r"/mnt/|/home/|/workspace/|/tmp/claude" + "".join(f"|{re.escape(w)}" for w in os.environ.get("AODL_SCAN_EXTRA", "").split(",") if w))


def load_json(path: Path):
    return json.loads(path.read_text()) if path.is_file() else None


def text(run: str, name: str) -> str | None:
    path = RAW / run / name
    return path.read_text() if path.is_file() else None


def runtime_of(build: str) -> dict:
    b = BUILD[build]
    return {"bend": b["bin_bend"], "base.bend": b["base.bend"], "bendtt.lean": b["bendtt.lean"]}


def check_run(spec: dict) -> dict:
    run, kind, fx, build = spec["id"], spec["kind"], spec["fixture"], spec["verifier"]
    out = load_json(RAW / run / "meta" / "stdout")
    exit_code = int((text(run, "meta/exit_code") or "-1").strip())
    receipt = load_json(RAW / run / "receipt.json")
    replay_receipt = load_json(RAW / run / "replay-receipt.json")
    mask = text(run, "meta/mask.inside") or ""
    net = text(run, "meta/net.inside") or ""
    before, after = text(run, "meta/live-home-stat.before"), text(run, "meta/live-home-stat.after")
    row = {"id": run, "kind": kind, "fixture": fx, "verifier": build, "exit_code": exit_code,
           "evidence": "REAL", "checks": {}}
    c = row["checks"]
    c["isolation_masks_empty"] = all(f"{k}=0" in mask for k in ("live_hermes_home_entries", "real_home_entries", "run_user_entries")) and "x11_entries=\n" in mask + "\n"
    # line 1 is the live home directory itself; its mtime ticks every few seconds from the owner's own
    # services (raw/ambient-live-home-stat.txt), so only the snapshotted files are compared
    c["live_hermes_files_unchanged"] = before is not None and after is not None and before.splitlines()[1:] == after.splitlines()[1:]
    c["hermes_head"] = (text(run, "meta/worktree-head") or "").strip() == HERMES_HEAD
    if kind == "replay-offline":
        c["offline_netns_lo_only"] = re.search(r"^ifaces=lo\s*$", net, re.M) is not None
    if out is None:
        c["stdout_json"] = False
        return row
    row.update({k: out.get(k) for k in ("verdict", "execution_verdict", "success", "code", "error", "input_manifest_sha256",
                                         "input_file_count", "proof_sha256", "kernel_sha256_after", "kernel_strategy",
                                         "replay_match", "replay_differences", "replay_of") if k in out})
    if kind == "verify":
        c["runtime_identity"] = out.get("runtime_identity") == runtime_of(build)
        c["scope"] = out.get("verification_scope") == "bend-emitted-book" and out.get("source_semantics_attested") is False
        if fx == "pristine":
            c["kernel_sha"] = out.get("kernel_sha256_after") == BUILD[build]["expected_kernel_sha256"]
            c["kernel_strategy"] = out.get("kernel_strategy") == "session-bootstrap"
        c["proof_sha_unchanged"] = out.get("proof_sha256") == PROOF_SHA
        c["receipt_matches_stdout"] = receipt is not None and all(receipt.get(k) == out.get(k) for k in out)
        if fx == "pristine":
            c["P1_pass"] = exit_code == 0 and out.get("success") is True and out.get("verdict") == "pass" \
                and out.get("execution_verdict") == "pass" and out.get("stdout") == "ALL PROOFS CHECK\n"
            c["P1_manifest"] = out.get("input_manifest_sha256") == PRISTINE_MANIFEST and out.get("input_file_count") == 6
        else:
            c["P3_not_pass"] = out.get("success") is False and out.get("verdict") != "pass"
            c["P3_expected_fail_exit1"] = out.get("verdict") == "fail" and exit_code == 1
            c["P3_manifest_differs"] = out.get("input_manifest_sha256") != PRISTINE_MANIFEST
    else:
        rec = load_json(RAW / spec["receipt"] / "receipt.json") or {}
        if fx == "pristine" and spec["receipt"].split("-")[2][:3] == build[:3]:
            c["P2_replay_match"] = exit_code == 0 and out.get("replay_match") is True and out.get("replay_differences") == [] \
                and out.get("verdict") == "pass" and out.get("replay_of") == rec.get("receipt_id")
            c["P2_replay_receipt"] = replay_receipt is not None and replay_receipt.get("replay_match") is True
            c["P2_identity_equal"] = all(out.get(k) == rec.get(k) for k in
                                         ("input_manifest_sha256", "runtime_identity", "kernel_sha256_after", "execution_verdict"))
        elif fx in ("law-mut", "impl-mut"):
            c["P4_stale_inputs"] = exit_code == 2 and out.get("code") == "receipt_stale" and out.get("error") == STALE_INPUTS
            c["P4_no_replay_receipt"] = replay_receipt is None
        elif fx == "readme-mut":
            c["P5_replay_match"] = exit_code == 0 and out.get("replay_match") is True and out.get("verdict") == "pass"
        else:
            c["P6_stale_runtime"] = exit_code == 2 and out.get("code") == "receipt_stale" and out.get("error") == STALE_RUNTIME
            c["P6_no_replay_receipt"] = replay_receipt is None
    return row


def posthoc() -> list[dict]:
    """aodl-x-failrcpt-*: replay of a FAIL receipt (from aodl-m-law-*), added after the pre-registered runs."""
    rows = []
    for run in sorted(p.name for p in RAW.glob("aodl-x-failrcpt-*")):
        source = "aodl-m-law-" + run.split("-")[3] + "-01"
        out = load_json(RAW / run / "meta" / "stdout") or {}
        src = load_json(RAW / source / "receipt.json") or {}
        rows.append({"id": run, "replayed_receipt_from": source, "evidence": "REAL",
                     "exit_code": int((text(run, "meta/exit_code") or "-1").strip()), "code": out.get("code"), "error": out.get("error"),
                     "source_receipt": {"verdict": src.get("verdict"), "kernel_sha256_after": src.get("kernel_sha256_after"),
                                        "kernel_strategy": src.get("kernel_strategy")},
                     "offline_netns_lo_only": re.search(r"^ifaces=lo\s*$", text(run, "meta/net.inside") or "", re.M) is not None,
                     "isolation_masks_empty": all(f"{k}=0" in (text(run, "meta/mask.inside") or "") for k in
                                                  ("live_hermes_home_entries", "real_home_entries", "run_user_entries")),
                     "live_hermes_files_unchanged": (text(run, "meta/live-home-stat.before") or "x").splitlines()[1:]
                     == (text(run, "meta/live-home-stat.after") or "y").splitlines()[1:]})
    return rows


def derive() -> dict:
    rows = [check_run(spec) for spec in PREREG["runs"]]
    probes = sorted(p.name for p in RAW.glob("aodl-probe-*"))
    by = {r["id"]: r for r in rows}
    preds = {}
    for p in ("P1", "P2", "P3", "P4", "P5", "P6"):
        hits = [(r["id"], ok) for r in rows for k, ok in r["checks"].items() if k.startswith(p + "_")]
        runs = sorted({rid for rid, _ in hits})
        good = [rid for rid in runs if all(ok for r2, ok in hits if r2 == rid)]
        preds[p] = {"runs": len(runs), "held": len(good), "failed": sorted(set(runs) - set(good))}
    infra = {k: [r["id"] for r in rows if not r["checks"].get(k, True)]
             for k in ("isolation_masks_empty", "live_hermes_files_unchanged", "hermes_head", "offline_netns_lo_only",
                       "runtime_identity", "scope", "kernel_sha", "kernel_strategy", "proof_sha_unchanged", "receipt_matches_stdout")}
    p7 = {b: sorted({by[f"aodl-v-{b[:3]}-0{i}"].get("kernel_sha256_after") for i in (1, 2, 3)}) for b in ("official", "patched")}
    return {
        "schema": "bend_native.experiment.summary.v1",
        "id": PREREG["id"],
        "denominators": {"prereg_runs": len(PREREG["runs"]), "runs_with_raw": sum(1 for r in rows if (RAW / r["id"]).is_dir()),
                         "launcher_probes_not_results": probes},
        "predictions": preds,
        "P7_descriptive": {"pristine_verdicts": {b: sorted({by[f"aodl-v-{b[:3]}-0{i}"].get("verdict") for i in (1, 2, 3)}) for b in ("official", "patched")},
                           "pristine_manifests": sorted({by[f"aodl-v-{b}-0{i}"].get("input_manifest_sha256") for b in ("off", "pat") for i in (1, 2, 3)}),
                           "kernel_sha256_after_by_build": p7},
        "mutant_verdicts": {r["id"]: {"verdict": r.get("verdict"), "exit_code": r["exit_code"], "input_manifest_sha256": r.get("input_manifest_sha256"),
                                      "kernel_strategy": r.get("kernel_strategy"), "kernel_sha256_after": r.get("kernel_sha256_after"),
                                      "bend_stderr_head": ((load_json(RAW / r["id"] / "meta" / "stdout") or {}).get("stderr") or "").splitlines()[:1]}
                            for r in rows if r["kind"] == "verify" and r["fixture"] != "pristine"},
        "posthoc_not_preregistered": posthoc(),
        "invariant_failures": infra,
        "rows": rows,
    }


def scan() -> list[str]:
    bad = []
    for path in sorted(HERE.rglob("*")):
        if path.is_file() and path.name != "verify_artifacts.py":
            for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if FORBIDDEN.search(line):
                    bad.append(f"{path.relative_to(HERE)}:{i}")
    return bad


def mirror_check() -> list[str]:
    root = os.environ.get("AODL_MIRROR")
    if not root:
        return []
    bad = []
    for line in (RAW / "originals.sha256").read_text().splitlines():
        digest, rel = line.split("  ", 1)
        path = Path(root) / rel
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            bad.append(rel)
    return bad


def main() -> int:
    summary = derive()
    if "--write" in sys.argv:
        (HERE / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print("wrote summary.json")
        return 0
    problems = []
    committed = load_json(HERE / "summary.json")
    if committed != summary:
        problems.append("summary.json does not match a fresh derivation from raw/")
    if summary["denominators"]["runs_with_raw"] != summary["denominators"]["prereg_runs"]:
        problems.append("raw/ is missing pre-registered runs")
    problems += [f"local path or host name in {hit}" for hit in scan()]
    problems += [f"mirror mismatch: {rel}" for rel in mirror_check()]
    for p, v in summary["predictions"].items():
        print(f"{p}: {v['held']}/{v['runs']} held" + (f"  failed: {v['failed']}" if v["failed"] else ""))
    for k, v in summary["invariant_failures"].items():
        print(f"invariant {k}: {'ok' if not v else 'FAILED ' + ', '.join(v)}")
    print("P7 kernels:", summary["P7_descriptive"]["kernel_sha256_after_by_build"])
    print("mutant verdicts:", {k: v["verdict"] for k, v in summary["mutant_verdicts"].items()})
    for p in problems:
        print("PROBLEM:", p)
    print("packet verification:", "FAIL" if problems else "OK (results as recorded; prediction outcomes printed above)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
