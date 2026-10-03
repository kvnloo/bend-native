#!/usr/bin/env bash
# fo_stop.sh <svc-run-id> <delay_s> <out.json>: stop the private z0 service <delay_s> seconds from now, from the same
# (plain-shell) domain that started it. Measured run M-bend_fix-06 showed a stop issued from inside hostless is
# refused by the Landlock signal scope (EPERM), so the stop must come from outside the driver.
set -uo pipefail
sleep "$2"
t="$(date +%s.%N)"
$Z0SVC_LANE/run-z0svc.sh stop "$1" > "$3.log" 2>&1; rc=$?
if ss -ltn | grep -q '127.0.0.1:11523 '; then up=true; else up=false; fi
printf '{"svc":"%s","stop_issued_at":%s,"rc":%d,"port_11523_listening_after":%s}\n' "$1" "$t" "$rc" "$up" > "$3"
