#!/usr/bin/env bash

YAML_FILE="/home/michael/homelab/homelab-k8s-platform/clusters/homelab/apps/ai-platform/vllm/reasoning.yaml"
KUSTOMIZE_DIR="/home/michael/homelab/homelab-k8s-platform/clusters/homelab/apps/ai-platform/vllm"
URL="https://llm.michaelhomelab.work/reasoning/api/generate"
MODEL="gemma4:e4b"

CTX=44288
INCREMENT=8000

while true; do
    echo -e "\n--- Testing CTX=$CTX ---"
    
    # Update YAML
    sed -i -E "s/(OLLAMA_NUM_CTX\s*:\s*value:\s*\")[0-9]+(\")/\1${CTX}\2/" "$YAML_FILE"
    echo "Updated YAML to CTX=$CTX"
    
    # Apply changes
    echo "Applying changes via kubectl..."
    kubectl apply -k "$KUSTOMIZE_DIR"
    
    # Wait for pod
    echo "Waiting for vllm-reasoning-0 to be Ready..."
    sleep 10
    kubectl wait --for=condition=Ready pod/vllm-reasoning-0 -n ai-platform --timeout=300s
    if [ $? -ne 0 ]; then
        echo "Pod failed to become Ready. Likely OOM during initialization."
        break
    fi
    
    # Give ollama a few seconds to initialize
    sleep 10
    
    # Test inference
    PROMPT_LENGTH=$((CTX * 3))
    # Generate dummy string of length PROMPT_LENGTH and write to file
    head -c "$PROMPT_LENGTH" < /dev/zero | tr '\0' 'A' > /tmp/prompt.txt
    
    echo "Sending test request to $URL with prompt length $PROMPT_LENGTH characters..."
    
    # Use jq to create JSON payload safely from file
    jq -n --arg model "$MODEL" --rawfile prompt /tmp/prompt.txt '{model: $model, prompt: ("Summarize this: " + $prompt), stream: false}' > /tmp/payload.json
    
    RESPONSE=$(curl -s -k -X POST "$URL" -H "Content-Type: application/json" -d "@/tmp/payload.json" -w "%{http_code}" -o /dev/null)
    
    if [ "$RESPONSE" = "200" ]; then
        echo "Success at CTX=$CTX"
    else
        echo "Failed with status code: $RESPONSE"
        # Check OOM
        RESTARTS=$(kubectl get pod vllm-reasoning-0 -n ai-platform -o jsonpath='{.status.containerStatuses[0].restartCount}')
        if [ "$RESTARTS" -gt 0 ]; then
            echo "Pod restarted $RESTARTS times. Likely OOM."
        fi
        break
    fi
    
    CTX=$((CTX + INCREMENT))
done

echo "Max stable CTX is likely $((CTX - INCREMENT))."
