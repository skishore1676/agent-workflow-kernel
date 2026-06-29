# Dependency policy

The Agent Workflow Kernel is the canonical workflow-law package. Hosts consume
it as `agent-workflow-kernel`; they must not vendor or copy kernel code.

## Canonical package

`pyproject.toml` declares the package and version. The public API is documented
in `docs/public-api.md` and guarded by tests.

Consumers should declare:

```text
agent-workflow-kernel>=0.3.0
```

## Consumer rule

Hosts such as Lane Host and Lathi use editable installs during development:

```bash
python -m pip install -e /Users/suman/code/agent-workflow-kernel
```

If a host needs new workflow law, add it here, test it here, then update the
consumer dependency. Do not add a local kernel copy to the host.

## Verification

The package-level check is:

```bash
make check
```

Consumer checks should prove import and adapter behavior from the consuming
venv, not from global Python.

