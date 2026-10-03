"""Independent GTK3 fixture oracle (gtk3 branch of the accepted stack2-addr oracle, unchanged semantics).

PASS iff the fixture recorded agreed == True and nothing else changed (counter 0, size none, note not saved).
Reads only fixture-owned state captured by the harness after Hermes exited; never asks Hermes, the plugin,
the observer or the Driver.  usage: oracle.py <state.before.json> <state.after.json>
"""
import json
import sys

before_p, after_p = sys.argv[1:3]
try:
    before, after = json.load(open(before_p)), json.load(open(after_p))
except Exception as e:  # unreadable state is UNKNOWN, never a pass or a fail
    print(json.dumps({"schema": "stack_smoke.oracle.v1", "task": "gtk3", "verdict": "unknown", "error": str(e)}))
    sys.exit(0)
collateral = {k: after.get(k) for k in ("counter", "size", "note_saved")
              if after.get(k) != {"counter": 0, "size": "none", "note_saved": None}[k]}
verdict = "pass" if after.get("agreed") is True and not collateral else "fail"
print(json.dumps({"schema": "stack_smoke.oracle.v1", "task": "gtk3", "verdict": verdict,
                  "detail": {"agreed": after.get("agreed"), "collateral": collateral, "seq": after.get("seq"),
                             "before_agreed": before.get("agreed")},
                  "source": "fixture_oracle:gtk3_task_state_v1"}, sort_keys=True))
