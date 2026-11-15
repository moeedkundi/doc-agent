#!/usr/bin/env bash
# Hourly: delete sessions older than their 24 h lifetime, with their files and checkpoints.
set -euo pipefail
cd "$(dirname "$0")/.."
echo -n "$(date -u +%FT%TZ) "
deploy/dc exec -T api python -m docagent_api.cleanup
