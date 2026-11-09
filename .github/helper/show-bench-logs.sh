#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

cat bench_start.log || true
for file in "$GITHUB_WORKSPACE"/*.log*; do
    echo "Printing log: $file"
    cat "$file"
done
