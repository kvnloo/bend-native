"""Drive the bend-native E2E battery: one fresh private Hermes profile (overlay) per run.

usage (under hostless): python3 drive_e2e.py <lane.json> <plan.json> [--only RUN_ID ...]
lane.json is local (absolute paths; never committed). plan.json:
  {"runs": [{"run_id", "task": cua_browser|cua_gtk3|bend_fix|bend_contra|z0_state|z0_opp, "rep": int,
             "set": "measured"|"pilot", "failopen_stop_after_s": float|null}]}
Every run is recorded in <runs_dir>/drive-ledger.jsonl, including harness errors. Analysis happens later
(analyze_e2e.py); this driver only executes runs and the per-run independent oracles.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

H = Path(__file__).resolve().parent

PROMPTS = {  # CUA prompts verbatim from the accepted stack SMOKE/ADDR harness
    "cua_gtk3": (
        "A GTK test window titled 'CuaTestHarness GTK3 Tasks' is open on this desktop; its app name for "
        "computer_use is 'Main.py'. Task: check the checkbox labeled 'I agree' in that window and change nothing "
        "else. Use the computer_use tool and never pass pid or window_id: 1) action='capture' with mode='ax' and "
        "app='Main.py'; 2) action='click' with element=<index of the 'I agree' check box>; 3) action='capture' with "
        "mode='ax' and app='Main.py' to confirm it is checked; 4) reply with the single word DONE."
    ),
    "cua_browser": (
        "A Chrome window is open on this desktop; its app name for computer_use is 'Google-chrome'. It shows a web "
        "form with a text field labeled 'verification value' and a Submit button. Task: enter the exact value "
        "{token} into that field and submit the form. Chrome only accepts input with delivery_mode='foreground'. "
        "Use the computer_use tool and never pass pid or window_id: 1) action='capture' with mode='ax' and "
        "app='Google-chrome'; in its 'elements' list find the element with role 'entry' and label 'verification "
        "value', and the element with role 'button' and label 'Submit'; 2) action='click' with element=<index of "
        "that entry> and delivery_mode='foreground'; 3) action='type' with text='{token}' and "
        "delivery_mode='foreground'; 4) action='click' with element=<index of that Submit button> and "
        "delivery_mode='foreground'; "
        "5) reply with the single word DONE."
    ),
}
BEND_PROMPT = (H / "bend_prompt.txt").read_text().strip()
Z0_QUESTION = "What is the current branch?"


def token_for(rep: int) -> str:
    return f"e2e{rep:02d}-" + hashlib.sha256(f"bend-e2e-2026-10-02/{rep}".encode()).hexdigest()[:6]


def sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def server_state(base_url: str) -> dict:
    root = base_url.rsplit("/v1", 1)[0]
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ps = json.loads(opener.open(root + "/api/ps", timeout=5).read().decode())
        return {"loaded": [{"name": m.get("name"), "digest": (m.get("digest") or "")[:12],
                            "context_length": m.get("context_length"), "size_vram": m.get("size_vram")}
                           for m in ps.get("models", [])]}
    except Exception as e:  # recorded, never fatal
        return {"error": f"{type(e).__name__}: {e}"}


def launcher(lane: dict, run_id: str, args: list[str], extra_env: dict | None = None, timeout: int = 1200) -> int:
    env = dict(os.environ)
    env.update({"BEND_CONFIG": lane["config"], "BEND_MASK_DIRS": lane["pm_installs_mask"]})
    env.update(extra_env or {})
    with open(Path(lane["log_dir"]) / f"{run_id}.launcher.log", "wb") as log:
        p = subprocess.run([lane["launcher"], run_id, *args], env=env, stdout=log, stderr=subprocess.STDOUT,
                           timeout=timeout)
    return p.returncode


def plugin_state(run_dir: Path) -> dict | None:
    for p in sorted((run_dir / "home-upper/.hermes/plugin-data").glob("agent-plugin-bend-*/state.json")):
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            return None
    return None


def run_cua(lane: dict, run: dict) -> dict:
    out = Path(lane["cua_out_dir"]) / run["run_id"]
    out.mkdir(parents=True)
    token = token_for(run["rep"])
    prompt = PROMPTS[run["task"]].format(token=token)
    env_file = {
        "OUT": str(out), "ID": run["run_id"], "TASK": run["task"], "PROMPT": prompt, "TOKEN": token,
        "E2E_HOME": lane["e2e_home"], "CONFIG": lane["config"], "HERMES_TIMEOUT": str(lane["cua_timeout"]),
        "MAX_TURNS": str(lane["cua_max_turns"]), "FIXTURE_DIR": lane["cua_fixture_dir"],
        "PM_INSTALLS_MASK": lane["pm_installs_mask"],
    }
    (out / "run.env").write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in env_file.items()))
    (out / "run.json").write_text(json.dumps({**run, "token": token,
                                              "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()},
                                             indent=1, sort_keys=True))
    env = dict(os.environ)
    env["CUA_SESSION_ATSPI"] = "1"
    env["CUA_SESSION_EXTRA_ENV"] = f"CUA_SESSION_ATSPI=1 CUA_HOSTLESS=1 E2E_ENV={out / 'run.env'}"
    with open(out / "session.log", "wb") as log:
        p = subprocess.run([lane["x11_session"], str(H / "run_one_cua.sh")], cwd=str(out), env=env, stdout=log,
                           stderr=subprocess.STDOUT, timeout=lane["cua_timeout"] + 600)
    oracle = json.loads((out / "oracle.json").read_text()) if (out / "oracle.json").exists() else {"verdict": "unknown"}
    rd = Path(lane["runs_dir"]) / run["run_id"]
    rc = (rd / "meta/exit_code").read_text().strip() if (rd / "meta/exit_code").exists() else None
    return {"session_rc": p.returncode, "hermes_rc": rc, "oracle": oracle.get("verdict"), "token": token}


def run_bend(lane: dict, run: dict) -> dict:
    rid = run["run_id"]
    rd = Path(lane["runs_dir"]) / rid
    fixture = Path(lane["bend_fixtures"][run["task"]])
    laws_sha = sha256(fixture / "LAWS.bend")
    prompt = BEND_PROMPT.replace("{P}", str(rd / "cwd"))
    stopper = None
    if run.get("failopen_stop_after_s") is not None:
        def stop():
            time.sleep(run["failopen_stop_after_s"])
            with open(Path(lane["log_dir"]) / f"{rid}.failopen-stop.log", "wb") as log:
                r = subprocess.run([lane["z0svc"], "stop", lane["z0svc_run_id"]], stdout=log, stderr=subprocess.STDOUT)
            (Path(lane["log_dir"]) / f"{rid}.failopen-stop.json").write_text(json.dumps(
                {"stopped_at": time.time(), "rc": r.returncode}))
        stopper = threading.Thread(target=stop, daemon=True)
    t0 = time.time()
    if stopper:
        stopper.start()
    rc = launcher(lane, rid, ["-m", "hermes_cli.main", "chat", "-Q", "-t", "bend,file", "--max-turns",
                              str(lane["bend_max_turns"]), "-q", prompt],
                  {"BEND_FIXTURE_DIR": str(fixture), "BEND_RUN_TIMEOUT": str(lane["bend_timeout"])})
    if stopper:
        stopper.join(timeout=120)
    t1 = time.time()
    # independent oracle 1: fresh `bend --verdict` outside Hermes on a private copy of the final project
    subprocess.run([lane["oracle_bend"], lane["bend_official"], str(rd / "cwd"), laws_sha, str(rd / "oracle.json")],
                   stdout=open(Path(lane["log_dir"]) / f"{rid}.oracle.log", "wb"), stderr=subprocess.STDOUT,
                   timeout=900)
    oracle = json.loads((rd / "oracle.json").read_text()) if (rd / "oracle.json").exists() else {"verdict": "unknown"}
    # independent oracle 2: replay of the last receipt the plugin saved, in a fresh profile/process
    state = plugin_state(rd)
    last = (state or {}).get("last_receipt")
    replay = {"attempted": False}
    if isinstance(last, dict):
        rec_path = Path(lane["work_dir"]) / f"{rid}.last_receipt.json"
        rec_path.write_text(json.dumps(last, indent=1))
        replay_id = f"{rid}-replay"
        out_path = Path(lane["runs_dir"]) / replay_id / "cwd" / "replay.json"
        rrc = launcher(lane, replay_id, ["-m", "hermes_cli.main", "bend", "replay", str(rec_path), "--project",
                                         str(rd / "cwd"), "--receipt", str(out_path)], {"BEND_RUN_TIMEOUT": "600"})
        stdout = (Path(lane["runs_dir"]) / replay_id / "meta/stdout")
        try:
            res = json.loads(stdout.read_text())
        except (OSError, ValueError):
            res = {}
        replay = {"attempted": True, "rc": rrc, "success": res.get("success"), "verdict": res.get("verdict"),
                  "code": res.get("code"), "error": res.get("error"), "replay_match": res.get("replay_match"),
                  "replay_differences": res.get("replay_differences"), "replay_of": res.get("replay_of"),
                  "last_receipt_id": last.get("receipt_id")}
    reply = (rd / "meta/stdout").read_text().strip() if (rd / "meta/stdout").exists() else ""
    return {"hermes_rc": str(rc), "oracle": oracle.get("verdict"), "laws_unchanged": oracle.get("laws_unchanged"),
            "reply_tail": reply[-200:], "last_receipt_verdict": (last or {}).get("verdict"),
            "replay": replay, "hermes_wall_s_incidental": round(t1 - t0, 1)}


def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout.strip()


def repo_fingerprint(repo: str) -> dict:
    return {"head": git(repo, "rev-parse", "HEAD"), "status": git(repo, "status", "--porcelain", "--ignored"),
            "branch": git(repo, "branch", "--show-current"), "readme_sha256": sha256(Path(repo) / "README.md")}


def run_z0(lane: dict, run: dict) -> dict:
    rid = run["run_id"]
    rd = Path(lane["runs_dir"]) / rid
    repo = lane["pinned_repo"]
    before = repo_fingerprint(repo)
    args = ["-m", "hermes_cli.main", "z0", "state", repo] if run["task"] == "z0_state" else \
        ["-m", "hermes_cli.main", "z0", "opportunity", repo, Z0_QUESTION]
    rc = launcher(lane, rid, args, {"BEND_RUN_TIMEOUT": "120"})
    after = repo_fingerprint(repo)
    checks = {"rc0": rc == 0, "repo_unchanged": before == after}
    try:
        out = json.loads((rd / "meta/stdout").read_text())
    except (OSError, ValueError):
        out = {}
    packet = out.get("packet") or {}
    claims = {c.get("key"): c.get("value") for c in packet.get("current_claims") or [] if isinstance(c, dict)}
    checks["head_matches"] = (packet.get("source_revisions") or {}).get("git", {}).get("head") == before["head"]
    checks["head_claim_matches"] = (claims.get("git.head") or {}).get("sha") == before["head"][:7]
    checks["branch_claim_matches"] = claims.get("git.branch") == (before["branch"] or "(detached)")
    checks["dirty_claim_matches"] = (claims.get("git.dirty") or {}).get("clean") == (before["status"] == "")
    checks["readme_matches"] = ((packet.get("source_revisions") or {}).get("docs") or {}).get("README.md") == \
        "sha256:" + (before["readme_sha256"] or "")[:12]
    if run["task"] == "z0_opp":
        opp = out.get("opportunity") or {}
        checks["authority_read_only"] = (opp.get("authority") or {}).get("grants") == ["read"]
        checks["effects_read_only"] = (opp.get("intent") or {}).get("effects") == ["read"]
        checks["request_echoed"] = (opp.get("intent") or {}).get("request") == Z0_QUESTION
        checks["provenance_packet_matches"] = (opp.get("provenance") or {}).get("packet_id") == packet.get("packet_id")
    verdict = "pass" if all(checks.values()) else "fail"
    (rd / "oracle.json").write_text(json.dumps({"schema": "bend_e2e.z0_oracle.v1", "verdict": verdict,
                                                "checks": checks, "repo_before": before, "repo_after": after},
                                               indent=1, sort_keys=True))
    return {"hermes_rc": str(rc), "oracle": verdict, "packet_id": packet.get("packet_id"),
            "semantic_id": (out.get("opportunity") or {}).get("semantic_id"), "gate": out.get("gate")}


def main() -> None:
    lane = json.loads(Path(sys.argv[1]).read_text())
    plan = json.loads(Path(sys.argv[2]).read_text())
    only = set(sys.argv[sys.argv.index("--only") + 1:]) if "--only" in sys.argv else None
    ledger = Path(lane["runs_dir"]) / "drive-ledger.jsonl"
    for run in plan["runs"]:
        if only and run["run_id"] not in only:
            continue
        if (Path(lane["runs_dir"]) / run["run_id"]).exists():
            print("skip existing", run["run_id"], flush=True)
            continue
        started = time.time()
        srv = server_state(lane["base_url"])
        load1 = os.getloadavg()[0]
        try:
            if run["task"].startswith("cua_"):
                res = run_cua(lane, run)
            elif run["task"].startswith("bend_"):
                res = run_bend(lane, run)
            else:
                res = run_z0(lane, run)
        except Exception as e:  # harness failure is recorded, never dropped
            res = {"harness_error": f"{type(e).__name__}: {e}"}
        res.update(run_id=run["run_id"], task=run["task"], rep=run["rep"], set=run["set"], started=started,
                   ended=time.time(), server_at_start=srv, loadavg1_at_start=round(load1, 2))
        with ledger.open("a") as fh:
            fh.write(json.dumps(res, sort_keys=True) + "\n")
        print(json.dumps(res, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
