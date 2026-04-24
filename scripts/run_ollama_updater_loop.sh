#!/usr/bin/env bash
# run_ollama_updater_loop.sh
# Continuously invoke the updater script every 6 hours.

set -euo pipefail

UPDATER="/workspace/scripts/ollama_context_updater.sh"

while true; do
  echo "Running Ollama context updater at $(date)"
  "$UPDATER"
  echo "Sleeping for 6 hours..."
  sleep $((6 * 3600))
done
