import requests
import time
import json

URL = "http://llm.michaelhomelab.work/v1/chat/completions"
HEADERS = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk-michael-homelab-llm-proxy"
}

def stress_test(model_name, token_count):
    print(f"--- Stress Testing {model_name} with ~{token_count} tokens ---")
    
    # Generate content (approx 4 chars per token)
    content = "This is a stress test word. " * (token_count // 5)
    
    payload = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": content},
            {"role": "user", "content": "Summarize the above stress test content in one sentence."}
        ],
        "max_tokens": 50
    }
    
    start_time = time.time()
    try:
        response = requests.post(URL, headers=HEADERS, json=payload, timeout=300)
        end_time = time.time()
        
        if response.status_code == 200:
            res_data = response.json()
            latency = end_time - start_time
            print(f"Success! Latency: {latency:.2f}s")
            print(f"Usage: {res_data.get('usage')}")
            print(f"Response: {res_data['choices'][0]['message']['content']}")
        else:
            print(f"Error {response.status_code}: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    # 30% of 65,536 for Builder (approx 19,660)
    stress_test("builder", 19000)
    print("\n")
    # 30% of 32,768 for Analyst (approx 9,830)
    stress_test("analyst", 9000)
