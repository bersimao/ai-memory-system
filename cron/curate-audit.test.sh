#!/usr/bin/env bash
# Legacy entry point; invariants now live with the shared core.
set -euo pipefail
exec python3 "$(dirname "$0")/../scripts/memory_core.test.py"
