import json
import time
import urllib.request
import urllib.error
import ssl

# Create unverified SSL context for internal homelab endpoints
ssl_context = ssl._create_unverified_context()

CODER_ENDPOINT = "https://llm.michaelhomelab.work/coder/api/chat"
REASONING_ENDPOINT = "https://llm.michaelhomelab.work/reasoning/api/chat"

def test_context(endpoint, name, token_count=120000):
    print(f"\n--- Testing {name} context limit ({token_count} tokens) ---")
    
    # Approx 4 chars per token for a rough estimate
    padding_text = "token " * token_count
    prompt = f"Here is a long context: {padding_text}\n\nQuestion: What was the first word of this prompt?"
    
    payload = {
        "model": "qwen3:4b" if "coder" in name.lower() else "gemma4:e4b",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "stream": False,
        "options": {
            "num_ctx": 128000
        }
    }
    
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(endpoint, data=data)
    req.add_header('Content-Type', 'application/json')
    
    start_time = time.time()
    try:
        with urllib.request.urlopen(req, timeout=300, context=ssl_context) as response:
            elapsed = time.time() - start_time
            body = response.read().decode('utf-8')
            result = json.loads(body)
            
            actual_tokens = result.get('prompt_eval_count', 0)
            print(f"Success! Response received in {elapsed:.2f}s")
            print(f"Actual tokens processed: {actual_tokens}")
            if actual_tokens < token_count * 0.8:
                print(f"WARNING: Context was TRUNCATED. Expected ~{token_count}, got {actual_tokens}")
            else:
                print(f"CONFIRMED: Full context accepted.")
                
    except urllib.error.HTTPError as e:
        print(f"FAILED: Status {e.code}")
        print(f"Error: {e.read().decode('utf-8')}")
    except Exception as e:
        print(f"ERROR: {str(e)}")

if __name__ == "__main__":
    # Test Coder (Requested via Researcher phase)
    # Testing with 32k to see if it works at a lower limit
    test_context(CODER_ENDPOINT, "Coder (qwen3:4b)", token_count=32000)
    
    # Test Reasoning (Requested via Coder phase)
    test_context(REASONING_ENDPOINT, "Reasoning (gemma4:e4b)", token_count=80000)
