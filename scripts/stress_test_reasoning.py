#!/usr/bin/env python3
import os
import time
import subprocess
import requests
import re
import json

YAML_FILE = "/home/michael/homelab/homelab-k8s-platform/clusters/homelab/apps/ai-platform/vllm/reasoning.yaml"
KUSTOMIZE_DIR = "/home/michael/homelab/homelab-k8s-platform/clusters/homelab/apps/ai-platform/vllm"
URL = "https://llm.michaelhomelab.work/reasoning/api/generate"
MODEL = "gemma4:e4b"

def update_yaml(ctx):
    with open(YAML_FILE, 'r') as f:
        content = f.read()
    
    content = re.sub(r'name: OLLAMA_NUM_CTX\n\s+value: "\d+"', f'name: OLLAMA_NUM_CTX\n              value: "{ctx}"', content)
    
    with open(YAML_FILE, 'w') as f:
        f.write(content)
    print(f"Updated YAML to CTX={ctx}")

def apply_kustomize():
    print("Applying changes via kubectl...")
    subprocess.run(["kubectl", "apply", "-k", KUSTOMIZE_DIR], check=True)

def wait_for_pod():
    print("Waiting for vllm-reasoning-0 to be Ready...")
    # Wait for the old pod to terminate and new one to be ready
    time.sleep(10)
    subprocess.run(["kubectl", "wait", "--for=condition=Ready", "pod/vllm-reasoning-0", "-n", "ai-platform", "--timeout=300s"], check=True)
    # Give ollama a few seconds to initialize
    time.sleep(10)

def test_inference(ctx):
    # Create a dummy prompt roughly equivalent to the context length
    # 1 token is roughly 4 characters
    prompt_length = ctx * 3 
    prompt = "A" * prompt_length
    
    print(f"Sending test request to {URL} with prompt length {prompt_length} characters...")
    payload = {
        "model": MODEL,
        "prompt": f"Summarize this: {prompt}",
        "stream": False
    }
    
    try:
        response = requests.post(URL, json=payload, verify=False, timeout=300)
        if response.status_code == 200:
            print(f"Success at CTX={ctx}")
            return True
        else:
            print(f"Failed with status code: {response.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Request failed: {e}")
        return False

def check_oom():
    # Check if the pod restarted due to OOM
    result = subprocess.run(["kubectl", "get", "pod", "vllm-reasoning-0", "-n", "ai-platform", "-o", "json"], capture_output=True, text=True)
    if result.returncode == 0:
        data = json.loads(result.stdout)
        restarts = data.get('status', {}).get('containerStatuses', [{}])[0].get('restartCount', 0)
        state = data.get('status', {}).get('containerStatuses', [{}])[0].get('state', {})
        if 'waiting' in state and state['waiting'].get('reason') == 'CrashLoopBackOff':
            return True
        if restarts > 0:
            print(f"Pod restarted {restarts} times. Likely OOM.")
            return True
    return False

def main():
    ctx = 28288
    increment = 8000
    
    # We already know 28288 works or is starting point, move to next
    ctx += increment
    
    while True:
        print(f"\n--- Testing CTX={ctx} ---")
        update_yaml(ctx)
        apply_kustomize()
        try:
            wait_for_pod()
        except subprocess.CalledProcessError:
            print("Pod failed to become Ready. Likely OOM during initialization.")
            break
            
        success = test_inference(ctx)
        
        if not success or check_oom():
            print(f"Model failed at CTX={ctx}. Max stable CTX is likely {ctx - increment}.")
            break
            
        ctx += increment

if __name__ == "__main__":
    # Disable insecure request warnings
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
