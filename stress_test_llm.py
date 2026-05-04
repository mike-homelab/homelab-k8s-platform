import requests
import time
import json
import sys

BASE_URL = "https://llm.michaelhomelab.work/v1"
API_KEY = "sk-michael-homelab-llm-proxy"
MODELS = ["analyst", "builder"]
# Adjusted stages to allow room for large output tokens within 32K total context
TOKEN_STAGES = [1000, 2000, 4000, 8000, 16000]

def generate_prompt(approx_tokens):
    # Roughly 4 chars per token
    char_count = approx_tokens * 4
    base_text = "This is a seed concept for expansion. "
    repeat_count = char_count // len(base_text) + 1
    return (base_text * repeat_count)[:char_count]

def run_test(model, target_tokens):
    prompt = generate_prompt(target_tokens)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a creative writer. Your task is to EXPAND the following text as much as possible. For every sentence provided, you must write a very long and detailed paragraph. Aim for an output that is at least twice as long as the input."},
            {"role": "user", "content": f"Please expand this text significantly:\n\n{prompt}"}
        ],
        "max_tokens": 16384, # Attempt large output
        "temperature": 0.7,
        "stream": True
    }
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    start_time = time.time()
    first_token_time = None
    output_tokens = 0
    
    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions", 
            headers=headers, 
            json=payload, 
            timeout=600, # Increased timeout for long generation
            stream=True
        )
        
        if response.status_code != 200:
            return {
                "error": f"Status {response.status_code}: {response.text}",
                "target": target_tokens
            }
            
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith("data: "):
                    data_str = line_str[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        content = data['choices'][0]['delta'].get('content')
                        if content:
                            if first_token_time is None:
                                first_token_time = time.time()
                            output_tokens += 1
                            # Print progress every 100 tokens to keep user informed
                            if output_tokens % 100 == 0:
                                print(f"      ... Generated {output_tokens} tokens ...", file=sys.stderr)
                    except:
                        pass
        
        end_time = time.time()
        
        ttft = (first_token_time - start_time) if first_token_time else 0
        total_duration = end_time - start_time
        generation_duration = end_time - first_token_time if first_token_time else 0
        tpot = (generation_duration / output_tokens) if output_tokens > 0 else 0
        
        return {
            "model": model,
            "input_tokens": target_tokens,
            "output_tokens": output_tokens,
            "ttft": ttft,
            "tpot": tpot,
            "total_duration": total_duration
        }
    except Exception as e:
        return {
            "error": str(e),
            "target": target_tokens
        }

def main():
    results = []
    print(f"{'Model':<10} | {'Input':<7} | {'Output':<7} | {'TTFT (s)':<10} | {'TPOT (ms)':<10} | {'Total (s)':<10}")
    print("-" * 75)
    
    for model in MODELS:
        for stage in TOKEN_STAGES:
            print(f"Testing {model} output stress with {stage} input tokens...", file=sys.stderr)
            res = run_test(model, stage)
            if "error" in res:
                print(f"{model:<10} | {stage:<7} | {'ERROR':<7} | {'-':<10} | {'-':<10} | {'-':<10}")
                results.append({"model": model, "target": stage, "error": res["error"]})
            else:
                tpot_ms = res['tpot'] * 1000
                print(f"{model:<10} | {res['input_tokens']:<7} | {res['output_tokens']:<7} | {res['ttft']:<10.2f} | {tpot_ms:<10.2f} | {res['total_duration']:<10.2f}")
                results.append(res)
            time.sleep(5) # Longer pause to allow VRAM to clear
            
    with open("output_stress_test_results.json", "w") as f:
        json.dump(results, f, indent=2)

if __name__ == "__main__":
    main()


