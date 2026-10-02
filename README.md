# Bend for Hermes

A native Hermes plugin for the edit → prove → verify loop in Bend projects.
Hermes orchestrates; Bend checks the proof. This plugin adds one model tool,
`bend_verify`, the `hermes bend` CLI, and the `bend:workflow` skill without changing
Hermes core or adding dependencies to every user's installation.

## Install

Requires Hermes with the native plugin API, Linux or macOS, an installed
[Bend](https://github.com/bendlang/bend) CLI ≥ 2.0.32, and Lean 4.34.0 (`lean` and
`leanc` on PATH, or the corresponding elan toolchain). Use the compiled Bend
release layout including its adjacent `bend2` directory; a shell wrapper is not
an identifiable verifier installation.

```sh
hermes plugins install https://github.com/kvnloo/hermes-bend
hermes plugins enable bend
hermes bend doctor
hermes bend verify /absolute/path/to/project
```

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
outside the project are rejected. Hub imports are currently rejected rather than
accepted without reproducible dependency evidence. Vendor them locally first.
Ambient Bend kernel/Hub/cache overrides and unrelated credentials are not passed
to the verifier; telemetry is disabled.

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

## Development

The runtime is Python's standard library plus Hermes's documented plugin APIs.
Use an existing Hermes checkout and its test interpreter:

```sh
HERMES_PYTHON=/path/to/hermes/test-env/bin/python \
  /path/to/hermes/scripts/run_tests.sh /path/to/hermes-bend/tests --import-mode=importlib
```

Tests load the plugin through Hermes's real discovery path. Real-kernel smoke:
install this checkout into a disposable `HERMES_HOME`, put Bend and Lean on PATH,
then run `hermes bend verify examples/basic` and check the returned scope and hashes.
