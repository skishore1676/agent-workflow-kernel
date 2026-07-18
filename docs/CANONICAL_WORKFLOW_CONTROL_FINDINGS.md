# Canonical Workflow Control Finding Ledger

Status date: 2026-07-18

This ledger prevents a green regression suite from hiding the concrete defects
found during the independent architecture review. A row closes only when its
required source and installed-artifact evidence passes. “Design resolved” means
the target contract is decided; it does not mean the code is fixed.

| ID | Severity | Reproduced finding | Design decision | Implementation evidence | Status |
|---|---|---|---|---|---|
| CWC-01 | Critical | A stage completed after its lease expired. | Authoritative mutations atomically validate status, owner, token, and expiry; late external results are non-authoritative reconciliation evidence. | AWK 0.4 stale, explicit and actor-defaulted foreign-owner, wrong-status, swept, boundary-time, and late-result tests pass in the 285-test suite. | Proven |
| CWC-02 | Critical | A Bus decision used gate/action/fingerprint values substituted from the current query, while the fingerprint omitted reviewed artifact and definition identity. | Publish and collect one versioned authority envelope bound to workflow/run identity, definition, immutable artifact hashes, choices, action, state, expiry, and provenance. | The real AWK to Lathi to Bus to Lathi to AWK test passes; Lathi compares the complete collected envelope with AWK's durable completed publish envelope; query/body/hash/expiry/provenance/ambiguity tampering blocks. | Proven |
| CWC-03 | Critical | Future and malformed SQLite databases were mutated before rejection. | Support frozen legacy version 0 and canonical version 1 only; validate shape before an atomic 0 to 1 migration; reject unknown shapes without mutation; live rollback restores a hashed backup. | Frozen v0 migration, future/malformed byte-hash no-mutation, second-open idempotency, integrity/semantic comparison, backup, and restore tests pass. | Proven |
| CWC-04 | High | A corrected Bus decision retained an already-consumed packet ID, and ACK of an unknown packet succeeded. | Packet revisions are content/version addressed; correction creates new pending work; ACK requires an existing harvested packet. | Bus 174-test suite includes correction-after-ACK through reconcile, ACK-before-harvest, duplicate harvest, and consumer pending behavior. | Proven |
| CWC-05 | High | AWK source tests passed while X Digest was missing from its wheel; checkout-only source pins did not prove dependency-resolving wheels. | Build a hashed candidate wheelhouse from exact candidate SHAs and test clean archive installs with dependencies outside checkouts. Enforce exact wheel package allowlists. | Clean-commit wheels include X Digest; Decision and Lathi install outside checkouts with empty `PYTHONPATH`, `pip check`, version/origin readback, and exact package checks. | Proven |
| CWC-06 | High | Lathi, Decision Lanes, and AWK adapters imported private submodules; version/provenance surfaces disagreed. | Consumers and official adapters use the top-level public API; local conveniences remain local; one release manifest binds versions, SHAs, locks, wheels, and installed provenance. | Import lint and repository scans find no private AWK imports; metadata resolves AWK 0.4.0 and Bus 0.2.0 from candidate wheels; installed Decision cases record AWK's distribution `RECORD` digest; the release manifest binds that artifact to source. | Proven |

## Review receipts

- Initial evaluator: Sol ultra, verdict **NO-GO**.
- Baseline suites at the time of review: AWK 273 passed plus 158 subtests;
  Lathi 382 passed; Lathi Bus 171 passed; Decision Lanes 92 passed.
- The evaluator performed read-only focused reproductions. It did not contact or
  mutate oldmac or live surfaces.
- Amended-design verdict: **GO for implementation fanout - design only**; no
  remaining design blockers.
- Candidate implementation evidence closes CWC-01 through CWC-06 as Proven.
  Oldmac/runtime deployment remains explicitly out of scope and unverified.
- Final implementation verdict: **GO for local source/package acceptance**.
  Sol independently reproduced and verified the fixes for complete-envelope
  comparison, installed-wheel case identity, and actor-defaulted lease-owner
  enforcement, and found no remaining critical/high software defect.

## Closure rule

Each row must end as **Proven**, **Accepted Risk** with an explicit owner and
rationale, or **Blocked** with an exact external dependency. Critical or high
Accepted Risks prevent declaring this Super Goal complete unless Suman expressly
accepts them.
