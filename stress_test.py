import requests
import time
import json

URL = "http://llm.michaelhomelab.work/v1/chat/completions"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-michael-homelab-llm-proxy"
}

def run_test(model_name, token_count):
    print(f"Testing {model_name} | Context: {token_count} tokens...")
    
    # Approx 4 characters per token for a realistic stress test
    # Generating a repetitive but token-dense string
    content = "Context block for stress testing. " * (token_count // 6)
    
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a performance benchmark assistant."},
            {"role": "user", "content": content},
            {"role": "user", "content": "Verify you received the stress test context by stating the word 'READY' and nothing else."}
        ],
        "max_tokens": 10,
        "temperature": 0
    }
    
    start_time = time.time()
    try:
        # 1800s timeout (30 mins) to allow for massive prefill at 80k
        response = requests.post(URL, headers=HEADERS, json=payload, timeout=1800)
        duration = time.time() - start_time
        
        if response.status_code == 200:
            res_data = response.json()
            # print(f"  Response: {res_data['choices'][0]['message']['content'].strip()}")
            return duration, res_data.get('usage', {}).get('prompt_tokens', 0)
        else:
            print(f"  Error {response.status_code}: {response.text}")
            return None, 0
    except Exception as e:
        print(f"  Exception: {e}")
        return None, 0

if __name__ == "__main__":
    test_rounds = range(10000, 80001, 10000)
    models = ["analyst", "builder"]
    
    results = []

    print("====================================================")
    print("STRESS TEST: 10K -> 80K (Phi-4 & Qwen2.5-Coder)")
    print("====================================================")

    for tokens in test_rounds:
        for model in models:
            duration, actual_tokens = run_test(model, tokens)
            if duration:
                print(f"  SUCCESS: {duration:.2f}s (Actual tokens: {actual_tokens})")
                results.append({
                    "Target Tokens": tokens,
                    "Model": model,
                    "Duration (s)": round(duration, 2),
                    "Actual Tokens": actual_tokens,
                    "Status": "PASS"
                })
            else:
                results.append({
                    "Target Tokens": tokens,
                    "Model": model,
                    "Duration (s)": "-",
                    "Actual Tokens": "-",
                    "Status": "FAIL"
                })
        print("-" * 30)

    # Summary Table
    print("\nFINAL RESULTS SUMMARY")
    print(f"{'Target':<10} | {'Model':<10} | {'Time (s)':<10} | {'Status':<10}")
    print("-" * 50)
    for res in results:
        print(f"{res['Target Tokens']:<10} | {res['Model']:<10} | {res['Duration (s)']:<10} | {res['Status']:<10}")
