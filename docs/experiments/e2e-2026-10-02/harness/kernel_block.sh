#!/usr/bin/env bash
# kernel_block.sh <first> <last>: K-<nn> processes of measure_kernel.py (1 cold + 10 warm each), sequential.
E=$E2E
for i in $(seq "$1" "$2"); do id=$(printf 'K-%02d' "$i")
  BEND_FIXTURE_DIR=$E/fixtures/bend-fix-solved BEND_CONFIG=$E/config/e2e-config.yaml \
  BEND_MASK_DIRS=$BEND_LANE/template/home/.hermes/installs \
    $E/bin/run-hermes-e2e.sh "$id" $E/bin/measure_kernel.py $E/runs/$id/cwd 10 $E/runs/$id/kernel.json
done
