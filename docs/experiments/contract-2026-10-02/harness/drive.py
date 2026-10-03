"""Drive contract-lane Hermes runs through the capture proxy (one run at a time).

usage: hostless python3 drive.py <lane.json> <plan.json> [--only RUN_ID ...]
lane.json (local, never committed) holds machine paths:
  {"runs_dir", "harness_dir", "x11_session", "fixtures": {"cua": <gtk3 main.py>, "bend": <dir>, "repo": <dir>},
   "proxy_port", "upstream_port", "model", "stable", "plugin": {"executable", "dependency_cache", "service_port"},
   "launch_env": {TEMPLATE, WT, VENV, ...}}
plan.json: {"runs": [{"run_id", "task": "cua"|"bend"|"repo", "arm": "off"|"shadow"|"aa"|"disabled", "pair": int,
                      "seed": int, "set": "pilot"|"measured"|"footprint"}]}
Arms differ ONLY in the bend plugin's settings (off/aa identical; shadow adds stack_mode shadow +
stack_opportunities true; disabled = plugin installed but not enabled). Everything else - prompt, toolsets,
model, base_url (this proxy), seed, paths - is identical within a pair.
"""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

H = Path(__file__).resolve().parent
sys.path.insert(0, str(H))
import proxy  # noqa: E402

CANARY = {  # scanned for in plugin-data by analyze.py (requirement 5)
    "prompt": "ctr-canary-prompt-7f3a",
    "tool_result": "ctr-canary-tool-91c2",
    "readme": "ctr-canary-readme-5d0e",
    "bend_source": "ctr-canary-src-b81e",
}

PROMPTS = {
    # verbatim gtk3 prompt of the accepted stack SMOKE/ADDR harness
    "cua": (
        "A GTK test window titled 'CuaTestHarness GTK3 Tasks' is open on this desktop; its app name for "
        "computer_use is 'Main.py'. Task: check the checkbox labeled 'I agree' in that window and change nothing "
        "else. Use the computer_use tool and never pass pid or window_id: 1) action='capture' with mode='ax' and "
        "app='Main.py'; 2) action='click' with element=<index of the 'I agree' check box>; 3) action='capture' with "
        "mode='ax' and app='Main.py' to confirm it is checked; 4) reply with the single word DONE."
    ),
    "bend": ("Use the bend_verify tool with project_dir set to {cwd}, then reply with only the verdict field it "
             "returned. (" + CANARY["prompt"] + ")"),
    "repo": ("In the git repository at {cwd}, use the read_file tool to read data/release.txt and reply with only "
             "the release codename written in it. (" + CANARY["prompt"] + ")"),
}
TOOLSETS = {"cua": "computer_use,bend", "bend": "bend", "repo": "file,bend"}
TOOLSETS_DISABLED = {"cua": "computer_use", "bend": "file", "repo": "file"}
MAX_TURNS = {"cua": 12, "bend": 4, "repo": 6}
EXPECTED = {"bend": "pass", "repo": CANARY["tool_result"]}

ALLOWLIST = [  # verbatim from the accepted SMOKE/ADDR harness: explicit per-action grants; no bypass
    "cua:click:background", "cua:click:foreground", "cua:type:background", "cua:type:foreground",
    "cua:key:background", "cua:key:foreground", "cua:set_value:background",
    "cua:scroll:background", "cua:scroll:foreground",
]


def config_yaml(lane: dict, arm: str) -> str:
    plugin = lane["plugin"]
    if arm == "disabled":
        enabled, disabled, stack = "    []\n", "    - bend\n", ""
    else:
        enabled, disabled = "    - bend\n", "    []\n"
        mode, opp = ("shadow", "true") if arm == "shadow" else ("off", "false")
        stack = f'        stack_mode: "{mode}"\n        stack_opportunities: {opp}\n'
    allow = "".join(f'  - "{k}"\n' for k in ALLOWLIST)
    return f"""# contract lane private Hermes profile (generated per run; arm={arm}); local model through the capture proxy.
model:
  default: "{lane['model']}"
  provider: "custom"
  base_url: "http://127.0.0.1:{lane['proxy_port']}/v1"
  api_key: "ollama-local-no-key"
  context_length: 65536
  ollama_num_ctx: 65536
plugins:
  auto_update_check_hours: 0
  auto_apply: false
  enabled:
{enabled}  disabled:
{disabled}  entries:
    bend:
      settings:
        executable: {plugin['executable']}
        dependency_cache: {plugin['dependency_cache']}
        stack_service_port: {plugin['service_port']}
{stack}updates:
  check: false
  refresh_cua_driver: false
  pre_update_backup: "off"
model_catalog:
  enabled: false
tools:
  tool_search:
    enabled: "off"
computer_use:
  cua_telemetry: false
  permission_mode: "standard"
  autostart: false
  native_wayland: false
  capture_after_mode: "ax"
bot_desktop:
  auto_start: false
command_allowlist:
{allow}logging:
  level: "INFO"
"""


def prepare(lane: dict, run: dict) -> tuple[Path, str]:
    rd = Path(lane["runs_dir"]) / run["run_id"]
    if rd.exists():
        raise SystemExit(f"run dir exists: {rd}")
    (rd / "cwd").mkdir(parents=True)
    task = run["task"]
    if task == "bend":
        shutil.copytree(lane["fixtures"]["bend"], rd / "cwd", dirs_exist_ok=True)
    elif task == "repo":
        shutil.copytree(lane["fixtures"]["repo"], rd / "cwd", dirs_exist_ok=True, symlinks=True)
    (rd / "config.yaml").write_text(config_yaml(lane, run["arm"]))
    prompt = PROMPTS[task].format(cwd=Path(lane["stable"]) / "cwd")
    toolsets = (TOOLSETS_DISABLED if run["arm"] == "disabled" else TOOLSETS)[task]
    (rd / "run.json").write_text(json.dumps({**run, "toolsets": toolsets, "max_turns": MAX_TURNS[task],
                                             "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()},
                                            indent=1, sort_keys=True))
    return rd, prompt


def server_state(port: int) -> dict:
    """Model-server residency at run start (other lanes may share the server; recorded, never controlled)."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        ps = json.loads(opener.open(f"http://127.0.0.1:{port}/api/ps", timeout=5).read().decode())
        return {"loaded": [{"name": m.get("name"), "digest": (m.get("digest") or "")[:12],
                            "context_length": m.get("context_length"), "size_vram": m.get("size_vram")}
                           for m in ps.get("models", [])]}
    except Exception as e:  # recorded, never fatal
        return {"error": f"{type(e).__name__}: {e}"}


def execute(lane: dict, run: dict, capture: proxy.Capture) -> dict:
    rd, prompt = prepare(lane, run)
    task = run["task"]
    toolsets = json.loads((rd / "run.json").read_text())["toolsets"]
    env = dict(os.environ)
    env.update(lane["launch_env"])
    capture.start(rd / "capture", run["seed"])
    t0 = time.time()
    try:
        if task == "cua":
            vals = {**lane["launch_env"], "RUN": str(rd), "FIXTURE_GTK": lane["fixtures"]["cua"],
                    "HARNESS": str(H), "PROMPT": prompt, "MAX_TURNS": str(MAX_TURNS[task]), "TOOLSETS": toolsets}
            (rd / "run.env").write_text("".join(f"{k}={shlex.quote(v)}\n" for k, v in vals.items()))
            env["CUA_SESSION_ATSPI"] = "1"
            env["CUA_SESSION_EXTRA_ENV"] = f"CUA_SESSION_ATSPI=1 CUA_HOSTLESS=1 CONTRACT_ENV={rd / 'run.env'}"
            argv = [lane["x11_session"], "bash", str(H / "run_cua.sh")]
        else:
            argv = ["bash", str(H / "launch.sh"), str(rd), "-m", "hermes_cli.main", "chat", "-Q", "-t", toolsets,
                    "--max-turns", str(MAX_TURNS[task]), "-q", prompt]
        with open(rd / "session.log", "wb") as log:
            p = subprocess.run(argv, cwd=str(rd), env=env, stdout=log, stderr=subprocess.STDOUT,
                               timeout=lane["run_timeout"] + 300)
    finally:
        time.sleep(1.0)  # let a trailing auxiliary request reach the proxy before the next run starts
        capture.stop()
    wall = round(time.time() - t0, 1)
    rc = (rd / "meta" / "exit_code").read_text().strip() if (rd / "meta" / "exit_code").exists() else None
    if task == "cua":
        oracle = json.loads((rd / "oracle.json").read_text()) if (rd / "oracle.json").exists() else {"verdict": "unknown"}
    else:
        out = (rd / "meta" / "stdout").read_text(errors="replace") if (rd / "meta" / "stdout").exists() else ""
        lines = [ln for ln in out.splitlines() if ln.strip() and not ln.startswith("session_id:")]
        reply = lines[-1] if lines else ""
        oracle = {"schema": "contract.reply_oracle.v1", "task": task, "expected": EXPECTED[task],
                  "verdict": "pass" if EXPECTED[task] in reply.lower() or EXPECTED[task] in reply else "fail",
                  "source": "fixture_known_answer"}
        (rd / "oracle.json").write_text(json.dumps(oracle, sort_keys=True))
    return {"run_id": run["run_id"], "session_rc": p.returncode, "hermes_rc": rc,
            "oracle": oracle.get("verdict"), "wall_s_untimed": wall}


def main() -> None:
    lane = json.loads(Path(sys.argv[1]).read_text())
    plan = json.loads(Path(sys.argv[2]).read_text())
    only = set(sys.argv[sys.argv.index("--only") + 1:]) if "--only" in sys.argv else None
    capture = proxy.Capture()
    server = proxy.serve(lane["proxy_port"], lane["upstream_port"], capture)
    ledger = Path(lane["runs_dir"]) / "drive-ledger.jsonl"
    try:
        for run in plan["runs"]:
            if only and run["run_id"] not in only:
                continue
            if (Path(lane["runs_dir"]) / run["run_id"]).exists():
                print("skip existing", run["run_id"], flush=True)
                continue
            started = time.time()
            srv = server_state(lane["upstream_port"])
            load1 = os.getloadavg()[0]
            try:
                res = execute(lane, run, capture)
            except Exception as e:  # harness failure is recorded, never dropped
                res = {"run_id": run["run_id"], "harness_error": f"{type(e).__name__}: {e}"}
            res.update(started=started, server_at_start=srv, loadavg1_at_start=round(load1, 2), set=run["set"])
            with ledger.open("a") as fh:
                fh.write(json.dumps(res, sort_keys=True) + "\n")
            print(json.dumps(res), flush=True)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
