import json
import urllib.request
import ssl
import time

def test_coder_limit(tokens):
    url = "https://llm.michaelhomelab.work/coder/api/chat"
    print(f"Testing Coder with {tokens}k tokens...")
    
    sentence = "The quick brown fox jumps over the lazy dog. "
    large_text = sentence * (tokens * 100) # Roughly 10 tokens per sentence
    
    data = {
        "model": "qwen3:4b",
        "messages": [
            {"role": "user", "content": f"Text: {large_text}\n\nSummarize."},
        ],
        "stream": False
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context, timeout=300) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(f"Success! Prompt tokens: {result.get('prompt_eval_count')}")
            return True
    except Exception as e:
        print(f"Failed: {e}")
        return False

if __name__ == "__main__":
    for t in [2, 4, 8]:
        if not test_coder_limit(t):
            break
