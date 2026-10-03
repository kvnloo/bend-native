#!/usr/bin/env bash
# score_all.sh <lane-name> <examples.jsonl> <out_dir> <backend>...
# 'hermes z0 score' through the installed plugin, in batches of 10 examples (the plugin child has a 120 s deadline),
# each batch in a fresh e2e launcher run; the julia worker venv is re-exposed read-only after the real-home mask.
set -uo pipefail
E=$E2E
lane="$1"; ex="$(realpath "$2")"; out="$(realpath "$3")"; shift 3
mkdir -p "$out/batches"
split -l 10 -d -a 3 "$ex" "$out/batches/in-"
for b in "$@"; do
  : > "$out/$b.scored.jsonl"; : > "$out/$b.batches.jsonl"
  for f in "$out"/batches/in-*; do
    n="${f##*-}"; id="${SCORE_PREFIX:-S2}-$lane-$b-$n"
    BEND_RO_BINDS="$REAL_HOME/tmp/julia-venv:$REAL_HOME/tmp/julia-venv $REAL_HOME/tmp/Julia-1:$REAL_HOME/tmp/Julia-1" BEND_RUN_TIMEOUT=400 \
    BEND_CONFIG=$E/config/e2e-config.yaml BEND_MASK_DIRS=$BEND_LANE/template/home/.hermes/installs \
      $E/bin/run-hermes-e2e.sh "$id" -m hermes_cli.main z0 score "$f" --backend "$b" \
      --z0-root $Z0_WT_ROOT/stack-z0 --z0-home $E/z0home --python $E/bin/z0-python.sh > /dev/null
    rc="$(cat $E/runs/$id/meta/exit_code)"
    rows_in="$(wc -l < "$f")"; rows_out=0
    if [ "$rc" = 0 ]; then cat "$E/runs/$id/meta/stdout" >> "$out/$b.scored.jsonl"; rows_out="$(wc -l < "$E/runs/$id/meta/stdout")"; fi
    printf '{"backend":"%s","batch":"%s","run_id":"%s","rc":%s,"rows_in":%s,"rows_out":%s,"started":"%s","ended":"%s"}\n' \
      "$b" "$n" "$id" "$rc" "$rows_in" "$rows_out" "$(cat $E/runs/$id/meta/started_at)" "$(cat $E/runs/$id/meta/ended_at)" >> "$out/$b.batches.jsonl"
  done
done
