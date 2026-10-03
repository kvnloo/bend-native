#!/bin/sh
# Harness glue passed as 'hermes z0 score --python': the plugin's score child keeps only PATH/HOME/TMPDIR/proxies
# (verify_core.clean_env) plus Z0INT_HOME, so the offline/checkpoint settings of the existing scorer environment
# (stack Z0_SETUP.json scorer_env) are re-applied here before exec'ing the z0 venv interpreter unchanged.
export HOME=$E2E/z0score-home HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export HF_HUB_CACHE=$MODELS/huggingface/hub HF_HOME=$E2E/z0score-tmp/hf-home
export Z0INT_LAYA_DEVICE=cpu Z0INT_JULIA_DEVICE=cpu Z0INT_JULIA_PYTHON=$REAL_HOME/tmp/julia-venv/bin/python
export Z0INT_NANOJEV_CHECKPOINT=$E2E/z0home/models/nanojev_06b PYTHONDONTWRITEBYTECODE=1
exec $Z0_WT_ROOT/venv-stack-z0/bin/python "$@"
