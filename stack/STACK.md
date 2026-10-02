# Composition and ownership

The RFC north star is the verified control loop: evidence → state → bounded
DecisionOpportunity → legal action → execution → independently verified outcome
→ credit → cheaper mechanism → deoptimization when assumptions drift.
These stages are not interchangeable. This package composes downstream code;
a proof PASS does not collapse the whole loop into a success or authority grant.

| Piece | Package integration | Owner and qualification |
| --- | --- | --- |
| Hermes evidence | Bundled observer; native profile spool | Metadata only; no context-engine or prompt mutation |
| API-attempt evaluation | Bundled joins, scorer, evaluator; native CLI | Existing DecisionBackend scores explicitly; labels concern API attempts |
| Source-backed context and State Packet | Bundled reducers; isolated child | Git/docs revisions; no ambient personal memory or network retrieval |
| DecisionOpportunity | Bundled builder; CLI and opt-in shadow projection | Read authority by default; no autonomous execution or widened grants |
| Bend proof evidence | Native tool/kernel/receipt/replay; turn trace reference | Emitted-book scope; source semantics not attested |
| Typed functions, Laya/Jev, model backends | Existing local z0 service and explicit offline backend CLI | Existing models, configuration, credentials and evidenced admission |
| Worker routing/execution | Existing z0 HTTP contracts | Canonical runtime owns selection, consent and execution; no plugin retries |
| Dispatch authority, quota, economics, receipts | Existing z0 host service | One authority and ledger; no second receipt protocol or scheduler |
| AODL/Bend gate | Existing proof project verified by native Bend | Python admission remains canonical; parity is not hot-path promotion |
| SoL-Pi/RLM and LCM context mechanisms | Configured companion components | One active context engine; no copied private-method overrides |
| OptMem, semantic memory, EvolutionLab, Kerdoios | Existing RFC/companion ownership | No invented global belief writes, automatic promotion or routing ladder |
| Zer0 repository map | Published component/dependency inventory | Architecture registry rather than runtime |

`sources.json` records unchanged file digests, donor commits and the tested live
service revision. The current service owns its manifests and ledger; we do not
replace it with the stale future-stack branch. Model environments remain separate
from Hermes. Copyright notices remain in `LICENSES/`.

RFC context: Hermes #324 (Bend boundary), #388 (private kernel), #389 (compiler
qualification), #390 (snapshot/replay); z0 #15 (verified control loop);
z0intelligence #48 (AODL parity), #56/#59 (mechanism-neutral choice), #63/#66
(memory ownership). Installed, invoked, causally beneficial and independently
replicated are separate claims. This release establishes integration and scoped
measurement seams, not task-quality qualification of the whole research program.
