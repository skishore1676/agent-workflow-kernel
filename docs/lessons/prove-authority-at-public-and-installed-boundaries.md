---
title: Prove authority at public and installed boundaries
type: pattern
area: workflow authority and release provenance
date: 2026-07-18
tags: [authority, leases, packaging, provenance, conformance]
refs: [docs/CANONICAL_WORKFLOW_CONTROL_FINDINGS.md, 7553798, 8d6a6a3, 0f1f343]
---

# Prove Authority at Public and Installed Boundaries

## Context

The canonical AWK migration initially passed every repository's full suite, but
the independent implementation review still reproduced three release-blocking
boundary defects.

## What We Learned

A system is not fail-closed merely because its canonical runner is safe. Every
public mutation API, editable transport boundary, and installed-artifact
provenance path must independently preserve the complete authority contract.

## Why / When It Applies

This applies whenever durable state crosses layers: runner to ledger, kernel to
human surface, or source commit to installed wheel. Convenience defaults,
selected-field comparisons, and checkout metadata are each reasonable in
isolation but can erase authority when another valid caller or install path is
used.

## Specifics

- Lease mutation must always validate status, token, expiry, and owner. If a
  public API permits an omitted explicit owner, the mutation actor becomes the
  owner identity; omission must never skip the predicate. AWK commit `7553798`
  implements this rule in `storage.py`.
- A human surface is editable transport, not authority. Lathi must compare the
  canonical collected envelope in full with the completed envelope stored in
  AWK's durable publish receipt. Comparing a selected subset lets omitted body
  or artifact hashes be rebound together. Lathi commit `8d6a6a3` closes that
  gap.
- `direct_url.json` is not an installed-wheel identity: a transitive wheel from
  `--find-links` commonly has no such file. Hash the installed distribution's
  `RECORD`, then bind that digest to the reviewed wheel hash and source commit
  in the release manifest. Decision commit `0f1f343` records this identity in
  every case.
- Clean-install probes must execute from their temporary directory. Empty
  `PYTHONPATH` alone does not remove the current checkout from Python's empty
  path entry. Decision verifier commit `331f5f1` supplies an explicit temporary
  working directory.

## Apply It Next Time

When a change claims one authority or one package identity, test the lowest
public API directly, mutate every omitted transport field together, and create
a real domain object from an installed wheel outside all checkouts. A green
canonical happy path is necessary but not sufficient.

## Dead Ends

- Comparing only fields labeled security-critical.
- Treating a valid token as sufficient lease authority.
- Assuming editable or direct-URL provenance exists in a normal wheel install.
- Clearing `PYTHONPATH` while launching the probe from a source checkout.
