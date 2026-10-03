"""Independent fixture oracle. Reads only fixture-owned state captured by the harness after Hermes exited.

gtk3   : PASS iff agreed is True and nothing else changed (counter 0, size none, note not saved).
browser: PASS iff the fixture's /state reports submitted == the run token exactly.
usage: oracle.py <gtk3|browser> <token> <state.before.json> <state.after.json>
"""
import json
import sys

task, token, before_p, after_p = sys.argv[1:5]
try:
    before, after = json.load(open(before_p)), json.load(open(after_p))
except Exception as e:  # unreadable state is UNKNOWN, never a pass or a fail
    print(json.dumps({"schema": "stack_smoke.oracle.v1", "task": task, "verdict": "unknown", "error": str(e)}))
    sys.exit(0)
if task == "gtk3":
    collateral = {k: after.get(k) for k in ("counter", "size", "note_saved")
                  if after.get(k) != {"counter": 0, "size": "none", "note_saved": None}[k]}
    target = after.get("agreed") is True
    verdict = "pass" if target and not collateral else "fail"
    detail = {"agreed": after.get("agreed"), "collateral": collateral, "seq": after.get("seq"),
              "before_agreed": before.get("agreed")}
else:
    submitted = after.get("submitted")
    verdict = "pass" if submitted == token else "fail"
    detail = {"submitted_matches_token": submitted == token, "submitted_present": submitted is not None,
              "before_submitted": before.get("submitted")}
print(json.dumps({"schema": "stack_smoke.oracle.v1", "task": task, "verdict": verdict, "detail": detail,
                  "source": "fixture_oracle:" + ("gtk3_task_state_v1" if task == "gtk3" else "jev_use_form_state")},
                 sort_keys=True))
