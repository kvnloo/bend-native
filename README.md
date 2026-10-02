# Bend for Hermes

A native Hermes plugin for the edit → prove → verify loop in Bend projects.
Hermes orchestrates; Bend checks the proof. This plugin adds one model tool,
`bend_verify`, the `hermes bend` CLI, and the `bend:workflow` skill without changing
Hermes core or adding dependencies to every user's installation. Version 0.4 also
adds `hermes z0`, the `bend:z0-stack` skill, and optional downstream z0 observation
and State Packet composition; observation defaults to off.

## Install

Requires Hermes with the native plugin API, Linux or macOS, an installed
[Bend](https://github.com/bendlang/bend) CLI ≥ 2.0.32, and Lean 4.34.0 (`lean` and
`leanc` on PATH, or the corresponding elan toolchain). Use the compiled Bend
release layout including its adjacent `bend2` directory; a shell wrapper is not
an identifiable verifier installation.

```sh
hermes plugins install kvnloo/bend-native
hermes plugins enable bend
hermes bend doctor
hermes bend verify /absolute/path/to/project --receipt receipt.json
```

The standalone plugin lives in [`kvnloo/bend-native`](https://github.com/kvnloo/bend-native).
For a reproducible installation, append `--ref <reviewed-40-character-SHA>`.
The pending Hermes catalog entry pins an exact revision; after catalog admission,
`hermes plugins install bend` selects that reviewed revision.

Start a new Hermes conversation after enabling the plugin, then ask:

> Load bend:workflow, update the implementation and its proof, and use bend_verify
> on /absolute/path/to/project. Preserve the agreed LAWS.bend and report the receipt.

The plugin is opt-in. The tool reports actionable errors when Bend or the project
is missing. Paths are on the **Hermes host**; files inside an SSH/Docker terminal
backend must first be made available there. The plugin does not silently verify a
similarly named host directory.

Use **Capabilities → Plugins → Bend executable** in the Desktop, or set this in
the active profile's `config.yaml`:

```yaml
plugins:
  enabled: [bend]
  entries:
    bend:
      settings:
        executable: /absolute/path/to/bend/bin/bend
        dependency_cache: /absolute/path/to/.bend/lib  # optional; host default when empty
```

`doctor` checks version and installation identity. `verify` builds a private
BendTT kernel on first use (up to 120 seconds); subsequent calls in the same
profile/process reuse that exact kernel (30-second verdict budget). CLI commands
are separate processes and therefore compile separately. Disable/unload cleans
up the private kernel; restarting is required after upgrading Bend in a running
Hermes process.

## Evidence contract

The JSON receipt records the local input manifest, proof, executable, Base library,
kernel source and kernel binary hashes, elapsed time, exit code and bounded
compiler output. `success: true` means only an exact Bend `ALL PROOFS CHECK`
response on the captured inputs, with no observed input/runtime identity change.
`verification_scope: bend-emitted-book` and `source_semantics_attested: false`
are always explicit.

A kernel PASS is **not** a proof of compiler/source equivalence, application
correctness beyond the written laws, AODL admission, or `verified_success`.
Bend releases 2.0.32–2.0.34 have a documented
[translation limitation](https://github.com/bendlang/bend/issues/1212).
Closing that upstream issue does not qualify an older release. Keep ordinary
tests and review the actual laws alongside proof evidence.

| Result | Meaning |
| --- | --- |
| `pass` | Bend accepted the captured proof and emitted book |
| `fail` | Bend exited unsuccessfully; inspect diagnostics |
| `timeout` | Verification exceeded its budget; child process group stopped |
| `indeterminate` | Zero exit without the exact verdict protocol |
| `unstable` | Source, verifier installation, or selected kernel changed |
| `error` + `code` | Setup, capture, unsupported input, or integrity error |

CLI exit codes: 0 for PASS/doctor success, 1 for a non-passing verdict, 2 for an
adapter/setup error. The plugin never edits project files or repairs proofs.
Local import closures are snapshotted before execution; symlink imports and paths
outside the project are rejected. Named and content-hash Hub imports are supported
from an existing Bend cache. The plugin recomputes each complete package's sorted
publication manifest, checks its content address, captures transitive imports and
name bindings, and records all identities. Missing or altered packages fail
closed. Fetch packages with Bend first; verification itself never downloads them.
The captured package cache is private and rechecked after the verdict.
Ambient Bend kernel/Hub/cache overrides and unrelated credentials are not passed
to the verifier; telemetry is disabled.

## Receipts and offline replay

Every verdict saves the most recent metadata receipt in the active profile's
native plugin state. No project source is stored there. A storage failure is
reported explicitly via `receipt_saved: false`; it never masquerades as saved
evidence. Use `--receipt` to retain multiple JSON receipts; export refuses to
overwrite an existing file.

```sh
hermes bend last-receipt
hermes bend replay receipt.json --project /absolute/path/to/project --receipt replay.json
```

Replay requires the original local inputs, the captured Hub packages/name
bindings in the configured cache, and exactly matching Bend installation and
kernel identities. It runs verification again without fetching dependencies.
Changed inputs/installations return `receipt_stale`; a different rebuilt kernel
or verdict returns `replay_mismatch`. A JSON receipt is metadata, not an archive
of project sources or a signed attestation. Copy the project and cache separately
when moving machines. It carries no execution grant.

## Zer0 boundary and provenance

This derives from [Hermes experiment #325](https://github.com/kvnloo/hermes-agent/pull/325),
with its plugin commit history retained. The separate
[z0intelligence Bend gate](https://github.com/kvnloo/z0intelligence/tree/exp/bend-aodl-gate/bend/aodl_gate)
is a proof/CI consumer: run `hermes bend verify /path/to/z0intelligence/bend/aodl_gate`
from that branch to check its laws. Its documented parity results are measured,
not proof of equivalence to the canonical AODL validator. Its lack of a latency
advantage means the Python admission gate remains authoritative.

AODL owns intent and authority contracts; z0intelligence owns admission and
outcome decisions; Hermes executes tools; Bend checks proof evidence; z0 owns
the repository map. No hook in this plugin grants execution authority, blocks
unrelated work, changes the conversation's cached prompt, or mints task success.

The [verified learning control loop](https://github.com/kvnloo/z0/issues/15)
consumes this as a **verifier artifact**, not a second decision/outcome protocol.
Existing Hermes observers and z0 adapters retain their session/trace/turn,
opportunity and attempt identities. A consumer can attach the receipt file's
content hash and location to its existing evidence references; it must validate
the receipt's input/runtime identity and narrow evidence scope before crediting
any particular outcome. This plugin does not automatically join or promote
outcomes, write the lifelong event ledger, or update beliefs.

Relevant contracts are [DecisionOpportunity](https://github.com/kvnloo/z0intelligence/issues/53),
[independent outcome credit](https://github.com/kvnloo/z0intelligence/issues/54),
[cross-harness identity](https://github.com/kvnloo/z0intelligence/issues/62), and
[memory provenance](https://github.com/kvnloo/z0intelligence/issues/66).
The original [adapter RFC](https://github.com/kvnloo/hermes-agent/issues/324)
and its [kernel](https://github.com/kvnloo/hermes-agent/issues/388),
[compiler qualification](https://github.com/kvnloo/hermes-agent/issues/389), and
[dependency replay](https://github.com/kvnloo/hermes-agent/issues/390) follow-ups
define the verification boundary. Automated edit gates or task-success claims
need their own qualified evidence; enabling this plugin does not establish them.

## Development

The runtime is Python's standard library plus Hermes's documented plugin APIs.
Use an existing Hermes checkout and its test interpreter:

```sh
HERMES_PYTHON=/path/to/hermes/test-env/bin/python \
  /path/to/hermes/scripts/run_tests.sh /path/to/bend-native/tests --import-mode=importlib
```

Tests load the plugin through Hermes's real discovery path. Real-kernel smoke:
install this checkout into a disposable `HERMES_HOME`, put Bend and Lean on PATH,
then run `hermes bend verify examples/basic` and check the returned scope and hashes.
The repeatable `scripts/smoke.py` exercises real plugin discovery/dispatch,
valid and invalid proofs, named Hub snapshots, replay, per-profile kernels and
A→B→A receipt isolation:

```sh
python scripts/smoke.py --hermes-root /path/to/hermes --bend /path/to/bend/bin/bend \
  --lean-bin /path/to/lean/bin --output /tmp/bend-smoke.json
```

Add `--aodl-project /path/to/z0intelligence/bend/aodl_gate` to also verify the
existing downstream gate's proof project. The smoke creates disposable profiles.

## License

This repository uses [AGPL-3.0](LICENSE). The inherited Hermes material retains
its original [MIT copyright and permission notice](LICENSES/hermes-MIT.txt).

## Bundled z0intelligence stack (0.4)

This package now composes the existing downstream Hermes observer, API-attempt
shadow scorer/evaluator, z0 State Packet reducer and DecisionOpportunity builder.
Their source files are copied **unchanged**, with commit pins and SHA-256 hashes
in [stack/sources.json](stack/sources.json). The native bridge owns configuration,
profile isolation, bounded delivery and subprocess lifetime; z0 owns semantics.
The original z0 MIT notice is retained in [LICENSES](LICENSES/z0intelligence-MIT.txt).

```sh
hermes z0 status
hermes z0 state /absolute/path/to/repository
hermes z0 opportunity /absolute/path/to/repository 'What is the current branch?'
```

State and opportunity commands use the bundled standard-library reducers in an
isolated child, with a 30-second deadline and 1 MiB output limit. They read Git and
repository documents, preserve source revisions and invalidate changed packets.
Personal transcript and resource adapters, GitHub/network retrieval and models
are excluded from this default projection. The deterministic gate is a shadow
baseline, not a permission to execute or a learned policy.

Enable optional observation in the active Hermes profile's `config.yaml`:

```yaml
plugins:
  enabled: [bend]
  entries:
    bend:
      settings:
        stack_mode: shadow
        stack_opportunities: true
        stack_service_port: 11501
```

`stack_mode` defaults to `off`. Shadow hooks always return `None`; they do not
change prompts, tools, provider choices or the active context engine. Metadata
is buffered in a bounded queue and written under that profile's
`plugin-data/bend/z0/`. Ambient lab raw-capture flags cannot enable raw request,
response or tool-content persistence here. `stack_opportunities` is separately
opt-in: it stores user requests up to 8,192 characters in opportunity records and
runs the existing reducer in the background. System/cron/subagent messages are
excluded. Queue exhaustion drops observations; report exposes delivery errors.
Unload waits at most two seconds; accepted background projections can finish
within their existing 30-second subprocess deadline.

API-attempt examples preserve the existing `session_id`/`turn_id` trace and
physical request identity. `post_llm_call` records observed behavior, not an
optimal-action label. Bend tool events attach scoped receipt references on the
same trace; proof PASS never becomes whole-task success or an authority grant.

```sh
hermes z0 report --examples-output examples.jsonl
hermes z0 score examples.jsonl --backend laya_421m \
  --z0-root /path/to/z0intelligence --z0-home /path/to/.z0int --python /path/to/z0-venv/bin/python > scored.jsonl
hermes z0 evaluate scored.jsonl
```

Example exports refuse overwrite. Scoring explicitly invokes the existing z0
DecisionBackend in its own Python environment; install/configure its dependencies
and models using that repository's onboarding instructions. Enabling the plugin never downloads models. Explicit scoring follows the chosen
backend's model loading behavior and uses the explicitly selected `--z0-home`. Scores are JSONL, have a 120-second child
deadline and a 1 MiB output limit; split large datasets into bounded batches.
Evaluation uses the existing Brier/log-loss/calibration implementation and always
reports `promotion_ready: false`. API failure evidence is scoped to API failure.

The larger execution stack remains a **single canonical z0 runtime** rather than
another copy of its authority, model services, quota accounting or receipt ledger:

```sh
# Start/configure the existing z0 service according to its repository docs.
hermes z0 runtime providers
hermes z0 runtime route --request-file canonical-request.json
hermes z0 runtime worker --request-file explicit-worker-request.json
```

The bridge calls the existing loopback `/v1/providers`, `/v1/intelligence`,
`/v1/worker`, `/v1/automatic` and `/v1/automatic/consumed` API contracts unchanged.
**`route` can execute an admitted function or worker**, as in z0's existing
`route_worker`; it is not a dry run. Submit explicit stable trace identity and
remote consent using the service's documented JSON schema. Execution mutations
require a request file. HTTP errors do not trigger retries, fallback providers or
synthetic success. A timed-out execution has an unknown outcome until reconciled
against z0's canonical receipt using the same identity.

The tested service revision is pinned in the source inventory. A live downstream
service smoke verified provider discovery, `PARENT_ONLY` admission and replay of
the same request without execution. Configured model inference requires that
separate runtime; this release does not claim model/task-quality qualification.
OptMem/global belief writing, EvolutionLab promotion, alternate context engines,
SoL-Pi private engine wrappers and scheduler replacement are not enabled by this
composition. They have distinct ownership and qualification requirements in the
RFCs and can remain companion components without core monkeypatches.
