import json
import urllib.request

def test_reranker():
    url = "https://llm.michaelhomelab.work/rerank/v1/rerank"
    data = {
        "model": "BAAI/bge-reranker-large",
        "query": "Who is the main character?",
        "documents": [
            "Ken Usato is a high school student who is a healing mage.",
            "Suzune Inukami is a hero from another world.",
            "The story takes place in the Llinger Kingdom."
        ]
    }
    
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST'
    )
    
    try:
        # Use context to ignore SSL verify if needed, but let's try standard first
        import ssl
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            result = json.loads(response.read().decode('utf-8'))
            print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_reranker()
