---
name: bend-workflow
description: Develop a Bend project with agreed laws, proof updates, and scoped verification receipts.
---

# Bend development in Hermes

Use this when editing a Bend project or checking its proof evidence.

1. Run `hermes bend doctor` and `bend guide` before working in unfamiliar Bend code.
   The verifier requires the compiled Bend CLI and Lean 4.34.0. Read the project's
   instructions, adjacent LAWS.bend, PROOF.bend, and relevant imports.
2. Treat the user's agreed laws as the specification. Do not weaken or remove a
   law merely to get a PASS. If requirements changed, make the proposed law change
   explicit to the user before treating it as an accepted specification.
3. Edit implementation and proof using Hermes's existing file tools. Keep
   PROOF.bend importing the adjacent LAWS.bend. Exercise the normal application
   tests as well; proof coverage is only as broad as the actual laws.
4. Call `bend_verify` with the absolute project path on the Hermes host and, if
   nested, its relative PROOF.bend path. Terminal backends may use another machine
   or filesystem; do not assume those paths are available to the plugin.
5. Fix proof errors and rerun after edits. Treat timeout, unstable, indeterminate,
   and adapter errors as no passing evidence. Hub imports require local vendoring
   until dependency snapshot/replay is supported.
6. Report the receipt's verdict, input manifest and limitations. A PASS covers
   Bend's emitted book, not source/compiler equivalence or whole-task success.
   `source_semantics_attested` is false, including on released Bend 2.0.34.

For z0intelligence's `exp/bend-aodl-gate` branch, the proof project is
`bend/aodl_gate`. This is CI/spec evidence; canonical AODL/Python admission stays
authoritative. Never turn a Bend receipt into an authorization or verified_success.
