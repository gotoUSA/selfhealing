#!/bin/bash
# Full Test Suite Runner

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "🔥 Running Full Test Suite..."

python runners/run_stage.py --profile full "$@"
