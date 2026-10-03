#!/usr/bin/env bash
# cli_phase.sh <cli-fixtures dir> <special list> <rep tag>
# `hermes bend verify` (installed plugin) on every special case for official then patched Bend, three cases at a
# time, each verifier as one block under the SHARED quiet-lane lock (same yield rule as blocks.sh).
# env: LANE, LOCKDIR
set -uo pipefail
FX="$1"; LIST="$2"; TAG="$3"
HERE="$(cd "$(dirname "$0")" && pwd)"; LEDGER="${LOCKDIR:?}/quiet-lane-ledger.jsonl"
ids=(); while read -r c; do [ -n "$c" ] && ids+=("$(basename "$(dirname "$c")")"); done < "$LIST"
for V in official patched; do
  waited=0
  while pgrep -f '^flock -x 9$' >/dev/null && [ "$waited" -lt 300 ]; do sleep 5; waited=$((waited+5)); done
  exec 9>"$LOCKDIR/quiet-lane.lock"; flock -s 9
  acq="$(date -u +%FT%T.%3NZ)"; load="$(cut -d' ' -f1-3 /proc/loadavg)"
  printf '%s\n' "${ids[@]}" | xargs -P 3 -I{} bash "$HERE/run_cli.sh" "$V" "$FX" "$TAG" {}
  rel="$(date -u +%FT%T.%3NZ)"; exec 9>&-
  printf '{"lane":"B389","label":"b389-cli-%s-%s","mode":"shared","pid":%d,"acquired":"%s","released":"%s","rc":0,"yield_wait_s":%d,"loadavg_at_acquire":"%s"}\n' \
    "$V" "$TAG" "$$" "$acq" "$rel" "$waited" "$load" >> "$LEDGER"
done
echo "cli phase $TAG complete"
