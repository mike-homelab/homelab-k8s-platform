import json
import urllib.request
import ssl

API_URL = "https://llm.michaelhomelab.work/reasoning/v1/chat/completions"
MODEL = "gemma4:e4b"

def analyze_style():
    with open("/tmp/style_ref.txt", "r") as f:
        style_ref = f.read()
        
    system_prompt = "You are a literary analyst. Analyze the following official translation excerpt and define its tone, vocabulary level, and sentence structure. Then, suggest a prompt that a cleaning agent can use to clean machine-translated text into this EXACT style without summarizing or losing any details."
    
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Official Style Reference:\n\n{style_ref}"}
        ],
        "temperature": 0.1
    }
    
    req = urllib.request.Request(API_URL, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode('utf-8'))["choices"][0]["message"]["content"]
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    print(analyze_style())
