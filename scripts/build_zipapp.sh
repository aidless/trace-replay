#!/usr/bin/env bash
# Build a self-contained trace-replay.pyz zipapp (stdlib only, no install needed).
# Usage: bash scripts/build_zipapp.sh [output.pyz]
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-trace-replay.pyz}"
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
cp -r trace_replay "$STAGING/trace_replay"
cat > "$STAGING/__main__.py" <<'PY'
from trace_replay.cli import main

raise SystemExit(main())
PY
python3 -m zipapp "$STAGING" -p "/usr/bin/env python3" -o "$OUT"
echo "built: $OUT ($(wc -c < "$OUT") bytes)"
