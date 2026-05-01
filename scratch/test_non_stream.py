
import requests
import json

LITELLM_BASE   = "https://llm.michaelhomelab.work/v1"
LITELLM_KEY    = "sk-michael-homelab-llm-proxy"
MODEL_REASON   = "reasoning"

def test_non_stream():
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_REASON,
        "stream": False,
        "messages": [
            {"role": "system",  "content": "You are a helpful assistant."},
            {"role": "user",    "content": "Hello, write a very short 2-sentence story."},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, verify=False, timeout=600)
    resp.raise_for_status()
    print(json.dumps(resp.json(), indent=2))

if __name__ == "__main__":
    test_non_stream()
