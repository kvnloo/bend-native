#!/usr/bin/env bash
# AODL lane driver: runs the pre-registered runs (PREREG.json) in order. Continues past failures.
set -u
B=${AODL_LANE}
L=$B/run-hermes-aodl.sh
R=${RUNS}
F=$B/fixtures
log=$B/run-all.log
v() { local id=$1 fx=$2 ver=$3
  echo "$(date -u +%FT%TZ) start $id verify $fx $ver" >> $log
  BEND_VERIFIER=$ver BEND_FIXTURE_DIR=$F/$fx $L $id -m hermes_cli.main bend verify $R/$id/cwd --receipt $R/$id/receipt.json >> $log 2>&1
  echo "$(date -u +%FT%TZ) end $id rc=$?" >> $log; }
r() { local id=$1 fx=$2 ver=$3 rc=$4
  echo "$(date -u +%FT%TZ) start $id replay $fx $ver $rc" >> $log
  BEND_NET_NONE=1 BEND_VERIFIER=$ver BEND_FIXTURE_DIR=$F/$fx $L $id -m hermes_cli.main bend replay $R/$rc/receipt.json --project $R/$id/cwd --receipt $R/$id/replay-receipt.json >> $log 2>&1
  echo "$(date -u +%FT%TZ) end $id rc=$?" >> $log; }
v aodl-v-off-01 pristine official
v aodl-v-off-02 pristine official
v aodl-v-off-03 pristine official
v aodl-v-pat-01 pristine patched
v aodl-v-pat-02 pristine patched
v aodl-v-pat-03 pristine patched
r aodl-r-off-01 pristine official aodl-v-off-01
r aodl-r-pat-01 pristine patched aodl-v-pat-01
v aodl-m-law-off-01 law-mut official
v aodl-m-law-pat-01 law-mut patched
v aodl-m-impl-off-01 impl-mut official
v aodl-m-impl-pat-01 impl-mut patched
r aodl-s-law-off-01 law-mut official aodl-v-off-01
r aodl-s-law-pat-01 law-mut patched aodl-v-pat-01
r aodl-s-impl-off-01 impl-mut official aodl-v-off-01
r aodl-s-impl-pat-01 impl-mut patched aodl-v-pat-01
r aodl-c-readme-off-01 readme-mut official aodl-v-off-01
r aodl-c-readme-pat-01 readme-mut patched aodl-v-pat-01
r aodl-c-xrt-offrcpt-patbin-01 pristine patched aodl-v-off-01
r aodl-c-xrt-patrcpt-offbin-01 pristine official aodl-v-pat-01
echo "$(date -u +%FT%TZ) ALL DONE" >> $log
