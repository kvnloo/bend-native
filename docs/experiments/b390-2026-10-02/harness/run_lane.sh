#!/usr/bin/env bash
# run_lane.sh <tag> : the measured B390 sequence (after PREREG.json is committed).
# Required environment (host layout stays out of the repository):
#   LAUNCHER     the bend-stack Hermes launcher (run-hermes.sh: hostless + bwrap masks + env -i)
#   WORK_ROOT    lane scratch root (per-phase work dirs, golden caches, outputs)
#   PATCHED_BEND the kvnloo/bend@17db447a build's bin/bend
# Phases: online (O*) -> offline (F*) -> workers (W*) -> patched (E*) -> cli (D*).
set -euo pipefail
tag="$1"
H="$(cd "$(dirname "$0")" && pwd)"
: "${LAUNCHER:?}" "${WORK_ROOT:?}" "${PATCHED_BEND:?}"
S="$WORK_ROOT/$tag/state"
mkdir -p "$S"
run() { local id="$1"; shift; BEND_EXEC=raw BEND_RUN_TIMEOUT=3000 "$LAUNCHER" "b390-$tag-$id" "$@" || echo "phase $id rc=$?"; }
run online  python "$H/matrix.py" online --n 5 --out "$WORK_ROOT/$tag/online/out" --work "$WORK_ROOT/$tag/online/work" --state "$S"
run offline python "$H/matrix.py" offline-outer --phase offline --inner-mode offline-inner --n 5 \
    --out "$WORK_ROOT/$tag/offline/out" --work "$WORK_ROOT/$tag/offline/work" --state "$S"
run workers python "$H/matrix.py" offline-outer --phase workers --inner-mode workers-outer --workers 3 --n 5 \
    --out "$WORK_ROOT/$tag/workers/out" --work "$WORK_ROOT/$tag/workers/work" --state "$S"
run patched python "$H/matrix.py" offline-outer --phase patched --inner-mode patched --executable "$PATCHED_BEND" --n 5 \
    --out "$WORK_ROOT/$tag/patched/out" --work "$WORK_ROOT/$tag/patched/work" --state "$S"
for mode in verify replay; do
  for kind in H N; do
    for i in 1 2 3 4 5; do
      run "cli-$mode-$kind-0$i" bash "$H/cli_run.sh" "$mode" "$kind" "$S" "$H"
    done
  done
done
echo done
