# Stage Actor Lease Policy

Date: 2026-06-01

## Design

AWK now resolves stage claim leases from portable workflow policy instead of
requiring a host runner to hardcode lane names. The supported declarative shape
is intentionally small:

```yaml
defaults:
  lease:
    seconds: 300
actors:
  researcher:
    adapter: runtime.openclaw_agent
    role: researcher
    lease:
      seconds: 5400
stages:
  - id: digest
    lease:
      seconds: 1800
```

Resolution precedence is:

1. Explicit one-off runner override.
2. Stage `lease.seconds`.
3. First referenced actor's `lease.seconds`.
4. Workflow `defaults.lease.seconds`.
5. `KernelRuntimeConfig.default_lease_seconds`.

The resolved lease is stored on `stage_runs`, emitted in the `stage_claimed`
event, included in stage-run audit exports, and copied into kernel adapter
receipt runtime provenance as `runtime_provenance.lease`.

## Still Host-Specific

AWK does not decide OpenClaw lane names or runtime-specific adapter behavior.
Hosts still decide which workflow actor/stage definitions correspond to lanes
such as `or_research`, `awk_openclaw`, or `x_digest`, and hosts still own actual
adapter execution, timeout mechanics, and process supervision.

## OpenClaw Next Step

OpenClaw should stop branching on lane names for stale-claim windows once its
AWK bridge can load workflow definitions with these lease declarations. The
temporary mapping can become workflow YAML:

- default: 900 seconds
- `or_research` actor or stage: 5400 seconds
- `awk_openclaw` actor or stage: 2700 seconds
- `x_digest` actor or stage: 1800 seconds

The bridge should read the `stage_claimed` event or stage-run audit export when
reporting why a lease duration was used.
