# CONTRACT lane: bend plugin contract and shadow safety on the integration Hermes (2026-10-02/03)

Plugin under test: kvnloo/bend-native e85e65e5 (v0.4.0), installed as a real Hermes plugin. Host: the integration
Hermes `exp/bend-stack-integration-20261002` @ ad31bbf0. Local model qwen2.5:7b-instruct 845dbda0 on the
workflow's own Ollama. No plugin file changed on this branch. Order of work: PREREG 86c605c (before any measured
run), then the analysis harness and DEVIATIONS 8b4de8f (before any measured analysis), then the results commit
93d91ce, then the review correction (DEVIATIONS D7). `python3 verify_artifacts.py` re-checks everything from the
committed files.

**Correction (D7, after review of 93d91ce).** The results commit reported H3 as PASS. That was wrong: the
preregistered H3 rule includes "`Stack.close()` returns within 2.0 s", and both H3 records say it did not
(`close_within_2s: false` in `raw/inproc/h3.json` and `h3slow.json`). H3 is now **PARTIAL**: queue bound,
accounting and non-blocking PASS; unload FAIL. Headline: H1, H2, H4, H5 and H6 pass; H3 fails its unload
criterion, so this packet does **not** show that all six contracts hold. No measurement changed; D7 also splits
pilots out of the chat-run counts and adds a plugin-free check of the bend oracle key.

Evidence labels: REAL = real Hermes + plugin + model/toolchain; FIXTURE = real plugin through Hermes's
PluginManager with synthetic payloads; SOURCE = code review; BENCHMARK = quiet-timed; EXPLORATORY = not
preregistered.

## Verdicts

| # | Contract | Evidence | Verdict |
|---|---|---|---|
| H1 | Shadow cannot change behaviour | REAL, 108 runs (36 pairs per comparison) | **PASS**: 0/36 plugin-attributable divergences; off-vs-shadow divergence equals the A/A control |
| H2 | Hooks return None; prompts, tools, provider and context engine unchanged | SOURCE + FIXTURE + REAL (108 measured + 6 footprint runs) | **PASS** |
| H3 | Bounded queue, nothing blocks, unload within 2 s | FIXTURE + BENCHMARK | **PARTIAL: unload FAIL.** Queue bound, accounting and non-blocking PASS. `Stack.close()` returned only when `join(timeout=2)` expired (2.0001 s and 2.0000 s, `close_within_2s: false` in both records); the worker stayed alive 3.0 s / 3.25 s longer and wrote 256 rows + 72 / 66 opportunity records after close returned (F2). Drops are reported only inside the dropping process (F1). `manager.unload('bend')` was not timed |
| H4 | Profile isolation A→B→A | FIXTURE (real Bend verify) | **PASS** (1 low finding in the single-manager variant) |
| H5 | No raw prompt, tool or response content in plugin data | REAL (108 measured + 6 footprint runs; 8 pilots reported apart) + FIXTURE | **PASS**, with 1 disclosure gap: State Packet stores repository text |
| H6 | Disabled plugin leaves zero footprint | REAL (6 runs) + FIXTURE | **PASS**; `stack_mode: off` is not zero-cost (EXPLORATORY) |

## Design (PREREG.json)

- Three tasks, each with an independent oracle:
  - **cua**: GTK3 fixture "I agree" checkbox in a private Xvfb session, cua-driver 0.32.0. The oracle is the fixture-owned state file.
  - **bend**: `bend_verify` on the fixture project. The oracle is the known verdict `pass`, a substring match on the final reply.
    The key was set before the runs as the fixture's known answer; D7 adds `raw/oracle_key/bend-verdict.json`, a direct run of
    official Bend 2.0.34 (`bend ./PROOF.bend --verdict`, fresh HOME, no Hermes, no plugin code) on the committed fixture:
    exit 0, `ALL PROOFS CHECK`, kernel a7e5203d built cold. So the key does not come from the plugin. It is still the same
    Bend/kernel identity the plugin uses, and a kernel verdict over the emitted book, not task success.
  - **repo**: read a file in a git fixture. The oracle is the known codename.
- Arms differ only in two plugin setting lines:
  - **off**: `stack_mode: off`, `stack_opportunities: false`.
  - **shadow**: `stack_mode: shadow`, `stack_opportunities: true`.
  - **aa**: byte-identical to off.
  - **disabled**: plugin installed but not enabled (footprint only).
- 12 pair blocks × 3 tasks × 3 arms = 108 measured runs, in seeded random order, plus 6 footprint runs. The per-block seed is shared by all arms.
- A loopback capture proxy sits in front of the model. It stores the **exact request bytes Hermes sends** and forwards
  `temperature 0` plus the block seed, because Hermes sends neither. Each run's cwd, tmp and xdg are bound at one stable path, so
  payloads are comparable byte for byte.
- Every run went through hostless and bwrap: the live Hermes home, real home, `/run/user` and the host X11 sockets were masked, `env -i` was set, and egress went to a dead proxy except loopback.

## H1: shadow does not change behaviour (REAL)

| Comparison (n=36 pairs) | identical | model-originated | Hermes-built (plugin-attributable) | tool-call names differ | outcome discordant |
|---|---|---|---|---|---|
| off vs shadow | 26 | 10 | **0** | 6 | 0 |
| off vs aa (A/A control) | 26 | 10 | **0** | 4 | 0 |
| shadow vs aa | 28 | 8 | 0 | 4 | 0 |

- **First request.** After masking only the date line and tool-call ids, the first provider request is byte-identical in
  all 36 runs of each task (one hash per task across all arms and pairs). The system prompt, tools array and
  request params are identical at **every** shared request index in every pair.
- **Where the divergences are.** Every divergence starts inside a model response to an identical request:
  - cua: 9/12 off-vs-shadow and 8/12 off-vs-aa, all at the 4th response (DONE vs one more confirm capture or different text);
  - repo: 1/12 and 2/12 at the first response (`offset` argument present or absent);
  - bend: 0/12 in both comparisons.
- **Tests.** Fisher two-sided, off-vs-shadow vs off-vs-aa: diverged 10/36 vs 10/36, p = 1.0; full tool-call sequence differs 7/36 vs 6/36, p = 1.0; names-only differs 6/36 vs 4/36, p = 0.74.
- **Attribution.** The divergences come from the model server's non-determinism at temperature 0. The stack track saw the same with prompt-cache state; the A/A rate shows it here. With n=12 per task, "no detectable difference" is not proof of equivalence.
- **Outcomes.** 108/108 runs passed (36/36 per arm). The peak prompt was 6,458 tokens, well under the 32,768 served per request.
- **EXPLORATORY (D6).** Whether Hermes's auxiliary title-generation request leaves before the one-shot CLI exits:
  present in off 32/36, aa 32/36, shadow 29/36 (Fisher shadow vs off p = 0.51; aa vs off p = 1.0). This is a race between the title thread and CLI cleanup inside Hermes, not a payload difference. The lower
  shadow count is not significant.

## H2: hooks return None, surfaces unchanged

- **Inventory** (FIXTURE, real PluginManager discovery, `raw/inproc/h2.json`). The plugin's ownership ledger holds exactly:
  - 12 hooks;
  - `tool:bend_verify`;
  - CLI `bend` and `z0`;
  - skills `bend:workflow` and `bend:z0-stack`;
  - 2 `on_unload` callbacks;
  - the host-created `tool_override_policy`.

  It registers no middleware, system-prompt section, context engine or aux task.
- **Invocations.** 26 invocations through `manager.invoke_hook` in shadow + opportunities mode, 12 normal and 14 malformed or
  hostile payloads, all returned `[]`. One hostile payload claimed `task_success` and an `execute` grant in a `bend_verify` result; nothing used it.
- **Chat runs** (REAL). Per-request component identity held, as in H1. 0 tool results carried a `pre_tool_call` block
  directive: 0 in the 108 measured runs and 0 in the 6 footprint runs (`summary.json` `denominator_split`). The 8 pilots,
  which are not in any denominator, also had 0.
- **SOURCE.** See the code-path review below.

### H2 code-path review (SOURCE)

What the plugin registers (`__init__.py register`): 12 hooks via `Stack.callback(event)` (the 11 observer
`_HOOKS` plus `post_llm_call`), the `bend_verify` tool, the `bend` and `z0` CLI commands, the `bend:workflow` and
`bend:z0-stack` skills, and two `on_unload` callbacks. No middleware, system-prompt section, context engine,
provider, auxiliary task or `transform_*` hook. The hooks are registered whatever `stack_mode` is; the mode is
read on every callback.

What a callback can return: `Stack.callback.record` has one exit path, `return None`, and wraps its whole body
in `try/except Exception`, so a failure ends in `self.error = <type>` (or `self.dropped += 1` for `queue.Full`),
never in a return value. It reads the payload and never mutates it: `observer._row` copies scalar fields and
key names; `decision_hooks.classify` and `turn_behaviour` only read `user_message` and `conversation_history`.

How Hermes uses return values at the sites these hooks feed:
- `pre_llm_call` (`agent/turn_context.py _collect_pre_llm_call_context`): a dict with `context` or a non-empty
  string is injected into the user message. `None` injects nothing.
- `pre_tool_call` (`hermes_cli/plugins_dispatch.py`): a policy hook. A `{"action": "block"}` result, a raised
  exception or a timeout (default 30 s) blocks the tool. The plugin's callback cannot raise `Exception` and only
  takes an uncontended lock, so it returns `None` in microseconds. But because the hook is registered even with
  `stack_mode: off`, every tool call of an enabled plugin passes through a fail-closed policy gate.
- The other hooks are observers whose results Hermes ignores. `subagent_stop` runs on the caller thread.
- `has_hook` gates (`pre_api_request`, `post_api_request`, `api_request_error`, `pre/post_auxiliary_call`,
  `post_tool_call`): with the plugin enabled, Hermes builds the sanitized request/response hook payloads and
  starts one bounded worker thread per callback call, even with `stack_mode: off`. That changes cost, not
  content: `_api_request_payload_for_hook` copies `api_kwargs` and never writes to it.

Conclusion: the code paths cannot change prompts, tools, the provider or the context engine. The runtime
checks are H2 in-process (inventory plus 26 invocations) and H1's per-request component identity.

## H3: bounded queue and unload (FIXTURE; timings BENCHMARK, quiet-timed `contract-inproc-h3`)

Setup: 8 threads × 400 direct callback calls (one in four is an opportunity-producing `pre_llm_call`), plus 200 calls through
`invoke_hook` while the queue is saturated.

- **Queue bound.** The queue never exceeded its 256-entry bound.
- **Accounting.** 3,400 submitted = 276 written + 3,124 dropped (balance 0).
- **Non-blocking** (BENCHMARK). Plugin callback p50 1.09 ms, p99 3.65 ms, max 6.18 ms under 8-thread contention. `invoke_hook` while saturated: p50 0.26 ms, p99 0.46 ms. This is far below Hermes's 30 s hook timeout, the timeout after which `pre_tool_call` would fail closed.
- **In-process report.** `report()` shows `rows_dropped` 3,124 and `queue_pending` 256.
- **Finding F1 (medium): drops are invisible after the process.** A fresh-process `hermes z0 report` for the same profile
  reports `rows_dropped: 0` with 276 events. Nothing about the 3,124 drops is persisted. The counter lives only on the in-memory Stack. The bundled
  original observer writes `observer_rows_dropped` marker rows, but the native bridge does not use that path. The README
  says "report exposes delivery errors", which is true only inside the process that dropped.
- **Unload: FAIL.** `Stack.close()` returned after 2.0001 s (h3) and 2.0000 s (h3slow; `close_within_2s: false` in both), so it
  misses the preregistered "returns within 2.0 s" rule. The miss is not a 62 µs rounding question. `close()` returns only because
  `join(timeout=2)` expires: it swallows `queue.Full` when it tries to enqueue its stop sentinel into the full queue
  (`stack/bridge.py` `close()`), and the daemon worker then keeps draining every accepted job (F2). Unload is not complete at 2 s.
  The earlier "in substance unload is bounded at 2 s" reading had no DEVIATIONS entry and is withdrawn (D7).
  - Scope: H3 timed `Stack.close()` directly (`inproc_contract.py`), not Hermes's unload path `manager.unload('bend')`. That
    path runs both registered `on_unload` callbacks, including the session-kernel close, and was not timed. These records
    make no claim about its duration beyond the fact that it includes `Stack.close()`.
- **Against spec item (3).** The queue is bounded and nothing blocks. But unload does not complete within 2 s, and drops are
  reported only inside the dropping process: a fresh-process `hermes z0 report` shows `rows_dropped: 0` after 3,124 drops (F1).
- **Finding F2 (low/medium): the worker keeps writing after unload.** The drain thread outlived `close()` by 3.0 s. In that time it wrote
  **256 event rows and 72 opportunity records after unload returned**, because `close()` cannot enqueue its
  stop sentinel into a full queue, and the thread then drains every accepted job, running a reducer subprocess for each opportunity.
  - The README's "accepted background projections can finish within their existing 30-second subprocess deadline" understates the bound. The real worst case is up to 256 queued jobs × 30 s each, in sequence, with plugin-data writes continuing after the plugin is unloaded.
  - EXPLORATORY `h3slow` (20,000-file repository): same picture with a 20,000-file repository: 256 rows and 66 opportunity records written after unload, worker exit 3.25 s after `close()` (the bundled reducer reads only top-level docs and git, so it stays fast); 3,400 = 275 written + 3,125 dropped, fresh-process `rows_dropped` 0 again.
- **Other behaviour.** A non-JSON `bend_verify` result makes the callback lose that row silently. It sets `last_error` but does not count the row in `rows_dropped` (H2 hostile payloads).

## H4: profile isolation A→B→A (FIXTURE, real official Bend)

- **Supported path** (`raw/inproc/h4.json`). Hermes keeps one PluginManager per profile home, and this run used it: 2 managers and 2 Stacks, with
  `hermes_cli.plugins.invoke_hook` and real `bend_verify` under each profile's override.
  - Profile A's spool holds only sA and sA2 (326 rows); profile B's holds only sB (163 rows). No foreign session or receipt id appears anywhere in the other profile's plugin data.
  - Each profile's `last_receipt` is its own. Verdicts: sA pass (session-bootstrap kernel), sB pass (session-bootstrap), sA2 pass (session-pinned).
  - A 150-row burst queued immediately before each profile switch landed in the right profile.
- **H4x: one manager serving both scopes** (robustness variant).
  - Rows still land in the profile in scope at callback time.
  - `bend_verify` is unknown under B, because tools are per profile scope.
  - **Finding F3 (low): delivery counters are shared.** `report()` under B shows `last_error: JSONDecodeError` caused in A. `rows_dropped` and `queue_pending` are per-Stack and so shared the same way. Hermes does not normally route two profiles through one manager.

## H5: no raw content in plugin data

- **REAL** (108 measured + 6 footprint runs; the 8 pilots are counted apart in `summary.json` `denominator_split`). Canaries were
  scanned in each run's plugin data (`plugin-data/bend/**` and the plugin's state dir):
  - The tool-result, model-reply, Bend-source and CUA-AX canaries (`Save note`, D1) were absent everywhere (0 violations in
    measured, footprint and pilot runs).
  - The user-prompt canary appears only in shadow-arm `opportunities.jsonl`: 24 measured runs (the 12 bend + 12 repo shadow
    runs; the CUA prompt canary is `CuaTestHarness`, also only there), as allowed by `stack_opportunities`. Pilots add 2 more.
  - The off, aa and disabled arms wrote no `plugin-data/bend/z0` at all; z0 data exists in exactly the 36 measured shadow runs.
- **FIXTURE.** With the ambient `Z0INT_HERMES_CAPTURE_SANITIZED_CONTENT=1` set, no request, response, history, args, result,
  error or system canary is persisted. Only the bounded user request appears in `opportunities.jsonl`, and only with
  opportunities on.
- **Finding F4 (low, disclosure): the State Packet persists repository text.** With `stack_opportunities` on, the bundled
  State Packet reducer stores repository document text and git metadata in `plugin-data/bend/z0/state/state_packet/<repo>/latest.json`
  and `history.jsonl`, and in each opportunity record. That includes README open-item text (canary `Ship the next release`, 12/12 measured repo shadow runs, plus 1 pilot), commit subjects, branch names and dirty-file names.
  This is not prompt, tool or response content, but the README only mentions metadata and the bounded user request.

## H6: disabled plugin footprint

- **REAL.** In 6/6 disabled-plugin runs, no path containing `bend` was created in the private home's upper layer, and there was no plugin data and no state dir. The 2 bend-task footprint runs exit 1 because the task needs the tool; that is expected and does not count toward H1.
- **FIXTURE.** Disabled: the module is not loaded, there are no ledger entries, there are 0 callbacks on all 12 events and `has_hook` is False. Enabled with `stack_mode: off`: 12 callbacks are registered, and plugin data stays empty.
- **EXPLORATORY** (BENCHMARK, quiet-timed `contract-inproc-h6cost`): `invoke_hook('pre_api_request')` dispatch p50 18.2 µs / p99 53.5 µs with the plugin disabled vs p50 161.9 µs / p99 203.8 µs enabled with `stack_mode: off` (n=3000 each). With the plugin enabled and `stack_mode: off`, Hermes's
  `has_hook` gates are open. Hermes builds sanitized hook payloads and starts one bounded worker thread per callback call
  (SOURCE). "Off" is free of content effects but not of dispatch cost.

## Setup gap found (affects every GUI lane on the setup template)

The bend-stack setup profile template cannot run `computer_use`. Hermes PM activates the profile's committed PM environment at
boot, and that environment lacks the `computer-use` extra. The first call then tries a lazy `uv sync`, and the dead proxy blocks it (pilot-05/06). This
lane used a private copy of the template provisioned once through PM's own `uv sync --locked`, which hash-checks against uv.lock
(provenance.json). The shared template was not changed.

Disclosure: that provisioning step used network inside hostless and bwrap, for PM's `uv sync --locked`, which goes beyond
the pinned Bend/Lean downloads. It ran once, before any measured run; no measured run had network. A reviewer checked that the
shared setup template still holds only the setup environment, and that the provisioned environment exists only in this lane's
private template copy.

## Files

- `PREREG.json`, `DEVIATIONS.json` (D1-D7), `plan.json`, `provenance.json`, `summary.json` (machine-readable results).
- `harness/`:
  - `proxy.py`, `drive.py`, `launch.sh`, `run_cua.sh`, `oracle.py`, `make_plan.py`;
  - `inproc_contract.py`, `export_raw.py`, `analyze.py`.
- `fixtures/`.
- `raw/runs/<id>/`: run.json, oracle, ledger, config, capture index, meta, plugin data, this run's agent-log lines.
- `raw/bodies/`: content-addressed request and response bodies, gzip, local paths replaced by placeholders.
- `raw/inproc/`: in-process records, their launcher meta, and the quiet-lane ledger lines.
- `raw/oracle_key/bend-verdict.json`: the plugin-free official Bend verdict on the bend fixture (D7).
- Pilots (`pilot-*`) are kept in `raw/runs` and excluded from every H1 denominator and from the H2-chat, H5 and H6 counts
  above; `summary.json` `H2_chat` and the `H5` totals still include them, and `denominator_split` separates them.
  pilot-01..04 ran on the shared setup template.
