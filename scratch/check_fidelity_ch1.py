import novel_cleaner_agent
import scratch.fidelity_check as fc
import os

def check_ch1():
    raw_path = "/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files/10_vol._1_chapter_-_Demon_Lord&#x27;s_Domain,_Infiltration.pdf"
    clean_path = "/home/michael/Documents/wrong_way_to_use_healing_magic/cleaned_files/Volume_10_Chapters_1_10.md"
    
    raw_text = novel_cleaner_agent.get_pdf_text(raw_path)
    with open(clean_path, "r") as f:
        clean_content = f.read()
    
    # Extract Chapter 1 from the cleaned file
    ch1_match = re.search(r'## Chapter 1\n\n(.*?)(?:\n\n## Chapter 2|\n\n---|$)', clean_content, re.DOTALL)
    
    if ch1_match:
        cleaned_ch1 = ch1_match.group(1).strip()
        if not cleaned_ch1:
            print("Error: Chapter 1 content is empty.")
            return
        print(f"Cleaned Chapter 1 length: {len(cleaned_ch1)} characters")
        print("Performing Fidelity Check on Chapter 1...")
        result = fc.fidelity_check(raw_text, cleaned_ch1)
        print("\n--- FIDELITY REPORT ---")
        print(result)
    else:
        print("Error: Could not find Chapter 1 in cleaned file.")

import re
if __name__ == "__main__":
    check_ch1()
