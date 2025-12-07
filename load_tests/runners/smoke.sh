#!/bin/bash
# Smoke Test Runner

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "🔥 Running Smoke Test..."

python runners/run_stage.py stage0_smoke "$@"
