import os
import re
import json
import urllib.request
import subprocess
import ssl
import time

# Endpoints
REASONING_API = "https://llm.michaelhomelab.work/coder/v1/chat/completions"
RERANK_API = "https://llm.michaelhomelab.work/rerank/v1/rerank"
MODEL = "qwen3:4b" # Using faster coder model for reliability

# Costs
COST_PER_1M_INPUT = 5.0
COST_PER_1M_OUTPUT = 15.0

# Paths
RAW_DIR = "/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files/"
CLEAN_DIR = "/home/michael/Documents/wrong_way_to_use_healing_magic/cleaned_files/"
TERM_FILE = "/home/michael/obsidian/Library/Novels/The_Wrong_Way_to_Use_Healing_Magic/Terminology.md"

total_input_tokens = 0
total_output_tokens = 0

def call_api(url, payload, timeout=120, retries=3):
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers={'Content-Type': 'application/json'}, method='POST')
    for attempt in range(retries):
        try:
            context = ssl._create_unverified_context()
            with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"API Attempt {attempt+1} Error at {url}: {e}")
            if attempt < retries - 1:
                time.sleep(5)
            else:
                return None
    return None

def get_relevant_terms(text_chunk):
    if not os.path.exists(TERM_FILE): return ""
    with open(TERM_FILE, "r") as f:
        terms = [l.strip() for l in f.readlines() if l.strip()]
    
    payload = {
        "model": "BAAI/bge-reranker-large",
        "query": text_chunk[:1000],
        "documents": terms[:100]
    }
    
    result = call_api(RERANK_API, payload)
    if result and "results" in result:
        # Get terms with decent relevance scores
        top_terms = [r["document"]["text"] for r in result["results"][:15] if r["relevance_score"] > 0.05]
        if top_terms:
            return "CHARACTER FOCUS LIST:\n" + "\n".join([f"- {t}" for t in top_terms])
    return ""

def clean_segment(segment, terms):
    global total_input_tokens, total_output_tokens
    
    system_prompt = f"""You are translating and polishing a novel. Your goal is to rewrite the raw text into professional English while maintaining 100% narrative integrity. 
Do not omit any descriptions, character thoughts, or dialogue. Do not summarize. Every sentence in the raw text must have a corresponding, polished equivalent in your output.

{terms}

STRICT INSTRUCTIONS:
1. OUTPUT ONLY THE NOVEL PROSE.
2. NO SUMMARIES, NO INTRODUCTIONS, NO TITLES.
3. PRESERVE EVERY DETAIL.
4. YOUR OUTPUT MUST BEGIN IMMEDIATELY WITH THE STORY TEXT."""
    
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Raw text to rewrite:\n\n{segment}"}
        ],
        "temperature": 0.1
    }
    
    # Reasoning models can take long, so we use a 300s timeout
    result = call_api(REASONING_API, payload, timeout=300)
    if result and "choices" in result:
        usage = result.get("usage", {})
        total_input_tokens += usage.get("prompt_tokens", 0)
        total_output_tokens += usage.get("completion_tokens", 0)
        
        content = result["choices"][0]["message"]["content"]
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
        content = re.sub(r'^(Here is the|The following is the|Cleaned prose|## Chapter).*?:\n*', '', content, flags=re.IGNORECASE)
        return content
    return ""

def get_pdf_text(path):
    try:
        res = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True, check=True)
        return res.stdout
    except: return ""

def main():
    os.makedirs(CLEAN_DIR, exist_ok=True)
    raw_files = sorted([f for f in os.listdir(RAW_DIR) if f.endswith(".pdf")])
    
    chapters = []
    for f in raw_files:
        m = re.search(r'(\d+)_vol.*?(\d+)_chapter', f, re.IGNORECASE)
        if m: chapters.append((int(m.group(1)), int(m.group(2)), f))
        else:
            m2 = re.search(r'Vol_(\d+)_Ch_(\d+)', f, re.IGNORECASE)
            if m2: chapters.append((int(m2.group(1)), int(m2.group(2)), f))
    chapters.sort()

    current_vol = -1
    out_file = None

    for vol, ch, filename in chapters:
        print(f"[{time.strftime('%H:%M:%S')}] Processing Volume {vol} Chapter {ch}...")
        if vol != current_vol:
            if out_file: out_file.close()
            current_vol = vol
            vol_path = os.path.join(CLEAN_DIR, f"Volume_{vol}.md")
            
            existing_chapters = set()
            if os.path.exists(vol_path):
                with open(vol_path, "r") as f:
                    existing_chapters = set(re.findall(r'^## Chapter (\d+)', f.read(), re.MULTILINE))
            
            out_file = open(vol_path, "a")
            if not existing_chapters:
                out_file.write(f"\n# Volume {vol}\n\n")
        
        if str(ch) in existing_chapters:
            print(f"  -> Skipping Chapter {ch} (already processed)")
            continue
            
        full_text = get_pdf_text(os.path.join(RAW_DIR, filename))
        if not full_text.strip(): continue
        
        paragraphs = full_text.split('\n')
        segments = []
        current_segment = ""
        # 4000 characters (approx 800 words) for high-fidelity transcreation
        for p in paragraphs:
            if len(current_segment) + len(p) < 4000:
                current_segment += p + "\n"
            else:
                segments.append(current_segment)
                current_segment = p + "\n"
        if current_segment: segments.append(current_segment)
        
        out_file.write(f"## Chapter {ch}\n\n")
        for i, seg in enumerate(segments):
            print(f"  -> Segment {i+1}/{len(segments)}")
            relevant_terms = get_relevant_terms(seg)
            cleaned = clean_segment(seg, relevant_terms)
            if cleaned:
                out_file.write(cleaned + "\n\n")
            out_file.flush()
        
        out_file.write("\n---\n\n")
        print(f"  -> Completed Chapter {ch}")

    if out_file: out_file.close()
    
    input_cost = (total_input_tokens / 1_000_000) * COST_PER_1M_INPUT
    output_cost = (total_output_tokens / 1_000_000) * COST_PER_1M_OUTPUT
    report = f"""# Cloud LLM Savings Report
- Total Input Tokens: {total_input_tokens:,}
- Total Output Tokens: {total_output_tokens:,}
- **Total Savings (vs GPT-4o): ${input_cost + output_cost:.2f}**
"""
    with open(os.path.join(CLEAN_DIR, "Savings_Report.md"), "w") as f:
        f.write(report)

if __name__ == "__main__":
    main()
