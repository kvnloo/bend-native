#!/usr/bin/env bash
# cli_run.sh <verify|replay> <H|N> <state-dir> <harness-dir>
# One end-to-end `hermes bend` CLI invocation through the installed plugin, run from the
# launcher's private run dir (cwd). The dependency cache is a copy of the Bend-fetched golden
# cache; the CLI runs inside a fresh network namespace (bwrap --unshare-net), so the verifier
# has no route to any hub. Each invocation is a new Hermes process (session-bootstrap kernel).
set -uo pipefail
mode="$1"; kind="$2"; state="$3"; harness="$4"
cwd="$PWD"
python "$harness/fixtures.py" project "$kind" "$cwd/project"
cp -a "$state/golden/$kind" "$cwd/cache"
python -m hermes_cli.main config set plugins.entries.bend.settings.dependency_cache "$cwd/cache" > "$cwd/config-set.out" 2>&1
echo "config_set_rc=$?" > "$cwd/cli.meta"
if [ "$mode" = verify ]; then
  args=(bend verify "$cwd/project" --receipt "$cwd/receipt.json")
else
  args=(bend replay "$state/golden-receipt-$kind.json" --project "$cwd/project" --receipt "$cwd/receipt.json")
fi
bwrap --dev-bind / / --unshare-net --die-with-parent -- bash -c '
  cat /proc/net/dev > "$1/netns.dev"; shift
  exec python -m hermes_cli.main "$@"' _ "$cwd" "${args[@]}" > "$cwd/cli.stdout" 2> "$cwd/cli.stderr"
rc=$?
echo "cli_rc=$rc" >> "$cwd/cli.meta"
exit 0
