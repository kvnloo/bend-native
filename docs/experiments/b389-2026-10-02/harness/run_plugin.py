#!/usr/bin/env python3
"""Packaged-plugin sweep: each case through the real Hermes plugin discovery + `bend_verify` dispatch.

usage: run_plugin.py --arm A --hermes-root <hermes wt> --plugin <bend-native wt> --bend <bin/bend>
                     --root <corpus copy, writable> --cases <list> --out <jsonl> --work <dir>
                     [--limit-seconds N]
Loads the plugin exactly as scripts/smoke.py does (disposable profile, PluginManager,
registry.dispatch('bend_verify')), one process = one profile = one session-pinned kernel.
The plugin requires the proof file to be named PROOF.bend, so each case's bytes are written
as PROOF.bend in the case's own directory (imports keep their relative layout), verified
with project_dir = corpus root, then removed. A directory that already holds a LAWS.bend or
PROOF.bend (other than the case itself) cannot take that layout: recorded as layout_na.
The plugin code is used unchanged.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import b389lib as L  # noqa: E402

KEEP = ("success", "verdict", "execution_verdict", "exit_code", "code", "error", "kernel_strategy",
        "kernel_cache_state", "kernel_source_sha256", "kernel_sha256_before", "kernel_sha256_after",
        "kernel_changed_during_verify", "bend_sha256", "bend_version", "runtime_identity",
        "verification_scope", "source_semantics_attested", "input_manifest_sha256", "input_file_count",
        "proof_sha256", "source_changed_during_verify", "receipt_saved", "timeout_seconds",
        "verdict_timeout_seconds")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", required=True)
    ap.add_argument("--hermes-root", type=Path, required=True)
    ap.add_argument("--plugin", type=Path, required=True)
    ap.add_argument("--bend", type=Path, required=True)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--work", type=Path, required=True)
    ap.add_argument("--limit-seconds", type=float, default=0)
    ap.add_argument("--bootstrap-case", default="b389cases/bootstrap/boot.bend")
    a = ap.parse_args()
    started = time.monotonic()
    sys.path.insert(0, str(a.hermes_root.resolve()))
    from hermes_cli.plugins import PluginManager
    from hermes_constants import set_hermes_home_override
    from tools.registry import registry
    import hermes_yaml as yaml

    profile = a.work / "profile"
    if profile.exists():
        shutil.rmtree(profile)
    shutil.copytree(a.plugin, profile / "plugins/bend",
                    ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
    (a.work / "hub-cache").mkdir(parents=True, exist_ok=True)
    (profile / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["bend"], "entries": {
        "bend": {"settings": {"executable": str(a.bend.resolve()),
                              "dependency_cache": str(a.work / "hub-cache")}}}}}))
    (a.work / "bundled").mkdir(exist_ok=True)
    os.environ["HERMES_HOME"] = str(profile)
    os.environ["HERMES_BUNDLED_PLUGINS"] = str(a.work / "bundled")
    set_hermes_home_override(profile)
    manager = PluginManager()
    manager.discover_and_load()
    if manager._plugins["bend"].error is not None:
        print("plugin load error", manager._plugins["bend"].error, file=sys.stderr)
        return 3
    meta = a.out.with_suffix(".meta.jsonl")
    plugin_files = sorted(p.relative_to(profile / "plugins/bend").as_posix()
                          for p in (profile / "plugins/bend").rglob("*") if p.is_file())

    def verify_case(case: str) -> dict:
        src = a.root / case
        d = src.parent
        rec = {"case": case, "arm": a.arm}
        if src.name != "PROOF.bend" and ((d / "LAWS.bend").exists() or (d / "PROOF.bend").exists()):
            rec.update({"plugin_status": "layout_na"})
            return rec
        created = False
        if src.name != "PROOF.bend":
            (d / "PROOF.bend").write_bytes(src.read_bytes())
            created = True
        rel = (d / "PROOF.bend").relative_to(a.root).as_posix()
        try:
            raw = registry.dispatch("bend_verify", {"project_dir": str(a.root), "proof_file": rel})
        finally:
            if created:
                (d / "PROOF.bend").unlink()
        try:
            r = json.loads(raw)
        except ValueError:
            rec.update({"plugin_status": "unparsed", "raw": str(raw)[:1000]})
            return rec
        rec["plugin_status"] = "ran"
        rec.update({k: r[k] for k in KEEP if k in r})
        rec["stdout"] = str(r.get("stdout", ""))[:1500]
        rec["stderr"] = str(r.get("stderr", ""))[:600]
        return rec

    boot = verify_case(a.bootstrap_case)
    boot["phase"] = "bootstrap"
    with meta.open("a") as fh:
        fh.write(json.dumps({"event": "start", "arm": a.arm, "plugin_files": len(plugin_files),
                             "plugin_tree_sha256": L.hashlib.sha256("\n".join(
                                 f + " " + L.sha256_file(profile / "plugins/bend" / f) for f in plugin_files
                             ).encode()).hexdigest(),
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
        fh.write(json.dumps(boot) + "\n")
    cases = [c for c in a.cases.read_text().split("\n") if c.strip()]
    done = L.jsonl_done(a.out)
    n = 0
    for case in cases:
        if case in done:
            continue
        if a.limit_seconds and time.monotonic() - started > a.limit_seconds:
            break
        try:
            rec = verify_case(case)
        except Exception as exc:
            rec = {"case": case, "arm": a.arm, "plugin_status": "harness_error",
                   "error": f"{type(exc).__name__}: {exc}"}
        with a.out.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        n += 1
    remaining = len([c for c in cases if c not in L.jsonl_done(a.out)])
    manager.unload("bend")
    with meta.open("a") as fh:
        fh.write(json.dumps({"event": "end", "arm": a.arm, "done_this_block": n, "remaining": remaining,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n")
    print(json.dumps({"arm": a.arm, "done_this_block": n, "remaining": remaining}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
