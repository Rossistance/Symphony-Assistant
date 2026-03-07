#!/usr/bin/env bash
set -euo pipefail

# One-shot local setup + run for Symphony simulator UI.
# Usage:
#   bash scripts/run_local_simulator.sh
# Optional:
#   GROQ_API_KEY=... PORT=8000 bash scripts/run_local_simulator.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV_DIR="${ROOT_DIR}/.venv"
PORT="${PORT:-8000}"
export PORT

cd "${ROOT_DIR}"

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

# shellcheck source=/dev/null
source "${VENV_DIR}/bin/activate"

python -m pip install -U pip
python -m pip install flask pytest

cat <<EOF

✅ Environment ready.
Run tests (optional):
  python -m pytest -q tests/test_simulator_ui.py

Starting app on http://localhost:${PORT}/simulator
Press Ctrl+C to stop.
EOF

exec python -m app.main
