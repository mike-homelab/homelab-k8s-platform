import novel_cleaner_agent
import os
import time

def test_v10_c1():
    raw_dir = "/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files/"
    files = os.listdir(raw_dir)
    raw_file = next((f for f in files if f.startswith("10_vol._1_chapter")), None)
    if not raw_file:
        print("Error: Could not find Volume 10 Chapter 1 file.")
        return
    raw_path = os.path.join(raw_dir, raw_file)
    clean_dir = "/home/michael/Documents/wrong_way_to_use_healing_magic/cleaned_files/"
    os.makedirs(clean_dir, exist_ok=True)
    
    print(f"[{time.strftime('%H:%M:%S')}] Processing Volume 10 Chapter 1...")
    
    full_text = novel_cleaner_agent.get_pdf_text(raw_path)
    if not full_text.strip():
        print("Error: No text extracted from PDF.")
        return
    
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
    
    out_path = os.path.join(clean_dir, "Test_Volume_10_Chapter_1.md")
    with open(out_path, "w") as out_file:
        out_file.write("# Volume 10 Chapter 1\n\n")
        for i, seg in enumerate(segments):
            print(f"  -> Segment {i+1}/{len(segments)}")
            relevant_terms = novel_cleaner_agent.get_relevant_terms(seg)
            cleaned = novel_cleaner_agent.clean_segment(seg, relevant_terms)
            if cleaned:
                out_file.write(cleaned + "\n\n")
            out_file.flush()
            
    print(f"  -> Completed Chapter 1. Saved to {out_path}")
    
    # Print costs
    input_cost = (novel_cleaner_agent.total_input_tokens / 1_000_000) * novel_cleaner_agent.COST_PER_1M_INPUT
    output_cost = (novel_cleaner_agent.total_output_tokens / 1_000_000) * novel_cleaner_agent.COST_PER_1M_OUTPUT
    print(f"\nCloud Savings for this run: ${input_cost + output_cost:.4f}")

if __name__ == "__main__":
    test_v10_c1()
