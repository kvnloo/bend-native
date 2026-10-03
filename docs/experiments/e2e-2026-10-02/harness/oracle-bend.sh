#!/usr/bin/env bash
# oracle-bend.sh <bend> <project> <laws sha> <out.json> : hostless, env -i, private oracle HOME (kernel cache owned by the oracle)
set -euo pipefail
E=$E2E
exec $CUA_LANES/bin/hostless env -i PATH=$BEND_STACK/lean-4.34.0-linux/bin:/usr/bin:/bin \
  ORACLE_HOME=$E/oracle/home TMPDIR=$E/oracle/tmp HOME=$E/oracle/home PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 \
  /usr/bin/python3 $E/bin/oracle_bend.py "$@"
