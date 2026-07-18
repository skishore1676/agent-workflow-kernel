# Canonical Workflow Control Finding Ledger

Status date: 2026-07-18

This ledger prevents a green regression suite from hiding the concrete defects
found during the independent architecture review. A row closes only when its
required source and installed-artifact evidence passes. “Design resolved” means
the target contract is decided; it does not mean the code is fixed.

| ID | Severity | Reproduced finding | Design decision | Implementation evidence | Status |
|---|---|---|---|---|---|
| CWC-01 | Critical | A stage completed after its lease expired. | Authoritative mutations atomically validate status, owner, token, and expiry; late external results are non-authoritative reconciliation evidence. | Stale, foreign-owner, wrong-status, swept, boundary-time, and late-result tests. | Design resolved; implementation open |
| CWC-02 | Critical | A Bus decision used gate/action/fingerprint values substituted from the current query, while the fingerprint omitted reviewed artifact and definition identity. | Publish and collect one versioned authority envelope bound to workflow/run identity, definition, immutable artifact hashes, choices, action, state, expiry, and provenance. | Real AWK-Lathi-Bus round trip plus artifact, query, state, expiry, replay, ambiguity, and self-approval negative tests. | Design resolved; implementation open |
| CWC-03 | Critical | Future and malformed SQLite databases were mutated before rejection. | Support frozen legacy version 0 and canonical version 1 only; validate shape before an atomic 0 to 1 migration; reject unknown shapes without mutation; live rollback restores a hashed backup. | State-rich migration fixtures, future/malformed no-mutation, second-open idempotency, integrity/semantic comparison, and backup restore. | Design resolved; implementation open |
| CWC-04 | High | A corrected Bus decision retained an already-consumed packet ID, and ACK of an unknown packet succeeded. | Packet revisions are content/version addressed; correction creates new pending work; ACK requires an existing harvested packet. | Correction-after-ACK, ACK-before-harvest, duplicate harvest, and per-consumer pickup tests. | Design resolved; implementation open |
| CWC-05 | High | AWK source tests passed while X Digest was missing from its wheel; checkout-only source pins did not prove dependency-resolving wheels. | Build a hashed candidate wheelhouse from exact candidate SHAs and test clean archive installs with dependencies outside checkouts. Enforce exact wheel package allowlists. | Empty-environment install, empty `PYTHONPATH`, `pip check`, metadata/origin readback, adapter imports, and representative execution. | Design resolved; implementation open |
| CWC-06 | High | Lathi, Decision Lanes, and AWK adapters imported private submodules; version/provenance surfaces disagreed. | Consumers and official adapters use the top-level public API; local conveniences remain local; one release manifest binds versions, SHAs, locks, wheels, and installed provenance. | AST boundary lint, zero old import identities, aligned metadata/docs/runtime provenance, and candidate manifest. | Design resolved; implementation open |

## Review receipts

- Initial evaluator: Sol ultra, verdict **NO-GO**.
- Baseline suites at the time of review: AWK 273 passed plus 158 subtests;
  Lathi 382 passed; Lathi Bus 171 passed; Decision Lanes 92 passed.
- The evaluator performed read-only focused reproductions. It did not contact or
  mutate oldmac or live surfaces.
- Amended-design verdict: **GO for implementation fanout - design only**; no
  remaining design blockers. Implementation and runtime evidence remain open.
- Final implementation verdict: pending.

## Closure rule

Each row must end as **Proven**, **Accepted Risk** with an explicit owner and
rationale, or **Blocked** with an exact external dependency. Critical or high
Accepted Risks prevent declaring this Super Goal complete unless Suman expressly
accepts them.
