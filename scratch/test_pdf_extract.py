
import fitz
from pathlib import Path

def extract_pdf_text(pdf_path: Path) -> str:
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        text = page.get_text("text")
        pages.append(text)
    doc.close()
    return "\n".join(pages)

pdf_path = Path("/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files/10_vol._3_chapter.pdf")
text = extract_pdf_text(pdf_path)
print(f"Extracted {len(text)} characters.")
print("First 500 chars:")
print(text[:500])
