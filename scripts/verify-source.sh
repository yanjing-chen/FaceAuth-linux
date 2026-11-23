#!/usr/bin/env bash
# Only checks source-level tests/build. NEVER changes the installed system or PAM.
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
echo "[1/3] Python unit tests"
python3 -m unittest discover -s tests -q
echo "[2/3] Python syntax"
python3 - <<'PYCODE'
import pathlib, ast
for f in list(pathlib.Path('scripts').glob('*.py')) + list(pathlib.Path('tests').glob('*.py')):
    ast.parse(f.read_text(encoding='utf-8'), filename=str(f))
print('Python syntax OK')
PYCODE
if command -v cargo >/dev/null 2>&1; then
  echo "[3/3] Rust unit tests"
  cargo test --workspace
else
  echo "[3/3] cargo not installed here; Rust checks must be run on Ubuntu"
fi
echo 'Source verification completed; no system services or PAM changed.'
