#!/usr/bin/env python3
"""B390 dependency-provenance matrix driver (harness glue only; the plugin is used as installed).

Modes
  online          hub fixture reachable on loopback; cells O*
  offline-outer   start the hub on loopback, then run offline-inner under bwrap --unshare-net;
                  the hub request log must stay empty
  offline-inner   cells F* (no route to the hub or anything else)
  workers-outer   spawn N worker processes (separate Hermes plugin loads) under one
                  bwrap --unshare-net and release them together; cells W*
  worker          one concurrent verifier process
  patched         offline cells E* with settings.executable = the patched Bend build

Every plugin call goes through the installed plugin: verification via the bend_verify tool
dispatch, replay via the plugin's receipts.replay (the `hermes bend replay` code path).
Nothing here changes plugin behaviour; mutations are applied to fixture caches only.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import http.server
import importlib
import json
import os
import random
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import fixtures as F  # noqa: E402

PACKAGES, IDS, HUB_NAMES, PROJECTS, CLOSURE = F.build()
KIND_PROJECT = {"H": "H", "N": "N"}
FORGE_PROJECT = {"H": "HF", "N": "NF"}


# ---------------------------------------------------------------- identities

def expected_identity(project: str, name_override: dict | None = None) -> dict:
    """Independent re-derivation of the receipt identities for a closure (not plugin code)."""
    closure = CLOSURE[project]
    keys = list(closure["packages"])
    names = dict(closure["names"])
    if name_override:
        names = dict(name_override)
        keys = []
        for key in names.values():
            keys.append(key)
            text = PACKAGES[key]["Main.bend"].decode()
            for other, pid in IDS.items():
                if pid in text and other not in keys:
                    keys.append(other)
    dep_files = {}
    for key in keys:
        for rel, data in PACKAGES[key].items():
            dep_files[IDS[key] + "/" + rel] = data
    for nv, key in names.items():
        dep_files["names/" + nv] = (IDS[key] + "\n").encode()
    combined = {**{"project/" + k: v for k, v in PROJECTS[project].items()},
                **{"dependencies/" + k: v for k, v in dep_files.items()}}
    return {
        "dependency_packages": {IDS[k]: F.sha(F.manifest(PACKAGES[k])) for k in keys},
        "dependency_names": {nv: IDS[k] for nv, k in names.items()},
        "dependency_manifest_sha256": F.input_manifest(dep_files),
        "input_manifest_sha256": F.input_manifest(combined),
    }


def identity_ok(result: dict, project: str, name_override=None) -> dict:
    exp = expected_identity(project, name_override)
    got = {k: result.get(k) for k in exp}
    return {"match": got == exp, "mismatched": [k for k in exp if got[k] != exp[k]]}


# ---------------------------------------------------------------- private hub

class Hub:
    """Static private BendHub fixture on 127.0.0.1 with a request log."""

    def __init__(self, root: Path, log_path: Path | None = None):
        self.root = root
        self.log: list[str] = []
        self.lock = threading.Lock()
        self.log_path = log_path
        hub = self

        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, fmt, *args):
                with hub.lock:
                    hub.log.append(self.path)
                    if hub.log_path:
                        with hub.log_path.open("a") as out:
                            out.write(json.dumps({"t": time.time(), "path": self.path, "status": args[1] if len(args) > 1 else None}) + "\n")

        self.server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(Handler, directory=str(root)))
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def count(self):
        with self.lock:
            return len(self.log)

    def bind(self, nv: str, key: str):
        (self.root / "name" / nv).write_text(IDS[key] + "\n")

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------- plugin access

class Plugin:
    def __init__(self):
        from hermes_cli.plugins import PluginManager
        from tools.registry import registry
        self.registry = registry
        self.manager = PluginManager()
        self.manager.discover_and_load()
        loaded = self.manager._plugins["bend"]
        assert loaded.enabled and loaded.error is None, loaded.error
        self.module = loaded.module
        self.plugin_dir = Path(loaded.module.__file__).resolve().parent
        self.service = registry.get_entry("bend_verify").handler.__self__
        self.receipts = importlib.import_module(loaded.module.__name__ + ".receipts")
        self.core = importlib.import_module(loaded.module.__name__ + ".verify_core")

    def set_cache(self, cache: Path):
        self.service.ctx.set_config("dependency_cache", str(cache))

    def set_executable(self, bend: str):
        self.service.ctx.set_config("executable", bend)

    def verify(self, project: Path) -> dict:
        return json.loads(self.registry.dispatch("bend_verify", {"project_dir": str(project)}))

    def replay(self, receipt: dict, project: Path) -> dict:
        try:
            return self.receipts.replay(self.service, receipt, str(project))
        except self.core.BendVerifyError as exc:
            return {"success": False, "error": str(exc), "code": exc.code}

    def kernel_path(self):
        sessions = list(self.service._sessions.values())  # read-only peek for the raw-Bend control
        return sessions[0]._kernel if sessions else None

    def identity(self):
        files = {}
        for name in ("__init__.py", "dependencies.py", "receipts.py", "verify_core.py",
                     "session_kernel.py", "service.py", "commands.py", "plugin.yaml"):
            files[name] = F.sha((self.plugin_dir / name).read_bytes())
        head = subprocess.run(["git", "-C", str(self.plugin_dir), "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        return {"plugin_head": head, "plugin_file_sha256": files,
                "executable": self.service.executable(),
                "runtime_identity": self.core.runtime_identity(self.service.executable())}


def outcome(result: dict) -> str:
    if "verdict" in result:
        return result["verdict"]
    return "error:" + str(result.get("code"))


# ---------------------------------------------------------------- recorder

class Recorder:
    def __init__(self, out: Path, phase: str):
        self.out = out
        self.phase = phase
        (out / "receipts").mkdir(parents=True, exist_ok=True)
        self.rows_path = out / "rows.jsonl"

    def row(self, cell, kind, run, step, result, expect, *, identity_project=None,
            name_override=None, extra=None):
        name = f"{cell}-{kind}-{run:02d}-{step}.json"
        (self.out / "receipts" / name).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
        out = outcome(result)
        ident = None
        if result.get("success") and not result.get("raw_bend") and (identity_project or kind in KIND_PROJECT):
            ident = identity_ok(result, identity_project or KIND_PROJECT[kind], name_override)
        row = {"phase": self.phase, "cell": cell, "kind": kind, "run": run, "step": step,
               "outcome": out, "success": bool(result.get("success")), "expect": expect,
               "as_expected": out in expect and (ident is None or ident["match"]),
               "identity": ident, "receipt_file": "receipts/" + name,
               "receipt_id": result.get("receipt_id"),
               "kernel_strategy": result.get("kernel_strategy"),
               "kernel_sha256_after": result.get("kernel_sha256_after"),
               "replay_match": result.get("replay_match"),
               "replay_differences": result.get("replay_differences"),
               "source_recheck_error": result.get("source_recheck_error"),
               "dependency_snapshot_changed": result.get("dependency_snapshot_changed"),
               "ts": time.time(), **(extra or {})}
        with self.rows_path.open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        flag = "ok " if row["as_expected"] else "DEV"
        print(f"[{flag}] {self.phase} {cell} {kind} #{run} {step}: {out} expect={expect}"
              + (f" ident={ident}" if ident else "") + (f" {extra}" if extra else ""), flush=True)
        return row

    def note(self, record: dict):
        with (self.out / "notes.jsonl").open("a") as handle:
            handle.write(json.dumps({"phase": self.phase, "ts": time.time(), **record}) + "\n")


# ---------------------------------------------------------------- fixtures on disk

class Work:
    def __init__(self, root: Path, state: Path):
        self.root = root
        self.state = state
        root.mkdir(parents=True, exist_ok=True)
        self.counter = 0

    def fresh(self, label: str) -> Path:
        self.counter += 1
        path = self.root / f"{self.counter:04d}-{label}"
        path.mkdir(parents=True)
        return path

    def project(self, base: Path, project: str) -> Path:
        path = base / "project"
        F.write_tree(path, PROJECTS[project])
        return path

    def golden_cache(self, name: str) -> Path:
        return self.state / "golden" / name

    def copy_golden(self, base: Path, name: str) -> Path:
        cache = base / "cache"
        shutil.copytree(self.golden_cache(name), cache)
        return cache


def bend_env(core, extra: dict) -> dict:
    env = core.clean_env()
    env.update(extra)
    return env


def bend_fetch(core, bend: str, project: Path, cache: Path, hub_url: str, timeout=120) -> dict:
    """Bend's own resolution/fetch (check-only: no kernel) into a private BEND_LIB."""
    env = bend_env(core, {"BEND_LIB": str(cache), "BEND_HUB": hub_url,
                          "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
    try:
        proc = subprocess.run([bend, "./PROOF.bend", "--check-only"], cwd=project, env=env,
                              capture_output=True, text=True, timeout=timeout)
        return {"rc": proc.returncode, "stdout": proc.stdout[-2000:], "stderr": proc.stderr[-2000:]}
    except subprocess.TimeoutExpired:
        return {"rc": None, "timeout": True}


def raw_verdict(core, bend: str, project: Path, cache: Path, kernel: str) -> dict:
    """Control: plain Bend --verdict reading the cache directly (no plugin snapshot)."""
    env = bend_env(core, {"BEND_LIB": str(cache), "BEND_HUB": "http://127.0.0.1:0", "BENDTT": kernel})
    proc = subprocess.run([bend, "./PROOF.bend", "--verdict"], cwd=project, env=env,
                          capture_output=True, text=True, timeout=120)
    verdict = "pass" if proc.returncode == 0 and proc.stdout.strip() == "ALL PROOFS CHECK" else "fail"
    return {"verdict": verdict, "success": verdict == "pass", "raw_bend": True,
            "rc": proc.returncode, "stdout": proc.stdout[-1500:], "stderr": proc.stderr[-1500:]}


def tree_files(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): F.sha(p.read_bytes())
            for p in sorted(root.rglob("*")) if p.is_file()} if root.exists() else {}


# ---------------------------------------------------------------- mutations

def mutate_benign(cache: Path, kind: str):
    target = cache / IDS["P_ok"] / "Util.bend" if kind == "H" else cache / IDS["Q_ok"] / "Main.bend"
    with target.open("ab") as handle:
        handle.write(b"# edited in the cache after capture\n")
    return target.relative_to(cache).as_posix()


def mutate_forging(cache: Path, kind: str):
    """Turn the failing package into a passing one in place (identity directory unchanged)."""
    if kind == "H":
        target = cache / IDS["P_bad"] / "Util.bend"
        target.write_bytes(PACKAGES["P_ok"]["Util.bend"])
    else:
        target = cache / IDS["Q_bad"] / "Main.bend"
        target.write_bytes(target.read_bytes().replace(b"  1n\n", b"  0n\n"))
    return target.relative_to(cache).as_posix()


def rebind_local(cache: Path, nv: str, key: str, add_package=True):
    if add_package:
        F.write_tree(cache / IDS[key], PACKAGES[key])
    (cache / "names" / nv).write_text(IDS[key] + "\n")


class DuringVerdict:
    """Apply a mutation while the plugin's Bend --verdict child is running."""

    def __init__(self, mutate):
        self.mutate = mutate
        self.info = {"landed": False}

    def _child(self):
        me = os.getpid()
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                stat = Path(f"/proc/{pid}/stat").read_text()
                ppid = int(stat.rsplit(")", 1)[1].split()[1])
                if ppid != me:
                    continue
                cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
                if b"--verdict" in cmd:
                    return int(pid)
            except (OSError, ValueError, IndexError):
                continue
        return None

    def run(self, action):
        tmp = Path(tempfile.gettempdir())
        before = set(tmp.glob("hermes-bend-verify-*"))
        box = {}
        thread = threading.Thread(target=lambda: box.update(result=action()))
        thread.start()
        deadline = time.monotonic() + 150
        while thread.is_alive() and time.monotonic() < deadline:
            pid = self._child()
            if pid is not None:
                snaps = [d for d in tmp.glob("hermes-bend-verify-*") if d not in before]
                self.info["mutated"] = self.mutate(snaps[0] if snaps else None)
                alive = Path(f"/proc/{pid}").exists()
                try:
                    alive = alive and b"--verdict" in Path(f"/proc/{pid}/cmdline").read_bytes()
                except OSError:
                    alive = False
                self.info["landed"] = alive
                self.info["child_pid_seen"] = True
                break
            time.sleep(0.0005)
        thread.join()
        return box["result"]


# ---------------------------------------------------------------- cells

def setup_common(args, phase):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rec = Recorder(out, phase)
    work = Work(Path(args.work), Path(args.state))
    plugin = Plugin()
    if getattr(args, "executable", None):
        plugin.set_executable(args.executable)
    bend = plugin.service.executable()
    rec.note({"kind": "identity", **plugin.identity(), "argv": sys.argv[1:]})
    # Kernel bootstrap on a dependency-free project (not a matrix cell).
    base = work.fresh("bootstrap")
    project = base / "project"
    F.write_tree(project, {"LAWS.bend": b"import Base\n\nlaw identity:\n  {0n == 0n : Nat}\n",
                           "PROOF.bend": PROJECTS["H"]["PROOF.bend"]})
    plugin.set_cache(base / "empty-cache")
    result = plugin.verify(project)
    rec.row("X0_bootstrap", "-", 0, "verify", result, ["pass"])
    kernel = plugin.kernel_path()
    rec.note({"kind": "kernel", "path": kernel, "sha256": F.sha(Path(kernel).read_bytes()) if kernel else None})
    return rec, work, plugin, bend, kernel


def run_online(args):
    rec, work, plugin, bend, kernel = setup_common(args, "online")
    core = plugin.core
    hub_root = work.root / "hub"
    F.write_hub(hub_root, PACKAGES, IDS, HUB_NAMES)
    hub = Hub(hub_root, Path(args.out) / "hub-requests.jsonl")
    rec.note({"kind": "hub", "url_port": hub.port, "tree": tree_files(hub_root)})
    n = args.n
    state = work.state
    (state / "golden").mkdir(parents=True, exist_ok=True)

    def verify_counted(project):
        before = hub.count()
        result = plugin.verify(project)
        return result, hub.count() - before

    for kind in ("H", "N"):
        pj = KIND_PROJECT[kind]
        exp_missing = ["error:dependency_missing"] if kind == "H" else ["error:input_capture_failed", "error:dependency_missing"]

        # O1 cold cache, no fetch: the plugin never downloads.
        for i in range(n):
            base = work.fresh(f"O1-{kind}")
            project = work.project(base, pj)
            cache = base / "cache"
            cache.mkdir()
            plugin.set_cache(cache)
            result, dh = verify_counted(project)
            rec.row("O1_cold_missing", kind, i, "verify", result, exp_missing, extra={"hub_requests": dh})

        # O2 cold cache -> Bend's own fetch from the private hub -> plugin verify.
        for i in range(n):
            base = work.fresh(f"O2-{kind}")
            project = work.project(base, pj)
            cache = base / "cache"
            before = hub.count()
            fetch = bend_fetch(core, bend, project, cache, hub.url)
            fetched = hub.count() - before
            synthetic = base / "synthetic"
            F.write_cache(synthetic, PACKAGES, IDS, CLOSURE[pj]["packages"],
                          {nv: k for nv, k in CLOSURE[pj]["names"].items()})
            same_bytes = tree_files(cache) == tree_files(synthetic)
            plugin.set_cache(cache)
            result, dh = verify_counted(project)
            rec.row("O2_cold_fetch_verify", kind, i, "verify", result, ["pass"],
                    extra={"hub_requests": dh, "fetch_rc": fetch.get("rc"), "fetch_hub_requests": fetched,
                           "fetched_equals_fixture_layout": same_bytes})
            if i == 0:
                shutil.copytree(cache, work.golden_cache(kind))
                (state / f"golden-receipt-{kind}.json").write_text(json.dumps(result, indent=2) + "\n")

        golden = json.loads((state / f"golden-receipt-{kind}.json").read_text())

        # O3 warm cache present.
        manifests = set()
        for i in range(n):
            base = work.fresh(f"O3-{kind}")
            project = work.project(base, pj)
            plugin.set_cache(work.copy_golden(base, kind))
            result, dh = verify_counted(project)
            manifests.add(result.get("input_manifest_sha256"))
            rec.row("O3_warm_present", kind, i, "verify", result, ["pass"], extra={"hub_requests": dh})
        rec.note({"kind": "O3_manifests", "case": kind, "distinct_input_manifests": sorted(m for m in manifests if m)})

        # O4 cache mutated after fetch (benign edit): plugin vs raw Bend control.
        for i in range(n):
            base = work.fresh(f"O4-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            where = mutate_benign(cache, kind)
            plugin.set_cache(cache)
            result, dh = verify_counted(project)
            rec.row("O4_mutated_benign", kind, i, "verify", result, ["error:dependency_integrity"],
                    extra={"hub_requests": dh, "mutated": where})
            rec.row("O4_mutated_benign", kind, i, "raw_bend_control",
                    raw_verdict(core, bend, project, cache, kernel), ["pass"])

        # O5 forging mutation: failing package edited in place to pass.
        fp = FORGE_PROJECT[kind]
        for i in range(n):
            base = work.fresh(f"O5-{kind}")
            project = work.project(base, fp)
            cache = base / "cache"
            fetch = bend_fetch(core, bend, project, cache, hub.url)
            if i == 0:
                shutil.copytree(cache, work.golden_cache("forge-" + kind))
            plugin.set_cache(cache)
            control, _ = verify_counted(project)
            rec.row("O5_mutated_forging", kind, i, "verify_before_mutation", control, ["fail"],
                    identity_project=fp, extra={"fetch_rc": fetch.get("rc")})
            where = mutate_forging(cache, kind)
            rec.row("O5_mutated_forging", kind, i, "raw_bend_control",
                    raw_verdict(core, bend, project, cache, kernel), ["pass"], identity_project=fp)
            result, dh = verify_counted(project)
            rec.row("O5_mutated_forging", kind, i, "verify", result, ["error:dependency_integrity"],
                    identity_project=fp, extra={"hub_requests": dh, "mutated": where})

        # O6 extra file inside a captured package; O7 partial package.
        for i in range(n):
            base = work.fresh(f"O6-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            (cache / IDS["P_ok"] / "NOTES.txt").write_text("not part of the publication manifest\n")
            plugin.set_cache(cache)
            result, dh = verify_counted(project)
            rec.row("O6_extra_file", kind, i, "verify", result, ["error:dependency_integrity"], extra={"hub_requests": dh})
        for i in range(n):
            base = work.fresh(f"O7-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            (cache / IDS["P_ok"] / "Util.bend").unlink()
            plugin.set_cache(cache)
            result, dh = verify_counted(project)
            rec.row("O7_partial_package", kind, i, "verify", result, ["error:dependency_integrity"], extra={"hub_requests": dh})

        if kind == "N":
            nv = "sample@1.0.0.0"
            for i in range(n):
                base = work.fresh("O8a")
                project = work.project(base, pj)
                cache = work.copy_golden(base, kind)
                (cache / "names" / nv).unlink()
                plugin.set_cache(cache)
                result, dh = verify_counted(project)
                rec.row("O8a_binding_missing", kind, i, "verify", result,
                        ["error:input_capture_failed", "error:dependency_missing"], extra={"hub_requests": dh})
            for i in range(n):
                base = work.fresh("O8b")
                project = work.project(base, pj)
                cache = work.copy_golden(base, kind)
                (cache / "names" / nv).write_text("not-a-package\n")
                plugin.set_cache(cache)
                result, dh = verify_counted(project)
                rec.row("O8b_binding_garbage", kind, i, "verify", result, ["error:dependency_integrity"], extra={"hub_requests": dh})
            for i in range(n):
                base = work.fresh("O8c")
                project = work.project(base, pj)
                cache = work.copy_golden(base, kind)
                (cache / "names" / nv).write_text("0x" + "%032x" % random.getrandbits(128) + "\n")
                plugin.set_cache(cache)
                result, dh = verify_counted(project)
                rec.row("O8c_binding_absent_package", kind, i, "verify", result, ["error:dependency_missing"], extra={"hub_requests": dh})
            for i in range(n):
                base = work.fresh("O8d")
                project = work.project(base, pj)
                cache = work.copy_golden(base, kind)
                rebind_local(cache, nv, "Q_alt")
                plugin.set_cache(cache)
                result, dh = verify_counted(project)
                rec.row("O8d_binding_rebound_fresh_verify", kind, i, "verify", result, ["pass"],
                        name_override={nv: "Q_alt"}, extra={"hub_requests": dh})

        # O9 replay with the same cache.
        for i in range(n):
            base = work.fresh(f"O9-{kind}")
            project = work.project(base, pj)
            plugin.set_cache(work.copy_golden(base, kind))
            before = hub.count()
            result = plugin.replay(golden, project)
            rec.row("O9_replay_same_cache", kind, i, "replay", result, ["pass"], extra={"hub_requests": hub.count() - before})

        # O10 replay with the cache missing; O11 replay with the cache mutated.
        for i in range(n):
            base = work.fresh(f"O10-{kind}")
            project = work.project(base, pj)
            (base / "cache").mkdir()
            plugin.set_cache(base / "cache")
            before = hub.count()
            result = plugin.replay(golden, project)
            rec.row("O10_replay_cache_missing", kind, i, "replay", result, exp_missing, extra={"hub_requests": hub.count() - before})
        for i in range(n):
            base = work.fresh(f"O11-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            mutate_benign(cache, kind)
            plugin.set_cache(cache)
            result = plugin.replay(golden, project)
            rec.row("O11_replay_cache_mutated", kind, i, "replay", result, ["error:dependency_integrity"])

        # O12 cache lost, refetched with Bend from the hub (binding unchanged), replay.
        for i in range(n):
            base = work.fresh(f"O12-{kind}")
            project = work.project(base, pj)
            cache = base / "cache"
            fetch = bend_fetch(core, bend, project, cache, hub.url)
            plugin.set_cache(cache)
            before = hub.count()
            result = plugin.replay(golden, project)
            rec.row("O12_replay_after_refetch", kind, i, "replay", result, ["pass"],
                    extra={"fetch_rc": fetch.get("rc"), "hub_requests": hub.count() - before})

        if kind == "N":
            nv = "sample@1.0.0.0"
            # O13a hub re-points the same name@version; cache refetched; replay must not follow.
            for i in range(n):
                base = work.fresh("O13a")
                project = work.project(base, pj)
                cache = base / "cache"
                hub.bind(nv, "Q_alt")
                try:
                    fetch = bend_fetch(core, bend, project, cache, hub.url)
                finally:
                    hub.bind(nv, "Q_ok")
                plugin.set_cache(cache)
                bound = (cache / "names" / nv).read_text().strip() if (cache / "names" / nv).exists() else None
                result = plugin.replay(golden, project)
                rec.row("O13a_replay_hub_rebound_refetch", kind, i, "replay", result, ["error:receipt_stale"],
                        extra={"fetch_rc": fetch.get("rc"), "cache_binding": bound,
                               "cache_binding_is_Q_alt": bound == IDS["Q_alt"]})
                fresh = plugin.verify(project)
                rec.row("O13a_replay_hub_rebound_refetch", kind, i, "fresh_verify_control", fresh, ["pass"],
                        name_override={nv: "Q_alt"})
            # O13b local binding re-pointed; replay.
            for i in range(n):
                base = work.fresh("O13b")
                project = work.project(base, pj)
                cache = work.copy_golden(base, kind)
                rebind_local(cache, nv, "Q_alt")
                plugin.set_cache(cache)
                result = plugin.replay(golden, project)
                rec.row("O13b_replay_local_rebound", kind, i, "replay", result, ["error:receipt_stale"])
            # O13c hub re-pointed but the captured local binding is warm: replay uses the cache only.
            for i in range(n):
                base = work.fresh("O13c")
                project = work.project(base, pj)
                plugin.set_cache(work.copy_golden(base, kind))
                hub.bind(nv, "Q_alt")
                try:
                    before = hub.count()
                    result = plugin.replay(golden, project)
                    dh = hub.count() - before
                finally:
                    hub.bind(nv, "Q_ok")
                rec.row("O13c_replay_hub_rebound_cache_warm", kind, i, "replay", result, ["pass"], extra={"hub_requests": dh})

        # O14 change injected between replay's identity precheck and its verification.
        for i in range(n):
            base = work.fresh(f"O14-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            plugin.set_cache(cache)
            original = plugin.service.verify

            def injected(*a, _cache=cache, _orig=original, **k):
                if kind == "N":
                    rebind_local(_cache, "sample@1.0.0.0", "Q_alt")
                else:
                    mutate_benign(_cache, kind)
                return _orig(*a, **k)

            plugin.service.verify = injected
            try:
                result = plugin.replay(golden, project)
            finally:
                del plugin.service.verify
            expect = ["replay_mismatch"] if kind == "N" else ["error:dependency_integrity"]
            rec.row("O14_replay_race_injected", kind, i, "replay", result, expect)

        # O15 dependency cache mutated while the verdict child runs.
        for i in range(n):
            base = work.fresh(f"O15-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            plugin.set_cache(cache)
            if kind == "N":
                mut = lambda snap, _c=cache: (rebind_local(_c, "sample@1.0.0.0", "Q_alt"), "names rebound to Q_alt")[1]
            else:
                mut = lambda snap, _c=cache: mutate_benign(_c, "H")
            dv = DuringVerdict(mut)
            result = dv.run(lambda: plugin.verify(project))
            rec.row("O15_mutate_cache_during_verdict", kind, i, "verify", result, ["unstable"], extra=dv.info)

        # O16 the private snapshot BEND_LIB mutated while the verdict child runs.
        for i in range(n):
            base = work.fresh(f"O16-{kind}")
            project = work.project(base, pj)
            plugin.set_cache(work.copy_golden(base, kind))

            def mut(snap):
                if snap is None:
                    return "no snapshot directory found"
                target = snap / "bend-lib" / IDS["P_ok"] / "Util.bend"
                with target.open("ab") as handle:
                    handle.write(b"# snapshot edited during verdict\n")
                return "snapshot " + target.relative_to(snap).as_posix()

            dv = DuringVerdict(mut)
            result = dv.run(lambda: plugin.verify(project))
            rec.row("O16_mutate_snapshot_during_verdict", kind, i, "verify", result, ["unstable"], extra=dv.info)

        # O17 concurrent Bend resolution into one cold cache, then verify.
        for i in range(n):
            base = work.fresh(f"O17-{kind}")
            projects = [work.project(base / f"p{j}", pj) for j in range(4)]
            cache = base / "cache"
            results = [None] * 4
            barrier = threading.Barrier(4)

            def fetcher(j):
                barrier.wait()
                results[j] = bend_fetch(core, bend, projects[j], cache, hub.url)

            threads = [threading.Thread(target=fetcher, args=(j,)) for j in range(4)]
            [t.start() for t in threads]
            [t.join() for t in threads]
            plugin.set_cache(cache)
            result = plugin.verify(projects[0])
            rec.row("O17_concurrent_bend_fetch", kind, i, "verify", result, ["pass"],
                    extra={"fetch_rcs": [r.get("rc") for r in results],
                           "cache_equals_fixture": tree_files(cache) == tree_files(work.golden_cache(kind))})

        # O18 plugin verification racing Bend resolution into the same cold cache.
        for i in range(n):
            base = work.fresh(f"O18-{kind}")
            projects = [work.project(base / f"p{j}", pj) for j in range(4)]
            cache = base / "cache"
            plugin.set_cache(cache)
            done = threading.Event()
            fetch_rcs = [None] * 4

            def fetcher(j):
                fetch_rcs[j] = bend_fetch(core, bend, projects[j], cache, hub.url).get("rc")

            threads = [threading.Thread(target=fetcher, args=(j,)) for j in range(4)]
            [t.start() for t in threads]
            # Back-to-back attempts while the fetchers run. Fail-closed errors return in
            # milliseconds, so they are counted (all in the denominator); every attempt that
            # reached a verdict is recorded as its own row with its identity checked.
            attempt = 0
            counts: dict[str, int] = {}
            while any(t.is_alive() for t in threads) and attempt < 20000:
                result = plugin.verify(projects[0])
                attempt += 1
                counts[outcome(result)] = counts.get(outcome(result), 0) + 1
                if "verdict" in result:
                    rec.row("O18_verify_racing_fetch", kind, i, f"attempt{attempt:05d}", result,
                            ["pass", "unstable", "fail"])
            [t.join() for t in threads]
            result = plugin.verify(projects[0])
            rec.row("O18_verify_racing_fetch", kind, i, "final", result, ["pass"],
                    extra={"racing_attempts": attempt, "racing_outcomes": counts, "fetch_rcs": fetch_rcs})

    rec.note({"kind": "hub_total_requests", "count": hub.count()})
    hub.stop()


def run_offline_inner(args):
    rec, work, plugin, bend, kernel = setup_common(args, args.phase)
    core = plugin.core
    port = int(Path(args.hub_port_file).read_text()) if args.hub_port_file else None
    probe = {"proc_net_dev": Path("/proc/net/dev").read_text().split("\n")[2:]}
    if port:
        sock = socket.socket()
        sock.settimeout(3)
        try:
            sock.connect(("127.0.0.1", port))
            probe["hub_connect"] = "connected"
        except OSError as exc:
            probe["hub_connect"] = type(exc).__name__ + ": " + str(exc)
        finally:
            sock.close()
    rec.note({"kind": "netprobe", **probe})
    n = args.n
    hub_url = f"http://127.0.0.1:{port}" if port else "http://127.0.0.1:9"
    tag = "E" if args.phase == "patched" else "F"
    for kind in ("H", "N"):
        pj = KIND_PROJECT[kind]
        exp_missing = ["error:dependency_missing"] if kind == "H" else ["error:input_capture_failed", "error:dependency_missing"]
        golden = json.loads((work.state / f"golden-receipt-{kind}.json").read_text())
        if args.phase == "patched":
            for i in range(n):
                base = work.fresh(f"E1-{kind}")
                project = work.project(base, pj)
                plugin.set_cache(work.copy_golden(base, kind))
                result = plugin.verify(project)
                rec.row("E1_patched_warm_present_offline", kind, i, "verify", result, ["pass"],
                        extra={"input_manifest_equals_official_golden":
                               result.get("input_manifest_sha256") == golden["input_manifest_sha256"]})
            for i in range(n):
                base = work.fresh(f"E2-{kind}")
                project = work.project(base, pj)
                plugin.set_cache(work.copy_golden(base, kind))
                result = plugin.replay(golden, project)
                rec.row("E2_patched_replay_official_receipt", kind, i, "replay", result, ["error:receipt_stale"])
            continue
        for i in range(n):
            base = work.fresh(f"F1-{kind}")
            project = work.project(base, pj)
            (base / "cache").mkdir()
            plugin.set_cache(base / "cache")
            rec.row("F1_cold_missing_offline", kind, i, "verify", plugin.verify(project), exp_missing)
        for i in range(n):
            base = work.fresh(f"F2-{kind}")
            project = work.project(base, pj)
            cache = base / "cache"
            fetch = bend_fetch(core, bend, project, cache, hub_url, timeout=60)
            plugin.set_cache(cache)
            rec.row("F2_cold_fetch_offline", kind, i, "verify", plugin.verify(project), exp_missing,
                    extra={"fetch_rc": fetch.get("rc"), "fetch_stderr_tail": (fetch.get("stderr") or "")[-300:],
                           "cache_files_after_fetch": len(tree_files(cache))})
        for i in range(n):
            base = work.fresh(f"F3-{kind}")
            project = work.project(base, pj)
            plugin.set_cache(work.copy_golden(base, kind))
            result = plugin.verify(project)
            rec.row("F3_warm_present_offline", kind, i, "verify", result, ["pass"],
                    extra={"input_manifest_equals_online_golden":
                           result.get("input_manifest_sha256") == golden["input_manifest_sha256"]})
        for i in range(n):
            base = work.fresh(f"F4-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            where = mutate_benign(cache, kind)
            plugin.set_cache(cache)
            rec.row("F4_mutated_benign_offline", kind, i, "verify", plugin.verify(project),
                    ["error:dependency_integrity"], extra={"mutated": where})
            rec.row("F4_mutated_benign_offline", kind, i, "raw_bend_control",
                    raw_verdict(core, bend, project, cache, kernel), ["pass"])
        fp = FORGE_PROJECT[kind]
        for i in range(n):
            base = work.fresh(f"F5-{kind}")
            project = work.project(base, fp)
            cache = work.copy_golden(base, "forge-" + kind)
            plugin.set_cache(cache)
            rec.row("F5_mutated_forging_offline", kind, i, "verify_before_mutation", plugin.verify(project),
                    ["fail"], identity_project=fp)
            mutate_forging(cache, kind)
            rec.row("F5_mutated_forging_offline", kind, i, "raw_bend_control",
                    raw_verdict(core, bend, project, cache, kernel), ["pass"], identity_project=fp)
            rec.row("F5_mutated_forging_offline", kind, i, "verify", plugin.verify(project),
                    ["error:dependency_integrity"], identity_project=fp)
        for i in range(n):
            base = work.fresh(f"F6-{kind}")
            project = work.project(base, pj)
            plugin.set_cache(work.copy_golden(base, kind))
            rec.row("F6_replay_offline", kind, i, "replay", plugin.replay(golden, project), ["pass"])
        for i in range(n):
            base = work.fresh(f"F7-{kind}")
            project = work.project(base, pj)
            (base / "cache").mkdir()
            plugin.set_cache(base / "cache")
            rec.row("F7_replay_offline_cache_missing", kind, i, "replay", plugin.replay(golden, project), exp_missing)
        if kind == "N":
            for i in range(n):
                base = work.fresh("F8")
                project = work.project(base, pj)
                cache = work.copy_golden(base, kind)
                rebind_local(cache, "sample@1.0.0.0", "Q_alt")
                plugin.set_cache(cache)
                rec.row("F8_replay_offline_local_rebound", kind, i, "replay", plugin.replay(golden, project),
                        ["error:receipt_stale"])
        for i in range(n):
            base = work.fresh(f"F9-{kind}")
            project = work.project(base, pj)
            cache = work.copy_golden(base, kind)
            plugin.set_cache(cache)
            if kind == "N":
                mut = lambda snap, _c=cache: (rebind_local(_c, "sample@1.0.0.0", "Q_alt"), "names rebound to Q_alt")[1]
            else:
                mut = lambda snap, _c=cache: mutate_benign(_c, "H")
            dv = DuringVerdict(mut)
            result = dv.run(lambda: plugin.verify(project))
            rec.row("F9_mutate_cache_during_verdict_offline", kind, i, "verify", result, ["unstable"], extra=dv.info)


def run_worker(args):
    out = Path(args.out)
    rec = Recorder(out, f"worker{args.worker_id}")
    work = Work(Path(args.work), Path(args.state))
    plugin = Plugin()
    base = work.fresh("bootstrap")
    project = base / "project"
    F.write_tree(project, {"LAWS.bend": b"import Base\n\nlaw identity:\n  {0n == 0n : Nat}\n",
                           "PROOF.bend": PROJECTS["H"]["PROOF.bend"]})
    rec.row("X0_bootstrap", "-", 0, "verify", plugin.verify(project), ["pass"])
    projects = {k: work.project(work.fresh(f"proj-{k}"), KIND_PROJECT[k]) for k in ("H", "N")}
    Path(args.ready_dir, f"ready-{args.worker_id}").write_text("1")
    while not Path(args.ready_dir, "go").exists():
        time.sleep(0.01)
    for i in range(args.n):
        for kind in ("H", "N"):
            started = time.time()  # overlap evidence only; not a latency result
            result = plugin.verify(projects[kind])
            rec.row("W1_concurrent_processes_shared_cache", kind, i, f"worker{args.worker_id}", result, ["pass"],
                    extra={"worker": args.worker_id, "receipt_saved": result.get("receipt_saved"),
                           "t_start": started})


def run_workers_outer(args):
    work_root = Path(args.work)
    state = Path(args.state)
    shared = work_root / "shared-cache"
    shutil.copytree(state / "golden" / "N", shared)   # union closure: P_ok, Q_ok, names
    before = tree_files(shared)
    cfg = subprocess.run([sys.executable, "-m", "hermes_cli.main", "config", "set",
                          "plugins.entries.bend.settings.dependency_cache", str(shared)],
                         capture_output=True, text=True)
    ready = work_root / "ready"
    ready.mkdir(parents=True)
    procs = []
    for k in range(args.workers):
        cmd = [sys.executable, str(Path(__file__).resolve()), "worker", "--worker-id", str(k),
               "--out", str(Path(args.out) / f"worker{k}"), "--work", str(work_root / f"w{k}"),
               "--state", str(state), "--n", str(args.n), "--ready-dir", str(ready)]
        procs.append(subprocess.Popen(cmd))
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline and len(list(ready.glob("ready-*"))) < args.workers:
        if any(p.poll() is not None for p in procs):
            break
        time.sleep(0.1)
    (ready / "go").write_text("1")
    codes = [p.wait() for p in procs]
    after = tree_files(shared)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "workers-outer.json").write_text(json.dumps({
        "config_set_rc": cfg.returncode, "worker_exit_codes": codes,
        "shared_cache_unchanged": before == after, "workers": args.workers, "n": args.n}, indent=2) + "\n")


def run_offline_outer(args):
    """Hub reachable on the outer loopback; the inner matrix runs in a fresh network namespace."""
    work = Path(args.work)
    hub_root = work / "hub"
    F.write_hub(hub_root, PACKAGES, IDS, HUB_NAMES)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    hub = Hub(hub_root, out / "hub-requests.jsonl")
    port_file = work / "hub-port"
    port_file.write_text(str(hub.port))
    # Outer sanity: the hub answers on the outer loopback (one request, logged before the inner run).
    import urllib.request
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    outer_probe = opener.open(hub.url + "/name/sample@1.0.0.0", timeout=5).read().decode().strip()
    before = hub.count()
    inner = ["bwrap", "--dev-bind", "/", "/", "--unshare-net", "--die-with-parent", "--",
             sys.executable, str(Path(__file__).resolve()), args.inner_mode, "--phase", args.phase,
             "--out", str(out), "--work", str(work / "inner"), "--state", args.state,
             "--n", str(args.n), "--hub-port-file", str(port_file)]
    if args.executable:
        inner += ["--executable", args.executable]
    if args.inner_mode == "workers-outer":
        inner += ["--workers", str(args.workers)]
    code = subprocess.call(inner)
    during = hub.count() - before
    hub.stop()
    (out / "offline-outer.json").write_text(json.dumps({
        "inner_exit": code, "outer_probe_binding": outer_probe,
        "hub_requests_during_inner": during, "hub_requests_total": hub.count()}, indent=2) + "\n")
    sys.exit(code)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["online", "offline-outer", "offline-inner", "workers-outer", "worker", "patched"])
    parser.add_argument("--out", required=True)
    parser.add_argument("--work", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--phase", default="offline")
    parser.add_argument("--inner-mode", default="offline-inner")
    parser.add_argument("--hub-port-file")
    parser.add_argument("--executable")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--worker-id", type=int, default=0)
    parser.add_argument("--ready-dir")
    args = parser.parse_args()
    try:
        if args.mode == "online":
            run_online(args)
        elif args.mode == "offline-outer":
            run_offline_outer(args)
        elif args.mode in ("offline-inner", "patched"):
            run_offline_inner(args)
        elif args.mode == "workers-outer":
            run_workers_outer(args)
        else:
            run_worker(args)
    except Exception:
        traceback.print_exc()
        sys.exit(3)


if __name__ == "__main__":
    main()
