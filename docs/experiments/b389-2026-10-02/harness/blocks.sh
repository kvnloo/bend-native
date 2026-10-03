#!/usr/bin/env bash
# blocks.sh <phase-label> <jobs-file>
# jobs-file: one job per line: <label> <direct|plugin> <arm> <bend> <root> <cases> <out> <work> [extra args...]
# Each iteration runs every unfinished job in parallel as one block of <= 9 min under ONE acquisition of the
# SHARED quiet-lane lock (nothing here is timed, so never the exclusive lock), with one ledger line per block.
# Before acquiring it yields to any waiting exclusive (quiet-timed) holder for up to 5 min, so the shared
# lock never starves a quiet measurement. Jobs resume from their output files.
# env: LANE (lane dir with run-hermes.sh), LOCKDIR (quiet-lane lock dir), HWT (Hermes worktree), PLUGIN (plugin export)
set -uo pipefail
PHASE="$1"; JOBS="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNH="${LANE:?}/run-hermes.sh"; LEDGER="${LOCKDIR:?}/quiet-lane-ledger.jsonl"
declare -A DONE
n=0
while :; do
  n=$((n+1)); pending=0
  left=0
  while read -r label _; do [ -n "${label:-}" ] && [ -z "${DONE[$label]:-}" ] && left=1; done < "$JOBS"
  [ "$left" = 0 ] && break
  waited=0
  while pgrep -f '^flock -x 9$' >/dev/null && [ "$waited" -lt 300 ]; do sleep 5; waited=$((waited+5)); done
  exec 9>"$LOCKDIR/quiet-lane.lock"
  flock -s 9
  acq="$(date -u +%FT%T.%3NZ)"; load="$(cut -d' ' -f1-3 /proc/loadavg)"
  pids=(); rids=()
  while read -r label mode arm bend root cases out work extra; do
    [ -z "${label:-}" ] && continue
    [ -n "${DONE[$label]:-}" ] && continue
    pending=1
    rid=$(printf '%s-blk%02d' "$label" "$n")
    if [ "$mode" = direct ]; then
      args=("$HERE/run_direct.py" --arm "$arm" --bend "$bend" --root "$root" --cases "$cases" --out "$out" --work "$work" --limit-seconds 510)
    else
      args=("$HERE/run_plugin.py" --arm "$arm" --hermes-root "${HWT:?}" --plugin "${PLUGIN:?}" --bend "$bend" --root "$root" --cases "$cases" --out "$out" --work "$work" --limit-seconds 510)
    fi
    # shellcheck disable=SC2086
    BEND_RUN_TIMEOUT=900 "$RUNH" "$rid" "${args[@]}" ${extra:-} >/dev/null &
    pids+=($!); rids+=("$label:$rid")
  done < "$JOBS"
  rcs=""
  for i in "${!pids[@]}"; do wait "${pids[$i]}"; rcs="$rcs ${rids[$i]}=$?"; done
  rel="$(date -u +%FT%T.%3NZ)"
  exec 9>&-
  [ "$pending" = 0 ] && break
  printf '{"lane":"B389","label":"%s-it%02d","mode":"shared","pid":%d,"acquired":"%s","released":"%s","rc":0,"jobs":"%s","yield_wait_s":%d,"loadavg_at_acquire":"%s"}\n' \
    "$PHASE" "$n" "$$" "$acq" "$rel" "${rcs# }" "$waited" "$load" >> "$LEDGER"
  echo "it$n:$rcs"
  for lr in "${rids[@]}"; do
    label="${lr%%:*}"; rid="${lr#*:}"
    last="$(tail -n 1 "$LANE/runs/$rid/meta/stdout" 2>/dev/null)"
    case "$last" in *'"remaining": 0'*) DONE[$label]=1;; esac
  done
  [ "$n" -lt 60 ] || { echo "block limit" >&2; exit 5; }
done
echo "phase $PHASE complete after $((n-1)) blocks"
