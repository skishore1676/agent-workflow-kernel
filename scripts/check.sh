#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON="${PYTHON:-python3}"
if [[ -x .venv/bin/python ]]; then
  PYTHON=".venv/bin/python"
fi

"$PYTHON" -m unittest discover -s tests

if [[ "$PYTHON" == ".venv/bin/python" ]]; then
  "$PYTHON" -m pytest
else
  echo "Skipping venv pytest: .venv/bin/python is missing. Run ./scripts/dev_setup.sh first."
fi
