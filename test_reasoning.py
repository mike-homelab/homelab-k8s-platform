import json
import urllib.request
import ssl

def test_reasoning():
    url = "https://llm.michaelhomelab.work/coder/v1/chat/completions"
    data = {
        "model": "gemma4:e4b",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Say hello!"}
        ],
        "temperature": 0
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_reasoning()
