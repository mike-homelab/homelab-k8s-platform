
import requests
import json

LITELLM_BASE   = "https://llm.michaelhomelab.work/v1"
LITELLM_KEY    = "sk-michael-homelab-llm-proxy"
MODEL_REASON   = "reasoning"

def llm_call(model: str, system: str, user: str):
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, verify=False)
    resp.raise_for_status()
    return resp.json()

system = "You are a helpful assistant."
user = "Hello, tell me a short joke."
response = llm_call(MODEL_REASON, system, user)
print(json.dumps(response, indent=2))
