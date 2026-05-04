import requests
import time
import json
import sys

BASE_URL = "https://llm.michaelhomelab.work/v1"
API_KEY = "sk-michael-homelab-llm-proxy"
MODELS = ["analyst", "builder"]
TOKEN_STAGES = [4000, 8000, 12000, 16000, 20000, 24000, 28000, 32000]

def generate_prompt(approx_tokens):
    # Roughly 4 chars per token
    char_count = approx_tokens * 4
    base_text = "This is a stress test sentence for LLM performance measurement. "
    repeat_count = char_count // len(base_text) + 1
    return (base_text * repeat_count)[:char_count]

def run_test(model, target_tokens):
    prompt = generate_prompt(target_tokens)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. Please summarize the following text briefly."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 512, # Keep output small to focus on input processing, or larger if needed
        "temperature": 0.0
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    start_time = time.time()
    try:
        response = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, timeout=300)
        end_time = time.time()
        
        if response.status_code != 200:
            return {
                "error": f"Status {response.status_code}: {response.text}",
                "target": target_tokens
            }
            
        data = response.json()
        duration = end_time - start_time
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        
        return {
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "duration": duration,
            "input_tps": input_tokens / duration if duration > 0 else 0,
            "output_tps": output_tokens / duration if duration > 0 else 0
        }
    except Exception as e:
        return {
            "error": str(e),
            "target": target_tokens
        }

def main():
    results = []
    print(f"{'Model':<10} | {'Target':<7} | {'Input':<7} | {'Output':<7} | {'Time (s)':<10} | {'In TPS':<10} | {'Out TPS':<10}")
    print("-" * 80)
    
    for model in MODELS:
        for stage in TOKEN_STAGES:
            print(f"Testing {model} with {stage} tokens...", file=sys.stderr)
            res = run_test(model, stage)
            if "error" in res:
                print(f"{model:<10} | {stage:<7} | {'ERROR':<7} | {res['error'][:40]}...")
                results.append({"model": model, "target": stage, "error": res["error"]})
            else:
                print(f"{model:<10} | {stage:<7} | {res['input_tokens']:<7} | {res['output_tokens']:<7} | {res['duration']:<10.2f} | {res['input_tps']:<10.2f} | {res['output_tps']:<10.2f}")
                results.append(res)
            time.sleep(2) # Brief pause between tests
            
    with open("stress_test_results.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()
