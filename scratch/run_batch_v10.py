import novel_cleaner_agent
import os
import time
import re

def process_v10_batch(start_ch=1, end_ch=10):
    raw_dir = "/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files/"
    clean_dir = "/home/michael/Documents/wrong_way_to_use_healing_magic/cleaned_files/"
    os.makedirs(clean_dir, exist_ok=True)
    
    files = os.listdir(raw_dir)
    v10_files = []
    for f in files:
        m = re.search(r'^10_vol\._(\d+)_chapter', f, re.IGNORECASE)
        if m:
            ch = int(m.group(1))
            if start_ch <= ch <= end_ch:
                v10_files.append((ch, f))
    
    v10_files.sort()
    
    out_path = os.path.join(clean_dir, f"Volume_10_Chapters_{start_ch}_{end_ch}.md")
    with open(out_path, "w") as out_file:
        out_file.write(f"# Volume 10 Chapters {start_ch}-{end_ch}\n\n")
        
        for ch, filename in v10_files:
            print(f"[{time.strftime('%H:%M:%S')}] Processing Chapter {ch} ({filename})...")
            raw_path = os.path.join(raw_dir, filename)
            full_text = novel_cleaner_agent.get_pdf_text(raw_path)
            
            if not full_text.strip():
                print(f"  -> Skipping Chapter {ch}: No text extracted.")
                continue
                
            paragraphs = full_text.split('\n')
            segments = []
            current_segment = ""
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
                relevant_terms = novel_cleaner_agent.get_relevant_terms(seg)
                cleaned = novel_cleaner_agent.clean_segment(seg, relevant_terms)
                if cleaned:
                    out_file.write(cleaned + "\n\n")
                out_file.flush()
            
            out_file.write("\n---\n\n")
            print(f"  -> Completed Chapter {ch}")

    # Generate small report
    input_cost = (novel_cleaner_agent.total_input_tokens / 1_000_000) * novel_cleaner_agent.COST_PER_1M_INPUT
    output_cost = (novel_cleaner_agent.total_output_tokens / 1_000_000) * novel_cleaner_agent.COST_PER_1M_OUTPUT
    print(f"\nBatch processing complete.")
    print(f"Total Input Tokens: {novel_cleaner_agent.total_input_tokens:,}")
    print(f"Total Output Tokens: {novel_cleaner_agent.total_output_tokens:,}")
    print(f"Cloud Savings: ${input_cost + output_cost:.4f}")

if __name__ == "__main__":
    process_v10_batch(1, 10)
