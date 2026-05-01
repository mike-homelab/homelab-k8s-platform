
import requests
import json

LITELLM_BASE   = "https://llm.michaelhomelab.work/v1"
LITELLM_KEY    = "sk-michael-homelab-llm-proxy"
MODEL_REASON   = "reasoning"

def test_stream():
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_REASON,
        "stream": True,
        "messages": [
            {"role": "system",  "content": "You are a helpful assistant."},
            {"role": "user",    "content": "Hello, write a very short 2-sentence story."},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, verify=False, stream=True)
    resp.raise_for_status()
    
    for line in resp.iter_lines():
        if line:
            print(f"RAW: {line.decode('utf-8')}")

if __name__ == "__main__":
    test_stream()
