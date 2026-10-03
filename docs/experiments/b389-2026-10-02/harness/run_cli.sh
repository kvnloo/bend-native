#!/usr/bin/env bash
# run_cli.sh <official|patched> <cli-fixtures dir> <rep tag> <id>...   (env: LANE = the lane dir holding run-hermes.sh and runs/)
# `hermes bend verify` through the plugin INSTALLED in the isolated template profile (pinned e85e65e5),
# one Hermes process per case (cold private kernel each time), via the lane launcher.
set -uo pipefail
V="$1"; FX="$2"; TAG="$3"; shift 3
L="${LANE:?set LANE}"; RUNH="$L/run-hermes.sh"
for id in "$@"; do
  rid="b389-cli-$V-$id-$TAG"
  BEND_VERIFIER="$V" BEND_FIXTURE_DIR="$FX/$id" BEND_RUN_TIMEOUT=600 \
    "$RUNH" "$rid" -m hermes_cli.main bend verify "$L/runs/$rid/cwd" --receipt "$L/runs/$rid/receipt.json" >/dev/null
  echo "$rid rc=$(cat "$L/runs/$rid/meta/exit_code")"
done
