import os
import glob
import subprocess
import json
import urllib.request
import ssl
import re

API_URL = "https://llm.michaelhomelab.work/reasoning/v1/chat/completions"
MODEL = "gemma4:e4b"
DOC_DIR = "/home/michael/Documents/wrong_way_to_use_healing_magic/"
OBSIDIAN_TERM_FILE = "/home/michael/obsidian/Library/Novels/The_Wrong_Way_to_Use_Healing_Magic/Terminology.md"

def get_text_from_pdf(pdf_path):
    try:
        result = subprocess.run(["pdftotext", pdf_path, "-"], capture_output=True, text=True, check=True)
        return result.stdout
    except Exception as e:
        print(f"Error reading {pdf_path}: {e}")
        return ""

def call_llm(text_chunk):
    system_prompt = "You are a terminology extraction agent. Extract character names, locations, and unique magic terms from the novel text. Format as a markdown list."
    data = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Extract terms from this text:\n\n{text_chunk[:15000]}"}
        ],
        "temperature": 0.1
    }
    req = urllib.request.Request(API_URL, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        context = ssl._create_unverified_context()
        with urllib.request.urlopen(req, context=context) as response:
            return json.loads(response.read().decode('utf-8'))["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"LLM Error: {e}")
        return ""

def main():
    reference_pdfs = sorted(glob.glob(os.path.join(DOC_DIR, "*.pdf")))[:5]
    print(f"Found {len(reference_pdfs)} reference volumes.")
    
    all_extracted_content = []
    for pdf in reference_pdfs:
        print(f"Processing {os.path.basename(pdf)}...")
        text = get_text_from_pdf(pdf)
        if text:
            extracted = call_llm(text)
            # Remove thinking tags
            extracted = re.sub(r'<think>.*?</think>', '', extracted, flags=re.DOTALL).strip()
            all_extracted_content.append(f"### From {os.path.basename(pdf)}\n{extracted}")

    if all_extracted_content:
        os.makedirs(os.path.dirname(OBSIDIAN_TERM_FILE), exist_ok=True)
        with open(OBSIDIAN_TERM_FILE, "a") as f:
            f.write("\n\n## Auto-Extracted Terminology (Vols 1-5)\n")
            f.write("\n\n".join(all_extracted_content))
            f.write("\n")
        print(f"Updated {OBSIDIAN_TERM_FILE}")

if __name__ == "__main__":
    main()
