# B389: Bend release qualification for the bend-native verifier

Lane B389 runs the next gate of kvnloo/hermes-agent#389. The question: can the patched candidate
kvnloo/bend@17db447a (kvnloo/bend#2) replace stock Bend 2.0.34 as the verifier behind `bend_verify` and
`hermes bend verify`?

The measure is kvnloo/hermes-agent#389's own rule. A release is promotable only if the source value and the certified
BendTT value agree on every semantic-attestation oracle. Anything else fails closed. The Bend repo's
own corpus and the known false-negative regressions are measured as separate families.

PREREG.json was committed (50e2d1e9) before any measured run. The plugin is unchanged (e85e65e5). No
latency number is reported.

## Verdict

| release | 389 semantic gate | translation mismatches | stable new failures vs stock | false negatives (regressions, F family) |
| --- | --- | --- | --- | --- |
| **A** stock 2.0.34 | **not promotable** | 1 (the issue 1212 layout, 3 of 3 reps) | n/a | 5 of 5 |
| **B** candidate 17db447a | **promotable** | 0 | 0 | 4 of 5 (issue 1212 crash fixed) |
| C1 control: main 01875127, no patch | not promotable | 1 (same as A) | n/a | 5 of 5 |
| C2 control: v2.0.34 + the 2 lines only | promotable | 0 | 0 vs A | 4 of 5 |

B meets every preregistered condition: the 389 semantic gate (g1–g3), zero plugin false passes, the expected
binary and kernel identities, and no case that stock certifies and B does not. This is evidence for the
owner. It does not change the plugin's default executable or mint any success:
`source_semantics_attested` stays `false`, and a proof PASS remains emitted-book evidence only.

**Stock 2.0.34 through the packaged plugin today.** `bend_verify` (3 of 3 reps) and
`hermes bend verify` both return `pass` on the bendlang/bend issue 1212 program. The source computes
`False{}`, but the certified book computes a different value (`True`). Any Hermes user on a stock
release can get a PASS receipt for a book whose value differs from the source. The plugin's scope
fields disclose this, but nothing blocks it.

## Results per release (REAL, direct `bend <f> --verdict`, final class = majority of runs)

| metric | A stock | B candidate | C1 | C2 |
| --- | --- | --- | --- | --- |
| T: safe-gate corpus tests/** (1526) agree | 969 (63.50%) | 973 (63.76%) | 972 (63.70%) | 970 (63.56%) |
| P: proof corpus tests/proof (86) agree | 51 (59.30%) | 52 (60.47%) | 51 (59.30%) | 52 (60.47%) |
| X: demos + evals (70) agree | 43 (61.43%) | 43 (61.43%) | 43 (61.43%) | 43 (61.43%) |
| T false rejects `!` (bend2 checks, kernel does not) | 3 | 1 | 2 | 2 |
| T out of scope `-` | 2 | 0 | 0 | 2 |
| T bend2 rejects / unsafe `u` (intended negatives) | 487 / 65 | 487 / 65 | 487 / 65 | 487 / 65 |
| X timeout `t` (app_slash_boss_3d, near the 30 s cap) | 1 | 1 (flaky u/t/t) | 1 (flaky) | 1 (flaky) |
| derived value oracles evaluated (certified files with main : Bool/Nat/U32) | 75 | 79 | 78 | 76 |
| derived oracles where certified ≠ source / indeterminate | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| plugin `bend_verify` pass / ran (excl. 31 layout_na) | 1004/1580 (63.54%) | 1014/1580 (64.18%) | 1007/1580 (63.73%) | 1011/1580 (63.99%) |
| plugin false pass / plugin non-pass on direct agree | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| kernel sha256 (direct = plugin = CLI) | a7e5203d… | 72e11a86… | 72e11a86… | a7e5203d… |

Pass rates are bounded by the corpus design: 487 tests are meant to be rejected by bend2, and 65 rely on
unsafe or foreign code. The comparison across releases is the signal; the absolute rate is not.

### Semantic-attestation oracles (S family, 3 repetitions each, direct + plugin; CLI once)

The certified relation comes from the emitted BendTT book. Two kernel claims are appended:
`main == source value` and `main == a different value`.

| case | A stock | B candidate | C1 | C2 |
| --- | --- | --- | --- | --- |
| s1212_fb3 (issue 1212 layout, PRIMARY) | verdict **PASS**; certified **different** (source False) | PASS; certified = source | PASS; different | PASS; = source |
| s1212_prlaw_nolaw (kvnloo/bend#2 program, law removed, PRIMARY) | MISMATCH `!` (non-member selector); certified indeterminate | PASS; = source | `!`; indeterminate | PASS; = source |
| s1212_up_dead_forward_nolaw (upstream fix's test, laws removed, PRIMARY) | `!`; indeterminate | PASS; = source (5n) | `!`; indeterminate | PASS; = source |
| s1212_fb3_law_false (claim = source) | `!` fail-closed | PASS | `!` | PASS |
| s1212_fb3_law_true (claim = mis-routed book) | bend2 rejects | bend2 rejects | bend2 rejects | bend2 rejects |
| s1212_prlaw (kvnloo/bend#2 law as committed) | `!` | PASS | `!` | PASS |
| s1212_up_dead_forward (upstream test as committed) | `!` | PASS | `!` | PASS |

No false proposition passes on any arm (`s1212_fb3_law_true` is rejected everywhere). On stock, the
issue-1212 mis-routing appears in two forms. With the module named `a-b.bend`, the names collide and
stock gives a silent wrong-value PASS. In the PR's own layout (`..._lib.bend`), stock gives a fail-closed
MISMATCH instead. The kvnloo/bend#2 regression law therefore tests the routing fix, but on stock it never
reproduces the silent PASS. The fb3 oracle is the case that does.

### False-negative regressions (F family, kept apart from semantic false positives)

PASS is the correct verdict for every one of these: bend2 checks each file, and upstream diagnoses the
kernel-side rejection as a defect.

| case | A | B | C1 | C2 |
| --- | --- | --- | --- | --- |
| f1179_ab (bendlang/bend issue 1179) | `-` out of scope | `-` | `-` | `-` |
| f1179_self | `-` | `-` | `-` | `-` |
| f1193 (bendlang/bend issue 1193, kernel out of fuel) | `!` then `t`,`t` | same | same | same |
| f1194_kept (bendlang/bend issue 1194) | `!` calls that descend | `!` | `!` | `!` |
| f1212_g4 (issue 1212 crash face) | crash: raw `TypeError`, exit 1, no FAIL header | **PASS** | crash | **PASS** |
| controls f1179_ba / f1194_erased / f1194_moved | PASS ×3 | PASS ×3 | PASS ×3 | PASS ×3 |

All of these fail closed. The plugin reports `fail`, never `pass`, on every false-negative case in every
arm.

### New failures introduced by the patch

There are no cases that stock certifies (agree) and the candidate does not. The same holds for the patch
alone on main (C1 → B) and the patch alone on the stock tag (A → C2). B gains 4 cases over stock:

- `tests/check/erased_field_bare_arm.bend`, `tests/check/spec_arg_depth.bend` (`-` → agree) and
  `tests/check/spec_arg_unused_column.bend` (`!` → agree). These come from the 6 upstream commits:
  C1 gains them too, and C2 does not. They are tests that those commits added, so stock fails closed on
  its own newer tests.
- `tests/proof/group_name_collision_semantics.bend` (`!` → agree). This is the 2-line patch: C2 gains
  it, and C1 does not.

The confirmation reruns covered 5 cases × 2 extra runs × 4 arms. Two cases are flaky near the 30 s cap
(f1193 and app_slash_boss_3d). Neither flips a PASS.

### Packaged paths

- **`bend_verify` (in-process discovery and dispatch, as in scripts/smoke.py).** 1611 cases per arm, plus
  2 × 15 special repetitions. On every case the plugin ran, its verdict equals direct agree (0
  disagreements over 4 × 1580 cases).
  - 31 cases are `layout_na`: the case's directory already holds LAWS.bend or PROOF.bend, as in the
    demos.
  - 13 cases are plugin input-policy errors (9 `unsupported_import` for .c/.txt/absolute imports, 4
    `input_capture_failed`). Direct also rejects every one of them, so the policy never costs a valid PASS.
- **`hermes bend verify` (installed plugin, isolated template profile).** 30 runs: 15 cases × {official,
  patched}, one Hermes process each. All 30 verdicts match direct and plugin (official 4 pass / 11 fail;
  patched 10 pass / 5 fail). Session-bootstrap kernels are a7e5203d… and 72e11a86…. When Bend rejects a
  file before reaching the kernel, the receipt reports `session-uninitialized`.

## Findings and notes for the owner

1. **Candidate B is promotable on the 389 semantic gate**, with no corpus regression. The choice of how to
   promote it stays with the owner. B carries 6 unreleased upstream commits, and its kernel source
   differs from stock (bendlang/bend PR 1192). It reports `bend 2.0.34`, so only hashes identify it.
2. **C2, the minimal alternative, also passes every preregistered condition** (informational; preregistered
   as a control, not a candidate). It is v2.0.34 plus the two safe.ts lines and keeps the official kernel
   (a7e5203d…). Like stock, it fails closed on the 3 newer tests that only the upstream commits in C1/B
   fix. The branch is local, at 6e940a0b; Publish may push it to kvnloo/bend as a new branch.
3. **Upstream has its own fix.** bendlang/bend issue 1212 was closed on 2026-10-02 by bendlang/bend
   PR 1215 (commit e1ed2435, 115+/46− in safe.ts). That fix differs from the 2-line patch and is
   unreleased; v2.0.34 is still the latest release. This lane ran only the upstream fix's regression test,
   on all arms; B and C2 pass it. The next upstream release will need this same gate.
4. **The plugin README's limitation note still applies to stock.** Its 2.0.32–2.0.34 note is correct.
   With stock Bend, a plugin PASS can certify a different value than the source (finding above). No plugin
   bug was found; plugin behaviour was not changed.
5. **Open false negatives** (fail-closed, open upstream): bendlang/bend issues 1179, 1193, 1194 on every
   arm.

## Evidence rows

| row | label | detail |
| --- | --- | --- |
| direct sweeps, 4 arms × 1611 cases | REAL | raw/direct-*.jsonl.gz |
| plugin sweeps, 4 arms × 1611 cases | REAL | raw/plugin-*.jsonl.gz |
| S/F repetitions r2, r3 (direct + plugin) | REAL | raw/*-special-r*.jsonl.gz |
| confirmation reruns (5 cases × 2 × 4 arms) | REAL | raw/direct-*-confirm-r*.jsonl.gz |
| `hermes bend verify` CLI, 30 runs | REAL | raw/cli/ |
| upstream fix and PR-layout analysis | SOURCE | issue texts and diffs read with gh |
| stock 2.0.32 / 2.0.33 | NOT_RUN | settled in kvnloo/hermes-agent#389 |
| upstream main after 01875127 incl. the 1212 fix | NOT_RUN | out of scope; only its test file is borrowed |
| latency | NOT_RUN | no BENCHMARK rows |

Denominators. There are 66 launcher runs: 2 pilots (excluded) and 64 measured, of which 48 exit 0 and
16 exit 1. All 16 exit-1 runs are CLI non-pass verdicts. There are 12,888 primary records (direct and plugin, 4 arms × 1611),
plus 240 repetition records and 40 confirmation records. Every failure and timeout is counted. Isolation held in
all 66 runs: empty masks for the live Hermes home, the real home, /run/user and X11; the live Hermes files
were unchanged (only the home directory's own mtime is excluded); and every run used Hermes worktree head
ad31bbf0.

## Deviations from PREREG (operational; metric definitions unchanged)

- The first two blocks (direct-A, plugin-B) ran under an earlier block loop, as two independent
  shared-lock loops with no yield to waiting exclusive (quiet-timed) holders. I stopped the loops after
  those blocks and added their two ledger lines after the fact. Every later block yields up to 5 min (at
  first 15 min) to exclusive waiters, then takes one shared acquisition for all of its jobs.
- Block length went from 270 s to 510 s, phase 4 was merged into phase 3, and the phase-1 loop was ended
  while idle. None of this touched any record.
- analyze.py, confirm_list.py, cli_phase.sh and package.py were written after the PREREG commit. They
  implement its procedures. The plugin pass rate excludes layout_na from its denominator ("pass / N_ran"),
  and the policy errors are split by direct class.

## Reproduce / verify

```sh
python3 verify_artifacts.py            # recomputes summary.json from raw/, checks PREREG order, identities, counts, paths
python3 analyze.py raw --out /dev/stdout
```

Harness: `harness/stage.sh` (corpus copies, case lists, plugin export, CLI fixtures), `run_direct.py`,
`run_plugin.py`, `run_cli.sh` / `cli_phase.sh`, `blocks.sh` (shared-lock blocks), `confirm_list.py`,
`package.py` (scrubs local paths into tokens). Every execution went through the lane launcher:
hostless, then bwrap masks, `env -i`, and a dead proxy. Unscrubbed raw records and launcher metadata are
mirrored outside git in the lane's artifact directory.
