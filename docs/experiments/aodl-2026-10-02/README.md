# AODL proof consumer: receipts, offline replay and one-byte mutations (2026-10-02)

**Lane:** AODL, in the Hermes x z0 x Bend integration track. **Plugin:** kvnloo/bend-native
`e85e65e5`, unchanged. **Hermes:** `exp/bend-stack-integration-20261002` `ad31bbf0`.
**Proof project:** the z0intelligence AODL structural gate, `exp/bend-aodl-gate` `a0e95785`,
`bend/aodl_gate`. **Pre-registration:** [`PREREG.json`](PREREG.json), commit `33654c36`,
committed 21 s before the first measured run.

## Where this fits

bend-native is the packaging seam of the integration. Bend proof evidence and the z0 stack
pieces ship as one opt-in native Hermes plugin, with no Hermes core change. In the
ownership split, Hermes runs the tools and Bend checks proof evidence over the emitted
BendTT book only (`verification_scope: bend-emitted-book`, `source_semantics_attested: false`).
z0intelligence owns admission and outcome decisions, and AODL owns intent and authority.

This lane runs the plugin's supported CLI on a real downstream proof project, outside the
Bend corpus. The 2026-10-01 meta triage on kvnloo/hermes-agent#324 set the remaining work,
and this lane covers two of its threads:

- **Replayable receipts (#390):** without Hub dependencies, which this project does not use.
- **Patched candidate on a real consumer project (#389):** this is one input to the
  release-qualification gate, not the gate itself.

**A PASS here is not task success, not an admission decision and not an authority grant.**

## Results

Every row was run once, as pre-registered. Nothing was dropped or retried. 20
pre-registered runs plus 2 post-hoc runs, all REAL. Two launcher probes are listed
separately and are not results.

| # | row | evidence | result |
|---|---|---|---|
| R1 | `hermes bend verify`, pristine project, stock 2.0.34 | REAL | **3/3 pass**: `ALL PROOFS CHECK`, exit 0, 6-file closure, manifest `04e7c7c5…`, kernel `a7e5203d…` |
| R2 | the same with patched kvnloo/bend@17db447a | REAL | **3/3 pass**: same manifest, kernel `72e11a86…` |
| R3 | `hermes bend replay`, offline (own netns, only `lo`), of rep 01 of R1/R2 | REAL | **2/2** `replay_match: true`, `replay_differences: []`, verdict pass |
| R4 | verify after a 1-byte change: LAWS.bend (`1n+ks` → `2n+ks` in `spawn_within_bounds`) and transition.bend (`1n+ks` → `0n+ks`), on both builds | REAL | **4/4 verdict changes pass → fail** (exit 1). The manifest changes (`561d4840…` / `0b867abe…`) while `proof_sha256` stays the same (PROOF.bend is untouched) |
| R5 | offline replay of the pristine receipts against both mutants, on both builds | REAL | **4/4 `receipt_stale`**, "Project inputs differ from the recorded receipt". Bend never ran and no replay receipt was written |
| R6 | control: 1 byte changed in README.md, which is outside the import closure | REAL | **2/2 `replay_match: true`**. The README is not part of the receipt identity |
| R7 | control: each build's receipt replayed with the other build | REAL | **2/2 `receipt_stale`**, "Bend installation differs from the recorded receipt" |
| R8 | *post-hoc, not pre-registered:* offline replay of the R4 FAIL receipts | REAL | **2/2 `invalid_receipt`**, "Receipt is missing input/kernel identity" (finding F1) |
| R9 | kernel reproducibility | REAL | 5 fresh session bootstraps per build (3 verify, 1 replay, 1 control replay). All official runs give `a7e5203d…` and all patched runs give `72e11a86…` |
| R10 | parity of the Bend gate with the canonical validator | SOURCE (restated) | see below; not re-measured |
| R11 | the Python admission gate stays authoritative | SOURCE (restated) | see below |
| R12 | latency | NOT_RUN | by design. `duration_ms` fields in the receipts are incidental and are not results |
| R13 | bendlang/bend issue 1212 oracle on this project | NOT_RUN | this project does not exercise the group-routing bug. Stock and patched agree here, which says nothing about issue 1212 |

The pre-registered predictions P1–P6 held on every run: P1 6/6, P2 2/2, P3 4/4, P4 4/4,
P5 2/2 and P6 2/2. P7 is descriptive: both builds give the same verdict and manifest, with
different kernel hashes.

## Findings

**F1 — A FAIL receipt that the plugin exports cannot be replayed by the same plugin** (minor;
fail-closed).

- When Bend rejects a proof before the BendTT kernel is built, `verify` still exports a
  receipt, as the create-only `--receipt` path does. That receipt has
  `kernel_sha256_after: null` and `kernel_strategy: session-uninitialized`.
- `schemas/receipt.schema.json` allows null there. But `receipts.read_receipt` requires a
  non-empty string, so `replay` refuses the receipt as `invalid_receipt` (R8, both builds).
- The README says "Every verdict saves … receipt" and "Changed inputs/installations return
  `receipt_stale`". For these receipts, replay does neither.
- A negative verdict therefore cannot be reproduced through `replay`. It can never turn into
  a pass, so this is not a forgery path.
- Possible fixes, owner's choice: let replay accept a null kernel identity when the verdict is
  not `pass` and compare `execution_verdict`, or document that only kernel-backed receipts
  are replayable.
- No code was changed on this branch.

**F2 — The one-byte mutants were caught by Bend's own checker, not by the Lean-checked kernel.**

- All 4 mutant runs printed `SOME PROOFS FAIL`.
- The error was pinned to `Laws.spawn_within_bounds`. Expected and observed show
  `2n+ks` vs `1n+ks` for the law mutant, and `1n+ks` vs `ks` for the implementation mutant.
- This happened before any BendTT kernel was built (`kernel_sha256_after: null`).
- The receipt states this honestly. A consumer must not read "verdict changed" as "the
  kernel rejected the book".
- Showing a kernel-level rejection would need a mutation that Bend's front end accepts. This
  lane did not look for one.

**F3 — The receipt covers the proof's import closure plus LAWS.bend, not the runtime entry
point.**

- The closure is 6 files: PROOF, LAWS, core, rules, transition and gate.
- `main.bend` is outside it, as is README.md (R6). `main.bend` is the effect shell, and
  z0int's `build_kernel()` compiles it into the runtime gate.
- So a receipt attests the laws over `gate.decide` and its callees. It does not attest the
  binary that z0 runs.
- This matches the stated scope, and the AODL README already says that runtime agreement is
  measured, not proven. Consumers should record the runtime kernel's own hash next to the
  receipt.

**F4 — Isolation notes** (no effect on results).

- Inside the replay netns, `getent hosts` still resolved names through the host resolver's
  socket, but TCP connect failed with "Network is unreachable" (probe `aodl-probe-net-01`).
- The live Hermes home's directory mtime advances about every 5 s from the owner's own
  services, with no lane run active (`raw/ambient-live-home-stat.txt`). The verifier
  therefore compares only the snapshotted files.
- One run, `aodl-r-off-01` (03:19:49–03:20:32Z), saw the live `auth.json` mtime move from
  21:35:03 to 22:20:01 local time, with the size unchanged.
- That run had the live home masked by an empty tmpfs (`mask.inside`
  `live_hermes_home_entries=0`) and had no route off the host (netns with only `lo`). So it
  could neither see nor refresh that file.
- A stat-only watch after the last AODL run saw the same file's mtime move again, to
  23:04:59 local (04:05Z), with no AODL run active (`raw/ambient-auth-mtime-watch.txt`).
  The cadence of 21:35:03, 22:20:01 and 23:04:59 (about 45 min, size constant) is the
  owner's own services refreshing it. The change is listed in the invariants rather than
  hidden.

## Restated, not re-measured (SOURCE: z0intelligence `a0e95785`, `bend/aodl_gate/README.md`, `src/z0int/bend_gate.py`)

**Parity** (seed 48, kernel `fc2e56d486f6d546`):

- **Documents:** 22,079 cases, with 0 unexplained verdict disagreements and 0 false
  allows. There was 1 false deny: `hotl-0.1-fanin`, which is not modeled and is therefore
  refused. On all 9,519 kernel-routed cases without a validator exception, the issue-code
  multisets were equal.
- **Spawn transitions:** 16,356 compared, with 0 disagreements against `reference_transition`
  and 0 against `check_budget`. Another 3,644 unrepresentable proposals were denied.
- **Status:** parity is measured, not proven. The 8 laws are theorems about the kernel's own
  functions over the Core IR.

**Authority:** the canonical AODL validator (`aodl_contract.validator.validate`) stays
authoritative.

- The Bend gate is an experimental fast structural filter. Any adapter error, timeout,
  malformed reply or missing binary becomes DENY. With `canonical=` set, a validator
  disagreement also denies.
- The project recommends against putting Bend on the hot path. It is 4–25x slower than
  in-process CPython validation, and the Core IR normalizer must move into `aodl_contract`
  before Bend can be more than experimental.
- Nothing in this packet changes who decides admission: z0intelligence and the canonical
  validator, under AODL authority.

## Reproduce and check

The harness files are in `harness/`. Local paths in them are replaced with `${VARS}`;
`provenance.json` holds the sha256 of each original.

```sh
python3 verify_artifacts.py                # recompute summary.json from raw/, check predictions, scan for local paths
AODL_MIRROR=<lane mirror>/raw AODL_SCAN_EXTRA=<host name> python3 verify_artifacts.py   # also check every redacted file against its unredacted original and scan for the host name
```

Each run used
`run-hermes-aodl.sh <id> -m hermes_cli.main bend verify|replay …` under hostless and bwrap.
The isolation:

- The live Hermes home, the real home, /run/user and host X11 are masked.
- The environment is `env -i`, and the profile template is seen through a per-run overlay.
- `BEND_VERIFIER=patched` selects the patched build.
- `BEND_NET_NONE=1` adds `--unshare-net` for replays.

No chat model, no z0 service (live or private) and no GUI were used.

## Files

- `PREREG.json`: the pre-registration, including fixture hashes and predictions.
- `summary.json`: derived by `verify_artifacts.py --write`.
- `provenance.json`: identities of everything used.
- `raw/<run>/`: `meta/` (argv, exit code, masks, netns, live-home stat, worktree head,
  stdout and stderr), `receipt.json` and `replay-receipt.json`, all redacted.
- `raw/originals.sha256`: hashes of the unredacted originals.
- `harness/`: the lane launcher, the driver, the fixture hashes and the three one-byte
  diffs.
- `collect.py`: the collector.
