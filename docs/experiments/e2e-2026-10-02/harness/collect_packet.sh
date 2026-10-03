#!/usr/bin/env bash
# collect_packet.sh <packet_dir>: copy the measured raw records into the packet, sanitized (no absolute local paths or
# host name), and mirror the UNSANITIZED records to the artifacts mirror. Large/private items stay in the mirror only:
# overlay homes, Chrome profiles, skill copies, PM state.
set -euo pipefail
E=$E2E
MIRROR=$CUA_LANES/artifacts/bend-stack/e2e
P="$1"; R="$P/raw"
mkdir -p "$R/runs" "$R/cua" "$MIRROR"
for rd in "$E"/runs/M-* "$E"/runs/K-* "$E"/runs/S-* "$E"/runs/S2-* "$E"/runs/S3-* "$E"/runs/X-*; do
  [ -d "$rd" ] || continue
  id="$(basename "$rd")"; o="$R/runs/$id"; mkdir -p "$o/meta"
  for f in argv exit_code stdout stderr mask.inside live-home-stat.before live-home-stat.after started_at ended_at \
           worktree-head worktree-status python home-upper.list; do
    [ -f "$rd/meta/$f" ] && cp "$rd/meta/$f" "$o/meta/$f"
  done
  # env.inside: names only plus sanitized values (no secrets are present: env -i allow-list)
  [ -f "$rd/meta/env.inside" ] && cp "$rd/meta/env.inside" "$o/meta/env.inside"
  for f in oracle.json kernel.json; do [ -f "$rd/$f" ] && cp "$rd/$f" "$o/$f"; done
  [ -f "$rd/cwd/replay.json" ] && cp "$rd/cwd/replay.json" "$o/replay.json"
  if [ -d "$rd/cwd" ] && ls "$rd"/cwd/*.bend >/dev/null 2>&1; then mkdir -p "$o/final-project"; cp "$rd"/cwd/*.bend "$o/final-project/"; fi
  z="$rd/home-upper/.hermes/plugin-data/bend/z0"
  [ -f "$z/events.jsonl" ] && cp "$z/events.jsonl" "$o/observer-events.jsonl"
  [ -f "$z/opportunities.jsonl" ] && cp "$z/opportunities.jsonl" "$o/opportunities.jsonl"
  for s in "$rd"/home-upper/.hermes/plugin-data/agent-plugin-bend-*/state.json; do [ -f "$s" ] && cp "$s" "$o/plugin-state.json"; done
  sid="$(grep -o 'session_id: [^ ]*' "$rd/meta/stderr" 2>/dev/null | tail -1 | cut -d' ' -f2 || true)"
  if [ -n "$sid" ] && [ -f "$rd/home-upper/.hermes/logs/agent.log" ]; then
    grep -F "[$sid]" "$rd/home-upper/.hermes/logs/agent.log" > "$o/agent-session.log" || true
  fi
  [ -f "$E/logs/$id.launcher.log" ] && cp "$E/logs/$id.launcher.log" "$o/launcher.log"
  [ -f "$E/logs/$id.oracle.log" ] && cp "$E/logs/$id.oracle.log" "$o/oracle.log"
  [ -f "$E/work/$id.last_receipt.json" ] && cp "$E/work/$id.last_receipt.json" "$o/last_receipt.json"
  for f in "$E/logs/$id".failopen-stop.*; do [ -f "$f" ] && cp "$f" "$o/"; done
done
for od in "$E"/cua-out/M-*; do
  [ -d "$od" ] || continue
  id="$(basename "$od")"; o="$R/cua/$id"; mkdir -p "$o"
  cp "$od/oracle.json" "$od/run.json" "$od/meta/timeline.log" "$od/meta/session.env" "$od/meta/launcher_rc" "$o/" 2>/dev/null || true
  cp "$od/fixture/state.before.json" "$od/fixture/state.after.json" "$o/" 2>/dev/null || true
done
cp "$E/runs/drive-ledger.jsonl" "$R/drive-ledger.jsonl"
find "$R" -type f -print0 | xargs -0 "$E/bin/sanitize.sh"
# unsanitized mirror (local only, never committed)
rsync -a --exclude 'home-work' --exclude 'chrome-profile' --exclude 'home-upper/.hermes/skills' "$E/runs" "$E/cua-out" "$E/logs" "$E/work" "$MIRROR/"
# analysis, scoring, latency ledger, fail-open helpers (appended section)
mkdir -p "$R/scoring/api" "$R/scoring/vn" "$R/failopen" "$R/latency"
cat "$E/work/analysis/runs.jsonl" "$E/work/analysis-fo/runs.jsonl" > "$R/runs.jsonl"
cp "$E/work/analysis/events.jsonl" "$R/events.jsonl"; cp "$E/work/analysis/metrics.json" "$R/metrics.json"
cp "$E/work/score/events.jsonl" "$R/scoring/report-input-events.jsonl"
for l in api vn; do
  cp "$E/work/score/$l/examples.jsonl" "$R/scoring/$l/examples.jsonl"
  for b in laya_421m julia_1 nanojev; do
    cp "$E/work/score/$l/$b.scored.jsonl" "$E/work/score/$l/$b.batches.jsonl" "$E/work/score/$l/$b.evaluate.json" "$R/scoring/$l/"
  done
done
cp "$E/work/score/vn/audit.json" "$R/scoring/vn/audit.json"
mkdir -p "$R/scoring/failed-attempts"; cp "$E/logs/score-attempt1/"*.batches.jsonl "$R/scoring/failed-attempts/" 2>/dev/null || true
cp "$E/logs/score-julia-attempt1/julia_1.batches.jsonl" "$R/scoring/failed-attempts/julia_1.attempt1.batches.jsonl" 2>/dev/null || true
cp "$E/logs/gpu-nanojev.csv" "$R/scoring/gpu-nanojev.csv"
cp "$E"/logs/M-fo_*.fo_stop.json "$E"/logs/M-fo_*.driver_launch "$E/logs/drive-failopen.log" "$R/failopen/" 2>/dev/null || true
cp "$E/logs/M-bend_fix-06.failopen-stop.json" "$E/logs/M-bend_fix-06.failopen-stop.log" "$R/failopen/"
cp "$E"/logs/svc-*.log "$E"/logs/e2e-svc-fo-0*-start.log "$R/failopen/" 2>/dev/null || true
grep 'bend-e2e-kernel' $LANE_TMP/locks/quiet-lane-ledger.jsonl > "$R/latency/quiet-lane-ledger.jsonl"
cp "$E/work/pilots.json" "$R/pilots.json"
find "$R" -type f -print0 | xargs -0 "$E/bin/sanitize.sh"
