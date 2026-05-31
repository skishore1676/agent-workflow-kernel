#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

python3 -m unittest discover -s tests

if [[ -x .venv/bin/python ]]; then
  .venv/bin/python -m pytest
else
  echo "Skipping venv pytest: .venv/bin/python is missing. Run ./scripts/dev_setup.sh first."
fi
