# B390: Hub dependency provenance and replay (kvnloo/hermes-agent#390)

**Result: all five #390 acceptance criteria hold for the packaged plugin (bend-native e85e65e5).** There were 427 matrix rows plus 20 real CLI runs, and every one matched its pre-registered outcome. Two items are findings, not defects: one error code differs from the documented one (F1), and replay depends on a retained cache (F2). The plugin's behaviour was not changed.

The lane follows the 2026-10-01 meta triage on kvnloo/hermes-agent#324. It answers trust-boundary question 3 (remote/BendHub dependency provenance and replayable receipts) and is not a broad semantic matrix. A proof PASS here is scoped evidence over Bend's emitted book (`verification_scope: bend-emitted-book`, `source_semantics_attested: false`). It is never task success, and it grants no authority.

## What was tested

The plugin (`dependencies.py`, `verify_core.py`, `receipts.py`) was installed in the isolated Hermes profile (Hermes ad31bbf0) and driven as installed:
- verification goes through the `bend_verify` tool dispatch;
- replay goes through `receipts.replay`, the `hermes bend replay` code path;
- 20 runs used the real `hermes bend verify|replay` CLI.

The fixtures (`harness/fixtures.py`) are five Hub packages built with Bend's own publication identity rule:

| Package | Identity | Content |
| --- | --- | --- |
| P_ok | `0xa40ae7ca…` | Main imports ./Util, zero = 0n |
| P_bad | `0xa6808672…` | Same shape, zero = 1n |
| Q_ok | `0xf9dba70d…` | Named payload importing P_ok by hash |
| Q_alt | `0x6ebc2d66…` | Other bytes, zero = 0n |
| Q_bad | `0xdba1a710…` | zero = 1n |

A private static hub on 127.0.0.1 serves these packages, and every cache was populated by **Bend's own fetch** (`bend PROOF.bend --check-only`, `BEND_LIB`=private cache, `BEND_HUB`=private hub). Bend checks every fetched byte against the hashes, which independently confirms the identities. The host's `~/.bend/lib` and the public hub were never used.

There are two import kinds:
- **H**: a content-hash import, `0x<P_ok>/Main.bend`.
- **N**: a named import, `sample@1.0.0.0/Main.bend` bound to Q_ok, with a transitive hash import of P_ok.

The forging projects HF and NF use P_bad and Q_bad.

**Offline** means the whole harness process, and therefore the verifier child, runs under `bwrap --unshare-net`. Only an unconfigured `lo` exists there. The hub keeps serving in the outer namespace, and its request log must not grow.

## Results

Evidence labels: REAL means real Bend 2.0.34 (or the patched build), a Lean 4.34.0 kernel, the installed plugin, Bend's own fetch and the CLI. FIXTURE marks the fixture packages, the private hub and the O14 interposition. n is per kind unless stated.

| Cell | Condition | H | N | Evidence |
| --- | --- | --- | --- | --- |
| O1 / F1 cold cache, no fetch | online / offline | `dependency_missing` 5/5, 5/5 | `input_capture_failed` 5/5, 5/5 (F1) | REAL |
| O2 cold → Bend fetch → verify | online | pass 5/5, exact ids; fetch hub requests > 0; plugin hub requests 0; fetched cache = fixture layout | same | REAL |
| F2 Bend fetch offline | offline | fetch fails ("Unable to connect"); plugin `dependency_missing` 5/5 | `input_capture_failed` 5/5 | REAL |
| O3 / F3 warm cache | online / offline | pass 5/5, 5/5; one input manifest = online golden | same | REAL |
| O4 / F4 benign edit in cache | online / offline | plugin `dependency_integrity` 10/10; **raw Bend control passes 10/10** | same | REAL |
| O5 / F5 forging edit (fail → pass in place) | online / offline | before: fail 10/10; **raw Bend after edit: pass 10/10 (forged)**; plugin `dependency_integrity` 10/10 | same | REAL |
| O6 extra file in package | online | `dependency_integrity` 5/5 | 5/5 | REAL |
| O7 file removed from package | online | `dependency_integrity` 5/5 | 5/5 | REAL |
| O8a–d name binding missing / garbage / absent package / re-pointed | online | n/a | `input_capture_failed` 5/5; `dependency_integrity` 5/5; `dependency_missing` 5/5; fresh verify pass 5/5 **naming Q_alt** | REAL |
| O9 replay, same cache | online | pass + `replay_match` 5/5, hub requests 0 | same | REAL |
| O10 / F7 replay, cache missing | online / offline | `dependency_missing` 10/10 | `input_capture_failed` 10/10 | REAL |
| O11 replay, cache mutated | online | `dependency_integrity` 5/5 | 5/5 | REAL |
| O12 replay after Bend refetch (hub unchanged) | online | pass + match 5/5 | 5/5 | REAL |
| O13a hub re-points sample@1.0.0.0 → Q_alt, refetch, replay | online | n/a | **`receipt_stale` 5/5**; fresh-verify control passes naming Q_alt | REAL |
| O13b / F8 local binding re-pointed, replay | online / offline | n/a | `receipt_stale` 10/10 | REAL |
| O13c hub re-pointed, warm local cache, replay | online | n/a | pass + match 5/5, **hub requests 0** | REAL |
| O14 cache changed after replay's precheck, before re-verify | online | `dependency_integrity` 5/5 | **`replay_mismatch`** 5/5 (`input_manifest_sha256` differs, success false) | FIXTURE interposition + REAL verifier |
| O15 / F9 cache changed while the `--verdict` child runs | online / offline | `unstable` 10/10 (landed 10/10) | `unstable` 10/10 (rebind; landed 10/10) | REAL |
| O16 private snapshot BEND_LIB edited during the verdict | online | `unstable` 5/5 (landed 5/5) | 5/5 | REAL |
| O17 4 concurrent Bend fetches into one cold cache, then verify | online | pass 5/5; fetch rc 0 ×20; cache = golden | same | REAL |
| O18 plugin verifies racing 4 Bend fetches | online | see below | see below | REAL |
| W1 3 Hermes processes released together on one shared warm cache | offline | pass 15/15 | pass 15/15; shared cache unchanged; 57 overlapping cross-worker verify pairs | REAL |
| E1 patched verifier, warm cache | offline | pass 5/5; dependency ids and input manifest equal to the official run | same | REAL |
| E2 replay of an official receipt with the patched verifier | offline | `receipt_stale` 5/5 | 5/5 | REAL |
| D CLI `hermes bend verify --receipt` | offline, new process each | exit 0 pass 5/5, exact ids | 5/5 | REAL |
| D CLI `hermes bend replay <online receipt>` | offline, new process each | exit 0 pass + match 5/5 | 5/5 | REAL |

**O18 race.** There were 980 back-to-back plugin attempts while the fetches wrote the same cold cache:

| Outcome | Attempts |
| --- | --- |
| `dependency_missing` | 537 |
| `input_capture_failed` | 416 |
| `dependency_integrity` | 1 (a package caught mid-write) |
| pass, exact identity | 26/26 |

Every final verify after the fetches passed: 10/10.

Kernel hashes: official `a7e5203d…` in 211 receipts, patched `72e11a86…` in 11. Every new process, including the 20 CLI runs, rebuilt the same hash. That is why replay in a new process matches the recorded `kernel_sha256_after`.

## Acceptance (#390)

| # | Criterion | Result |
| --- | --- | --- |
| A1 | Every successful receipt names immutable dependency identities | **Pass.** 181 successful receipts (161 matrix + 20 CLI): `dependency_packages`, `dependency_names`, `dependency_manifest_sha256` and `input_manifest_sha256` all equal identities re-derived independently from the fixture bytes. 0 mismatches. |
| A2 | Replay cannot silently resolve a different package | **Pass.** 0 successes in 35 rows: O13a, O13b and F8 are `receipt_stale`; O14-N is `replay_mismatch`; O14-H is `dependency_integrity`; E2 is `receipt_stale`. |
| A3 | Cache mutation cannot produce a successful receipt for unrecorded bytes | **Pass.** 0 plugin successes in 105 mutated rows, while the 40 raw Bend controls on the same caches all pass, 20 of them forged (fail → pass). During-verdict mutations landed while the child was alive in 30/30. |
| A4 | Offline replay works once dependencies are captured | **Pass.** 10/10 offline replays of the online-captured receipt (F6). 10/10 CLI replays in a fresh Hermes process with no network. |
| A5 | No network needed after resolution/snapshot | **Pass.** 0 hub requests across 125 online plugin calls, though the hub was reachable (Bend's own fetches did reach it). The outer hub log was unchanged during all three namespaced phases. The offline namespaces had only `lo` and the hub connect was refused. All 20 CLI runs had only `lo`. |

## Findings

**F1: the error code for a missing name binding (minor; fail-closed preserved).** A named import whose `names/<name>@<version>` file is absent returns `input_capture_failed`, not the `dependency_missing` that the source intends.
- `dependencies.py` catches `(OSError, UnicodeError)` around `_read_stable(mapping)`.
- But `_read_stable` already converts `OSError` into `BendVerifyError('input_capture_failed', …)`, so the `dependency_missing` "No usable cached binding" branch cannot be reached for a missing file.
- This was predicted from SOURCE in PREREG and observed in 30/30 rows (O1-N, O8a, O10-N, F1-N, F2-N, F7-N), plus 416 racing attempts.
- Impact: callers that branch on `dependency_missing` to suggest `bend` fetching will miss named imports. A two-line fix would catch `BendVerifyError` with code `input_capture_failed` there and re-raise it as `dependency_missing`. That is not applied on this branch.

**F2: replay requires a retained cache (a design limit, as documented).** A receipt is metadata, not an archive. With the cache gone, replay fails closed in 20/20 runs (O10, F7) and never re-resolves.
- After a Bend refetch from the hub, content-hash replays match (5/5).
- Named replays match only while the hub binding is unchanged: 5/5 match, but 5/5 `receipt_stale` after the hub re-pointed the name.
- So "offline replay once captured" holds exactly as long as the captured cache, or a byte-identical copy, is kept with the project. This matches the README ("Copy the project and cache separately").
- If replay must survive cache loss, the closure bytes would need to be retained beside the receipt. That is an owner decision, not a defect.

**F3: Bend by itself trusts cached bytes (upstream behaviour; context for A3).** In bendlang/bend v2.0.34 (`bend2/bend.ts`), `book_file` only fetches, and so only hash-checks, when a file is missing. `name_hash` keeps the first cached binding. A cache edited in place is therefore used as is: raw `bend --verdict` passed 40/40 mutated caches, including 20 forged fail → pass. The plugin's recomputation of the publication manifest at capture time is what closes this hole.

**F4: a fresh verify after a re-pointed name is a new identity, not a replay.** O8d and the O13a control pass and correctly name Q_alt. Consumers must compare `dependency_names`/`dependency_packages` (or `input_manifest_sha256`) with the receipt they credit. A PASS on the same project path alone does not prove the same dependency set.

**F5 (positive): race coverage.** The replay window between precheck and verify is covered by the post-verify comparison (O14-N gives `replay_mismatch`). Mutation during the verdict is caught by the post-verdict recapture and the BEND_LIB re-hash (O15, O16, F9 all `unstable`). Concurrent Bend resolution never led to a success with unrecorded bytes (O17, O18, W1).

**Source note (not tested):** `schemas/receipt.schema.json` does not require the dependency fields, even for `success: true`. A1 holds because the implementation always emits them. Consumers who validate only against the schema would not notice a receipt without them.

## Limitations

- **Fixture hub, not the public hub.** It speaks Bend's GET protocol, and Bend's own hash checks accepted it. The public hub.bend-lang.com was not contacted: NOT_RUN. The public hub's own name-rebinding policy is not tested; O13a simulates a re-point.
- **Linux only**, on a single host. macOS is NOT_RUN.
- **O14 is a deterministic interposition**, not a natural race.
- **"Landed" for during-verdict mutations** means that the Bend `--verdict` child (a direct child of the harness with `--verdict` in its cmdline) was still alive after the mutation was written.
- **No latency is reported.** `duration_ms` and the worker `t_start` stamps are not results; `t_start` is used only to show overlap.
- **Isolation.**
  - All 24 launcher runs saw empty masks of the live Hermes home, the real home, `/run/user` and X11.
  - The live Hermes `state.db`, `-wal`, `config.yaml`, `.env`, `auth.json` and `logs` stats were unchanged in 24/24. Only the live home directory's own mtime moved, as in setup: the user's running Hermes touches it, and the launcher masks it from lane processes.
- **No model, z0 service or GPU** was used.
- **Pilot runs (n=1), disclosed in PREREG,** are excluded from these results.

## Files and reproduction

- `PREREG.json`: committed in 7eb782d before any measured run. The harness is unchanged since; see `provenance.json`.
- `harness/`:
  - `fixtures.py`: packages, hub tree and projects;
  - `matrix.py`: phase driver;
  - `cli_run.sh`: CLI runs;
  - `run_lane.sh`: the measured sequence;
  - `collect.py`: copy and path scrub;
  - `analyze.py`: produces `summary.json`.
- `raw/`:
  - `online|offline|workers|patched/`: `rows.jsonl`, `notes.jsonl`, `receipts/*.json` (every receipt or error) and the hub request logs;
  - `cli/`: CLI stdout, receipts and `netns.dev`;
  - `launcher/`: run meta, masks and live-home stat.

  Host paths are replaced by `$RUNS`, `$BEND_STACK` and similar variables (see `provenance.json`).
- `summary.json`: per-cell outcome histograms and A1–A5.
- `verify_artifacts.py`: an independent re-check (standard library plus `harness/fixtures.py`, never the plugin). Run it with `python3 verify_artifacts.py`; it reports 0 failures.

To reproduce, set `LAUNCHER`, `WORK_ROOT` and `PATCHED_BEND`, then run `bash harness/run_lane.sh <tag>`. It needs the bend-stack launcher, Bend 2.0.34, Lean 4.34.0 and the plugin installed in the launcher's template profile.
