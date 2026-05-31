# Wave 3 Goal: Developer Setup Hardening

## Goal

Make local development and worker-thread setup repeatable so future waves do
not rediscover dependency or generated-file problems.

## Target Files

Own these files:

- `Makefile`
- `scripts/dev_setup.sh`
- `scripts/check.sh`
- `README.md`
- `docs/control.md`
- `tests/test_developer_setup.py`

Avoid editing kernel implementation modules unless a test exposes a clear
packaging issue.

## Inputs To Read

- `README.md`
- `docs/control.md`
- `pyproject.toml`
- `.gitignore`

## Acceptance Criteria

- Add a one-command setup path that creates `.venv` and installs `.[dev]`.
- Add a one-command check path that runs bare `python3` unittest and venv
  pytest when `.venv` exists.
- Document why the project keeps both bare-stdlib resilience and venv-backed
  dependency verification.
- Ensure generated files such as `__pycache__`, `.pytest_cache`, and egg-info
  remain ignored.
- Include tests that inspect the setup/check scripts for safe shell flags and
  expected commands without executing installs.

## Verification

Run:

```bash
python3 -m unittest discover -s tests
.venv/bin/python -m pytest
```

Commit with:

```bash
git commit -m "Harden developer setup checks"
```

