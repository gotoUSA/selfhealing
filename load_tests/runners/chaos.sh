#!/bin/bash
# Chaos Test Runner

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

echo "🎲 Running Chaos Tests..."

export CHAOS_ENABLED=true
export CHAOS_PROBABILITY=0.10

python runners/run_stage.py --profile chaos "$@"
