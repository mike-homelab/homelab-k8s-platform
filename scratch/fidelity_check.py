import novel_cleaner_agent
import json
import re

def fidelity_check(raw_text, cleaned_text):
    # Use LLM to identify key narrative beats in raw text
    beat_prompt = """Analyze the following novel segment and list all key narrative beats (events, character thoughts, dialogue exchanges) in a concise bulleted list. 
Raw segment:
{text}"""

    raw_beats = novel_cleaner_agent.call_api(novel_cleaner_agent.REASONING_API, {
        "model": novel_cleaner_agent.MODEL,
        "messages": [{"role": "user", "content": beat_prompt.format(text=raw_text[:4000])}],
        "temperature": 0
    })
    
    if not raw_beats or "choices" not in raw_beats: return "Error: Could not extract raw beats."
    
    beats_list = raw_beats["choices"][0]["message"]["content"]
    print(f"Extracted {len(beats_list)} chars of beats.")
    
    # Now verify those beats exist in the cleaned text
    verify_prompt = f"""Compare these two versions of a novel. 
BEATS FROM RAW TEXT:
{beats_list}

CLEANED TEXT SAMPLE:
{cleaned_text[:4000]}

TASK: For each beat listed, confirm if it is present in the cleaned text. 
If any beat is missing or heavily summarized (losing details), list it.
If all are present, say "FIDELITY: 100%".
"""

    print(f"Sending simplified verification prompt.")
    verification = novel_cleaner_agent.call_api(novel_cleaner_agent.REASONING_API, {
        "model": novel_cleaner_agent.MODEL,
        "messages": [{"role": "user", "content": verify_prompt}],
        "temperature": 0
    })
    
    if not verification or "choices" not in verification: return "Error: Verification failed."
    return verification["choices"][0]["message"]["content"]

if __name__ == "__main__":
    # This will be called by the agent after processing
    pass
