import json
import urllib.request
import ssl
import time

def test_context(url, model, name):
    print(f"Testing {name} ({url}) with large context...")
    
    # Generate ~100k tokens (roughly 4 chars per token = 400k chars)
    # Using a simple repeated sentence
    sentence = "The quick brown fox jumps over the lazy dog. "
    large_text = sentence * 10000 # ~10k sentences, ~100k words
    
    data = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a helpful assistant. I will provide a very long text. Please summarize it in one sentence."},
            {"role": "user", "content": f"Text: {large_text}\n\nSummarize the above text."},
        ],
        "options": {
            "num_ctx": 128000
        }
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    start_time = time.time()
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=600) as response:
            result = json.loads(response.read().decode('utf-8'))
            duration = time.time() - start_time
            print(f"Success! Response received in {duration:.2f}s")
            print(f"Response: {result['choices'][0]['message']['content']}")
            print(f"Usage: {json.dumps(result.get('usage', {}), indent=2)}")
    except Exception as e:
        print(f"Error testing {name}: {e}")

if __name__ == "__main__":
    # Test Coder
    test_context("https://llm.michaelhomelab.work/coder/v1/chat/completions", "qwen3:4b", "Coder")
    print("-" * 40)
    # Test Reasoning
    test_context("https://llm.michaelhomelab.work/reasoning/v1/chat/completions", "gemma4:e4b", "Reasoning")
