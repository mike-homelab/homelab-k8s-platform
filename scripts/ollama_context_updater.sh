#!/usr/bin/env bash
# raphael_context_updater.sh
# This script runs inside a Kubernetes CronJob.
# It checks the current OLLAMA_NUM_CTX in the vllm-coder and vllm-reasoning manifests,
# increments by 8000 up to a final target of 128000, and commits/pushes changes.

set -euo pipefail

REPO_ROOT="/workspace"
CODER_YAML="$REPO_ROOT/clusters/homelab/apps/ai-platform/vllm/coder.yaml"
REASONING_YAML="$REPO_ROOT/clusters/homelab/apps/ai-platform/vllm/reasoning.yaml"
TARGET=128000
INCREMENT=8000

# Function to extract current value
extract_ctx() {
  local file=$1
  grep -Po '(?<=value: ")([0-9]+)(?=")' "$file" | head -n1
}

# Function to update yaml with new value
update_ctx() {
  local file=$1
  local new=$2
  # Use sed to replace the first occurrence of OLLAMA_NUM_CTX value
  sed -i -E "s/(OLLAMA_NUM_CTX\s*:\s*value:\s*\")[0-9]+(\")/\1${new}\2/" "$file"
}

# Get current values
current_coder=$(extract_ctx "$CODER_YAML")
current_reasoning=$(extract_ctx "$REASONING_YAML")

# Determine next values (increment only if below target)
next_coder=$current_coder
next_reasoning=$current_reasoning

if (( current_coder < TARGET )); then
  next_coder=$(( current_coder + INCREMENT ))
  if (( next_coder > TARGET )); then
    next_coder=$TARGET
  fi
fi

if (( current_reasoning < TARGET )); then
  next_reasoning=$(( current_reasoning + INCREMENT ))
  if (( next_reasoning > TARGET )); then
    next_reasoning=$TARGET
  fi
fi

# Apply updates only if values changed
if (( next_coder != current_coder )); then
  echo "Updating coder OLLAMA_NUM_CTX: $current_coder -> $next_coder"
  update_ctx "$CODER_YAML" "$next_coder"
fi
if (( next_reasoning != current_reasoning )); then
  echo "Updating reasoning OLLAMA_NUM_CTX: $current_reasoning -> $next_reasoning"
  update_ctx "$REASONING_YAML" "$next_reasoning"
fi

# If no changes, exit gracefully
if (( next_coder == current_coder && next_reasoning == current_reasoning )); then
  echo "Both manifests already at target $TARGET. No changes needed."
  exit 0
fi

# Commit and push changes
cd "$REPO_ROOT"
git add "$CODER_YAML" "$REASONING_YAML"
git commit -m "chore: increment OLLAMA_NUM_CTX to ${next_coder}/${next_reasoning}"
# Push without interactive prompt
env GIT_TERMINAL_PROMPT=0 git push origin main

echo "Context increment applied and pushed."
