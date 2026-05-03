#!/usr/bin/env python3
"""
Novel Proofreader Agent - "The Wrong Way to Use Healing Magic"
==============================================================
Two-phase pipeline:
  Phase 1 - Bible Builder : Extract Character & Grammar Bible from translated vols
  Phase 2 - Chapter Cleaner: Clean raw fan-translated chapters using the Bible

LLM routing:
  - analyst (DeepSeek-R1 / ~128K ctx) : planning, bible synthesis, quality review
  - builder (Mistral-Small / ~24K ctx) : execution & chapter cleaning

Output: Markdown (.md) + optional PDF per chapter, cost savings report at end.
Author: Antigravity for Michael's Homelab
"""

import os
import sys
import re
import json
import time
import datetime
import textwrap
import argparse
import hashlib
import traceback
import threading
import concurrent.futures
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests
import fitz  # PyMuPDF
import markdown as md_lib
from weasyprint import HTML as WeasyprintHTML

# ─────────────────────── GLOBAL TOKEN TRACKER ───────────────────────── #

class TokenTracker:
    """Accumulates token usage across all LLM calls for cost reporting."""
    def __init__(self):
        self.local_calls: list[dict] = []
        self.cloud_calls: list[dict] = []
        self._lock = threading.Lock()

    def record(self, model: str, prompt_tokens: int, completion_tokens: int, is_local: bool = True):
        call = {
            "model": model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        with self._lock:
            if is_local:
                self.local_calls.append(call)
            else:
                self.cloud_calls.append(call)

    def totals(self) -> dict:
        by_model: dict[str, dict] = {}
        for c in self.local_calls + self.cloud_calls:
            m = c["model"]
            if m not in by_model:
                by_model[m] = {"prompt": 0, "completion": 0, "calls": 0}
            by_model[m]["prompt"]     += c["prompt_tokens"]
            by_model[m]["completion"] += c["completion_tokens"]
            by_model[m]["calls"]      += 1
        return by_model

    def total_tokens(self) -> tuple[int, int]:
        p = sum(c["prompt_tokens"]     for c in self.local_calls + self.cloud_calls)
        o = sum(c["completion_tokens"] for c in self.local_calls + self.cloud_calls)
        return p, o

    def local_totals(self) -> tuple[int, int]:
        p = sum(c["prompt_tokens"]     for c in self.local_calls)
        o = sum(c["completion_tokens"] for c in self.local_calls)
        return p, o

    def cloud_totals(self) -> tuple[int, int]:
        p = sum(c["prompt_tokens"]     for c in self.cloud_calls)
        o = sum(c["completion_tokens"] for c in self.cloud_calls)
        return p, o

TRACKER = TokenTracker()

# Cloud equivalent pricing (GPT-4o as reference, per 1M tokens, USD)
CLOUD_PRICE_INPUT_PER_M  = 5.00   # GPT-4o input  $5 / 1M tokens
CLOUD_PRICE_OUTPUT_PER_M = 15.00  # GPT-4o output $15 / 1M tokens
LOCAL_COST_PER_CALL      = 0.0    # Local inference: $0 marginal cost

# ─────────────────────────────── CONFIG ─────────────────────────────── #

LITELLM_BASE   = "https://llm.michaelhomelab.work/v1"
LITELLM_KEY    = "sk-michael-homelab-llm-proxy"
MODEL_PLANNER  = "analyst"   # DeepSeek-R1 (14B) – Planning & QA
MODEL_EXECUTOR = "builder"   # Mistral-Small (24B) – Execution & Cleaning

# Context budgets (in characters, ~4 chars/token estimate)
PLANNER_CTX_CHARS  = 100_000   # 32K context safety limit
EXECUTOR_CTX_CHARS = 50_000    # 16K context safety limit

TRANSLATED_DIR = Path("/home/michael/Documents/wrong_way_to_use_healing_magic/translated_vol")
RAW_DIR        = Path("/home/michael/Documents/wrong_way_to_use_healing_magic/raw_files")
CLEANED_DIR    = Path("/home/michael/Documents/wrong_way_to_use_healing_magic/cleaned_files_v2")
BIBLE_DIR           = Path("/home/michael/obsidian/Library/Novels/The_Wrong_Way_to_Use_Healing_Magic/v2")
BIBLE_PATH          = BIBLE_DIR / "character_grammar_bible.json"
BIBLE_PROGRESS_PATH = BIBLE_DIR / "bible_progress.json"

# Concurrency settings
MAX_WORKERS   = 1  # Process 1 chapter at a time for maximum accuracy
CHUNK_WORKERS = 4  # Process 4 chunks of that chapter in parallel to saturate GPU
LOG_LOCK = threading.Lock()

def safe_log(msg: str):
    """Thread-safe logging to console."""
    with LOG_LOCK:
        print(msg, flush=True)

# ──────────────────────────── UTILITIES ──────────────────────────────── #

def llm_call(model: str, system: str, user: str, temperature: float = 0.2,
             max_tokens: int = 4096, retries: int = 3) -> str:
    """Send a chat completion request to the LiteLLM proxy and track token usage."""
    url = f"{LITELLM_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {LITELLM_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "messages": [
            {"role": "system",  "content": system},
            {"role": "user",    "content": user},
        ],
    }
    payload["stream"] = True
    
    try:
        # Use streaming to keep connection alive and avoid gateway timeouts
        resp = requests.post(url, headers=headers, json=payload,
                             timeout=3600, verify=False, stream=True)
        resp.raise_for_status()
        
        full_content = []
        prompt_tok = 0
        completion_tok = 0
        
        for line in resp.iter_lines():
            if not line:
                continue
            line_str = line.decode("utf-8")
            if line_str.startswith("data: "):
                data_str = line_str[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    
                    # Capture multiple potential content fields (reasoning models use different ones)
                    content   = delta.get("content", "")
                    reasoning = delta.get("reasoning_content", "") or delta.get("thought", "")
                    
                    if content:
                        full_content.append(content)
                    if reasoning:
                        # We collect reasoning but it will be stripped later if needed
                        full_content.append(reasoning)
                    
                    # Extract usage if present
                    usage = chunk.get("usage")
                    if usage:
                        prompt_tok = usage.get("prompt_tokens", 0)
                        completion_tok = usage.get("completion_tokens", 0)
                except Exception:
                    continue
        
        final_content = "".join(full_content).strip()
        
        if not final_content:
            raise RuntimeError(f"LLM returned absolutely no content for {model}.")
        
        # ── Track token usage ──
        if prompt_tok == 0:
            prompt_tok     = len(system + user) // 4
            completion_tok = len(final_content) // 4
        
        TRACKER.record(model, prompt_tok, completion_tok, is_local=True)
        
        # ── Handle Reasoning/Thinking blocks ──
        # 1. Strip known thinking tags (critical for agent-planner)
        final_content = re.sub(r'<thought>.*?</thought>', '', final_content, flags=re.DOTALL)
        final_content = re.sub(r'<think>.*?</think>', '', final_content, flags=re.DOTALL)
        final_content = re.sub(r'thinking\n.*?\n', '', final_content, flags=re.DOTALL)
        
        # 2. Extract from <cleaned_text> tags if present
        tag_match = re.search(r'<cleaned_text>(.*?)</cleaned_text>', final_content, flags=re.DOTALL)
        if tag_match:
            final_content = tag_match.group(1).strip()
        
        return final_content.strip()
    except Exception as exc:
        safe_log(f"  [LLM] Call failed for {model}: {exc}")
        raise RuntimeError(f"LLM call failed for {model}: {exc}")

def wakeup_models():
    """Ensure both the analyst and builder models are awake and responsive."""
    safe_log("\n[WAKEUP] Warming up Agentic Duo endpoints...")
    models = [MODEL_PLANNER, MODEL_EXECUTOR]
    
    def poke(m):
        try:
            safe_log(f"  -> Poking {m}...")
            llm_call(m, "ping", "Hello, are you awake? Reply with 'yes'.", max_tokens=512)
            safe_log(f"  [✓] {m} is online.")
        except Exception as e:
            safe_log(f"  [!] Failed to wake {m}: {e}")

    threads = []
    for m in models:
        t = threading.Thread(target=poke, args=(m,))
        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()
    safe_log("[WAKEUP] Warm-up sequence complete.\n")



def extract_pdf_text(pdf_path: Path) -> str:
    """Extract clean text from a PDF using PyMuPDF."""
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        text = page.get_text("text")
        pages.append(text)
    doc.close()
    return "\n".join(pages)


def chunk_text(text: str, max_chars: int, overlap: int = 500) -> list[str]:
    """Split text into overlapping chunks that fit within max_chars."""
    chunks = []
    start  = 0
    while start < len(text):
        end = start + max_chars
        chunks.append(text[start:end])
        start = end - overlap  # small overlap for context continuity
        if start >= len(text):
            break
    return chunks


def safe_filename(name: str) -> str:
    """Sanitize a string to be a safe directory/file name."""
    return re.sub(r'[^\w\s-]', '_', name).strip()


def merge_dicts_deep(base: dict, patch: dict) -> dict:
    """Recursively merge patch into base (lists are extended, not replaced)."""
    for k, v in patch.items():
        if k in base:
            if isinstance(base[k], dict) and isinstance(v, dict):
                merge_dicts_deep(base[k], v)
            elif isinstance(base[k], list) and isinstance(v, list):
                # deduplicate by string representation
                existing = {str(i) for i in base[k]}
                base[k].extend(i for i in v if str(i) not in existing)
            else:
                base[k] = v
        else:
            base[k] = v
    return base


# ─────────────────────────── PHASE 1 : BIBLE ─────────────────────────── #

BIBLE_EXTRACT_SYSTEM = textwrap.dedent("""\
    You are a professional light novel editor and Japanese-to-English localization expert.
    You are analyzing chapters of "The Wrong Way to Use Healing Magic" to build a canonical
    Character & Grammar Bible for future proofreading.

    Your output MUST be valid JSON (no markdown fences, no extra prose).
    Follow this exact schema:

    {
      "characters": {
        "<CharacterName>": {
          "gender": "male|female|unknown",
          "pronouns": "he/him|she/her|they/them",
          "self_reference": "watashi|boku|ore|atashi|etc (or null)",
          "speech_style": "formal|casual|rude|childlike|etc",
          "titles_held": ["Princess", "Knight Commander", ...],
          "aliases": ["nickname1", "alias2"]
        }
      },
      "relationships": [
        {"from": "CharA", "to": "CharB", "address_term": "how CharA addresses CharB"}
      ],
      "terminology": {
        "skills": {"<jp_or_wrong_term>": "<correct_en_term>"},
        "locations": {"<jp_or_wrong_term>": "<correct_en_term>"},
        "items": {"<jp_or_wrong_term>": "<correct_en_term>"},
        "titles": {"<jp_or_wrong_term>": "<correct_en_term>"},
        "other": {"<jp_or_wrong_term>": "<correct_en_term>"}
      },
      "gender_fixes": [
        "Sentence-level note about a recurring gender error to watch for"
      ],
      "honorifics_policy": "Description of how honorifics should be handled"
    }

    Extract ONLY information present in the text. Use null for unknown fields.
    If a character appears with inconsistent gender, note both and flag under gender_fixes.
""")


def extract_bible_from_chunk(chunk: str, vol_label: str) -> dict:
    """Ask the reasoning model to extract bible data from one text chunk."""
    user_msg = f"[Source: {vol_label}]\n\n{chunk}"
    raw = llm_call(MODEL_PLANNER, BIBLE_EXTRACT_SYSTEM, user_msg,
                   temperature=0.1, max_tokens=4096)
    
    # Robust JSON extraction
    json_str = raw
    if "```" in raw:
        # Try to extract content between triple backticks
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
        if match:
            json_str = match.group(1)
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        # Second attempt: find the first { and last }
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except Exception:
                pass
        
        safe_log(f"  [WARN] JSON parse failed for chunk from {vol_label}. Raw response snippet: {raw[:100]}...")
        return {}


BIBLE_SYNTHESIS_SYSTEM = textwrap.dedent("""\
    You are a senior light novel editor. You have received multiple partial Character & Grammar
    Bible drafts extracted from different volumes of "The Wrong Way to Use Healing Magic".
    
    Your job is to MERGE and RECONCILE all drafts into one single, authoritative, deduplicated
    Character & Grammar Bible. Resolve any contradictions (e.g., inconsistent gender) by
    choosing the most frequently stated value and noting the discrepancy in gender_fixes.

    Output MUST be a single valid JSON object matching this schema (no markdown, no prose):
    {
      "characters": { ... },
      "relationships": [ ... ],
      "terminology": { "skills": {}, "locations": {}, "items": {}, "titles": {}, "other": {} },
      "gender_fixes": [ ... ],
      "honorifics_policy": "..."
    }
""")


def synthesize_bible(partial_bibles: list[dict]) -> dict:
    """Merge all partial bibles into one authoritative bible using the reasoning model."""
    # Merge in-memory first to reduce payload size
    merged = {}
    for pb in partial_bibles:
        merged = merge_dicts_deep(merged, pb)

    merged_str = json.dumps(merged, ensure_ascii=False, indent=2)

    # If merged JSON fits in one reasoning call, synthesize directly
    if len(merged_str) < PLANNER_CTX_CHARS - 4000:
        system = BIBLE_SYNTHESIS_SYSTEM
        user = f"Here are all partial bible drafts merged:\n\n{merged_str}"
        raw = llm_call(MODEL_PLANNER, system, user, temperature=0.1, max_tokens=8192)
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)
        try:
            return json.loads(raw)
        except Exception:
            print("[WARN] Final synthesis parse failed, returning in-memory merge.")
            return merged
    else:
        print("[INFO] Merged bible too large for synthesis call, returning in-memory merge.")
        return merged


def load_bible_progress() -> dict:
    """Load Phase 1 progress from disk."""
    if BIBLE_PROGRESS_PATH.exists():
        try:
            with open(BIBLE_PROGRESS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] Failed to load progress file: {e}")
    return {"processed_volumes": [], "partial_bibles": []}


def save_bible_progress(progress: dict):
    """Save Phase 1 progress to disk."""
    BIBLE_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BIBLE_PROGRESS_PATH, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def phase1_build_bible(force: bool = False) -> dict:
    """
    Phase 1: Read all translated PDFs and build the Character & Grammar Bible.
    Saves result to BIBLE_PATH as JSON.
    Includes checkpointing to resume from interrupted runs.
    """
    print("\n" + "="*60)
    print("PHASE 1 — Building Character & Grammar Bible")
    print("="*60)

    BIBLE_DIR.mkdir(parents=True, exist_ok=True)

    pdfs = sorted(TRANSLATED_DIR.glob("*.pdf"))
    if not pdfs:
        if BIBLE_PATH.exists():
            safe_log(f"[INFO] No PDFs found, but Bible exists at {BIBLE_PATH}. Loading existing.")
            with open(BIBLE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        raise FileNotFoundError(f"No PDFs found in {TRANSLATED_DIR} and no existing Bible found.")

    # Load progress
    progress = {"processed_volumes": [], "partial_bibles": []} if force else load_bible_progress()
    processed_volumes = set(progress.get("processed_volumes", []))
    partial_bibles = progress.get("partial_bibles", [])

    # Check if we actually have anything new to process
    all_vol_labels = {p.stem for p in pdfs}
    new_volumes = all_vol_labels - processed_volumes

    if not new_volumes and BIBLE_PATH.exists() and not force:
        safe_log(f"[SKIP] All {len(pdfs)} volumes already processed. Bible is up to date.")
        with open(BIBLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)

    safe_log(f"[INFO] Found {len(pdfs)} total volume(s). {len(new_volumes)} are new.")

    for pdf_path in pdfs:
        vol_label = pdf_path.stem
        if vol_label in processed_volumes:
            print(f"  [SKIP] Volume already processed: {vol_label}")
            continue

        print(f"\n  Processing: {vol_label}")
        text = extract_pdf_text(pdf_path)
        print(f"    Extracted {len(text):,} characters of text.")

        # Chunk to fit planner model context
        chunks = chunk_text(text, PLANNER_CTX_CHARS - 4000)
        safe_log(f"    Split into {len(chunks)} chunk(s). Processing in parallel...")

        vol_bibles = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_chunk = {
                executor.submit(extract_bible_from_chunk, chunk, f"{vol_label} chunk {i}/{len(chunks)}"): i 
                for i, chunk in enumerate(chunks, 1)
            }
            for future in concurrent.futures.as_completed(future_to_chunk):
                i = future_to_chunk[future]
                try:
                    partial = future.result()
                    if partial:
                        vol_bibles.append(partial)
                    safe_log(f"    → Chunk {i}/{len(chunks)} completed.")
                except Exception as exc:
                    safe_log(f"    → Chunk {i}/{len(chunks)} generated an exception: {exc}")

        # Merge all chunks from this volume
        vol_merged = {}
        for vb in vol_bibles:
            vol_merged = merge_dicts_deep(vol_merged, vb)
        
        partial_bibles.append(vol_merged)
        
        # Update progress
        progress["processed_volumes"].append(vol_label)
        progress["partial_bibles"] = partial_bibles
        save_bible_progress(progress)
        safe_log(f"    [CHECKPOINT] Progress saved for {vol_label}")

    print("\n[INFO] Synthesizing final bible …")
    bible = synthesize_bible(partial_bibles)

    # Ensure all required keys exist
    for key in ["characters", "relationships", "terminology", "gender_fixes", "honorifics_policy"]:
        if key not in bible:
            bible[key] = {} if key in ("characters", "terminology") else []

    BIBLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(BIBLE_PATH, "w", encoding="utf-8") as f:
        json.dump(bible, f, ensure_ascii=False, indent=2)

    # We keep the progress file as a permanent checkpoint for partial bibles
    safe_log(f"[INFO] Bible checkpoint updated at {BIBLE_PROGRESS_PATH}")

    char_count = len(bible.get("characters", {}))
    term_count = sum(len(v) for v in bible.get("terminology", {}).values() if isinstance(v, dict))
    print(f"[OK] Bible saved → {BIBLE_PATH}")
    print(f"     Characters: {char_count}  |  Terminology entries: {term_count}")
    return bible


# ─────────────────────────── PHASE 2 : CLEAN ─────────────────────────── #

CODER_CLEAN_SYSTEM = textwrap.dedent("""\
    You are a professional editor specializing in Japanese light novel localization into English.
    Your task is to CORRECT and POLISH a fan-translated chapter of
    "The Wrong Way to Use Healing Magic" using the provided Character & Grammar Bible.

    STRICT RULES:
    1. Fix ALL gender pronoun errors (he/she/his/her/him) using the Bible's character gender map.
    2. Fix ALL Mr./Mrs./Ms. honorific mismatches using the Bible's gender data.
    3. Replace any terminology that differs from the Bible's glossary with the canonical term.
    4. Fix grammatical errors and awkward literal-translation phrasing.
    5. Improve dialogue flow so it sounds like a native English light novel — not a word-for-word MTL.
    6. Preserve the original chapter structure: keep all paragraph breaks, scene breaks (***), and chapter headings.
    7. Do NOT add, remove, or summarize plot content. Every scene and event must be present.
    8. Do NOT add commentary or meta-notes — output the corrected chapter text ONLY.
    9. Dialogue tags should use natural English speech verbs (said, replied, asked, exclaimed, etc.).

    Output format: You MUST wrap the final corrected chapter text inside <cleaned_text> tags. 
    Example: <cleaned_text>Chapter content here...</cleaned_text>
    Do NOT include any thoughts, explanations, or meta-notes outside these tags.
""")

REASON_QA_SYSTEM = textwrap.dedent("""\
    You are a quality-assurance editor for the light novel "The Wrong Way to Use Healing Magic".
    You will be given: (1) the raw fan-translated text, (2) the cleaned version, and (3) the Bible.
    
    Your job: Perform a final QA pass and output an improved final version.
    Focus on:
    - Any remaining pronoun/gender errors the previous pass missed
    - Consistency with terminology in the Bible
    - Natural English flow in dialogue and narration
    - Completeness: ensure NO plot content was dropped

    Output format: You MUST wrap the final corrected chapter text inside <cleaned_text> tags.
    Do NOT include any explanation or meta-notes outside these tags.
""")


def build_bible_context(bible: dict, max_chars: int = 3000) -> str:
    """Serialize the bible into a compact context string for LLM prompts."""
    lines = ["=== CHARACTER & GRAMMAR BIBLE ===\n"]

    # Characters
    chars = bible.get("characters", {})
    if chars:
        lines.append("--- CHARACTERS ---")
        for name, info in chars.items():
            gender   = info.get("gender", "unknown")
            pronouns = info.get("pronouns", "unknown")
            style    = info.get("speech_style", "")
            
            # Defensive checks for list fields
            raw_aliases = info.get("aliases", [])
            if not isinstance(raw_aliases, (list, tuple)):
                raw_aliases = [raw_aliases] if raw_aliases else []
            aliases = ", ".join([str(a) for a in raw_aliases if a])

            raw_titles = info.get("titles_held", [])
            if not isinstance(raw_titles, (list, tuple)):
                raw_titles = [raw_titles] if raw_titles else []
            titles = ", ".join([str(t) for t in raw_titles if t])

            line = f"{name}: {gender} ({pronouns})"
            if titles:   line += f", titles: {titles}"
            if aliases:  line += f", aliases: {aliases}"
            if style:    line += f", speech: {style}"
            lines.append(line)

    # Relationships
    rels = bible.get("relationships", [])
    if rels:
        lines.append("\n--- RELATIONSHIPS (how A addresses B) ---")
        for r in rels[:40]:  # cap to avoid overflow
            lines.append(f"  {r.get('from','?')} → {r.get('to','?')}: \"{r.get('address_term','?')}\"")

    # Terminology
    terms = bible.get("terminology", {})
    if terms:
        lines.append("\n--- TERMINOLOGY ---")
        for category, mapping in terms.items():
            if isinstance(mapping, dict) and mapping:
                lines.append(f"  [{category.upper()}]")
                for wrong, correct in list(mapping.items())[:20]:
                    lines.append(f"    {wrong} → {correct}")

    # Gender fixes
    fixes = bible.get("gender_fixes", [])
    if fixes:
        lines.append("\n--- KNOWN GENDER ERRORS TO WATCH ---")
        for fix in fixes[:10]:
            lines.append(f"  • {fix}")

    # Honorifics
    hon = bible.get("honorifics_policy", "")
    if hon:
        lines.append(f"\n--- HONORIFICS POLICY ---\n  {hon}")

    result = "\n".join(lines)
    return result[:max_chars]  # hard-cap


def clean_chapter_main(raw_text: str, bible_ctx: str, chapter_label: str) -> str:
    """
    Clean raw text using the BUILDER model (Mistral Small) for high-fidelity cleaning.
    """
    # Max chars for chapter text = builder budget minus bible context and prompt overhead
    overhead   = len(bible_ctx) + 500
    max_text   = EXECUTOR_CTX_CHARS - overhead

    if max_text < 10000:
        # Bible context too large – trim it further
        bible_ctx = bible_ctx[:EXECUTOR_CTX_CHARS // 4]
        overhead  = len(bible_ctx) + 500
        max_text  = EXECUTOR_CTX_CHARS - overhead

    chunks = chunk_text(raw_text, max_text, overlap=2000)
    cleaned_chunks = [None] * len(chunks)

    # Process chunks sequentially within a chapter to avoid nested ThreadPoolExecutor 
    # and overloading the LLM proxy (since chapters are already parallel).
    def process_chunk(i, chunk):
        label = f"{chapter_label} [part {i+1}/{len(chunks)}]"
        user_msg = (
            f"{bible_ctx}\n\n"
            f"=== RAW CHAPTER TEXT ({label}) ===\n\n"
            f"{chunk}"
        )
        safe_log(f"      Builder cleaning pass part {i+1}/{len(chunks)} …")
        
        result = llm_call(MODEL_EXECUTOR, CODER_CLEAN_SYSTEM, user_msg,
                          temperature=0.25, max_tokens=16384)
        
        if not result:
            safe_log(f"      [WARN] Empty result for {label}. Retrying with higher temperature...")
            result = llm_call(MODEL_EXECUTOR, CODER_CLEAN_SYSTEM, user_msg,
                              temperature=0.7, max_tokens=16384)
            
        return i, result

    with concurrent.futures.ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as executor:
        futures = [executor.submit(process_chunk, i, chunk) for i, chunk in enumerate(chunks)]
        for future in concurrent.futures.as_completed(futures):
            idx, res = future.result()
            cleaned_chunks[idx] = res

    final_text = "\n\n".join([c for c in cleaned_chunks if c])
    
    if not final_text:
        raise ValueError(f"Cleaning pass for {chapter_label} returned NO text content.")
        
    return final_text


def qa_pass_reasoning(raw_text: str, cleaned_text: str, bible_ctx: str,
                      chapter_label: str) -> str:
    """
    Use the ANALYST model (DeepSeek-R1) for a QA pass.
    """
    total_len = len(bible_ctx) + len(raw_text) + len(cleaned_text) + 1000
    if total_len > PLANNER_CTX_CHARS:
        # Too large for QA pass — return cleaned_text as-is
        print(f"      [QA] Chapter too long for QA pass ({total_len:,} chars), skipping.")
        return cleaned_text

    user_msg = (
        f"{bible_ctx}\n\n"
        f"=== RAW TEXT ===\n{raw_text}\n\n"
        f"=== CLEANED VERSION (needs QA) ===\n{cleaned_text}"
    )
    print(f"      Analyst QA pass ({len(user_msg):,} chars) …", end=" ", flush=True)
    # QA pass uses the planning model to verify the work
    result = llm_call(MODEL_PLANNER, REASON_QA_SYSTEM, user_msg,
                      temperature=0.15, max_tokens=32768)
    print("done")
    return result


def get_volume_from_filename(filename: str) -> str:
    """Extract volume number from raw filename like '10_vol._1_chapter_...'"""
    match = re.match(r'^(\d+)_vol\.', filename)
    if match:
        return f"vol_{match.group(1).zfill(2)}"
    return "vol_unknown"


def get_chapter_sort_key(filename: str) -> tuple:
    """Return a sort key (vol_num, chapter_num) for ordering."""
    vol_match = re.match(r'^(\d+)_vol\._(\d+(?:\.\d+)?)', filename)
    if vol_match:
        vol = int(vol_match.group(1))
        # Handle chapter numbers like "13.1", "31.2"
        chap_str = vol_match.group(2)
        try:
            chap = float(chap_str)
        except ValueError:
            chap = 0.0
        return (vol, chap)
    return (9999, 0)


def phase2_clean_chapters(bible: dict, volumes: Optional[list[int]] = None,
                          force: bool = False, skip_qa: bool = False) -> None:
    """
    Phase 2: Clean all raw chapter PDFs using the bible.
    Groups chapters by volume and creates a single PDF for the entire volume.
    """
    print("\n" + "="*60)
    print("PHASE 2 — Cleaning Raw Chapters")
    print("="*60)

    bible_ctx = build_bible_context(bible, max_chars=3500)
    print(f"[INFO] Bible context size: {len(bible_ctx):,} chars")

    all_pdfs = sorted(RAW_DIR.glob("*.pdf"), key=lambda p: get_chapter_sort_key(p.name))
    if not all_pdfs:
        raise FileNotFoundError(f"No PDFs found in {RAW_DIR}")

    # Filter by volume if requested
    if volumes:
        all_pdfs = [p for p in all_pdfs if int(re.match(r'^(\d+)', p.name).group(1)) in volumes]
        print(f"[INFO] Filtered to volumes {volumes}: {len(all_pdfs)} chapter(s).")
    else:
        print(f"[INFO] Found {len(all_pdfs)} total raw chapter(s).")

    CLEANED_DIR.mkdir(parents=True, exist_ok=True)
    
    # Group chapters by volume
    volume_groups: Dict[str, List[Path]] = {}
    for p in all_pdfs:
        v_key = get_volume_from_filename(p.name)
        if v_key not in volume_groups:
            volume_groups[v_key] = []
        volume_groups[v_key].append(p)

    total_processed = total_skipped = total_errors = 0

    for vol_key, vol_pdfs in sorted(volume_groups.items()):
        print(f"\n[VOLUME] {vol_key.replace('_', ' ').upper()}")
        vol_dir = CLEANED_DIR / vol_key
        vol_dir.mkdir(parents=True, exist_ok=True)
        
        # Sort chapters correctly
        vol_pdfs.sort(key=lambda p: get_chapter_sort_key(p.name))
        
        chapter_mds = {} # stem -> markdown
        
        def process_chapter(pdf_path: Path):
            nonlocal total_processed, total_skipped, total_errors
            md_path  = vol_dir / (pdf_path.stem + ".md")

            # Check if MD already exists and not forcing
            if md_path.exists() and not force:
                with LOG_LOCK: total_skipped += 1
                with open(md_path, "r", encoding="utf-8") as f:
                    chapter_mds[pdf_path.stem] = f.read()
                return

            safe_log(f"  Processing {pdf_path.name}...")
            try:
                raw_text = extract_pdf_text(pdf_path)
                if len(raw_text.strip()) < 100:
                    safe_log(f"    [WARN] Very short text, skipping: {pdf_path.name}")
                    with LOG_LOCK: total_skipped += 1
                    return

                # Cleaning passes
                cleaned = clean_chapter_main(raw_text, bible_ctx, pdf_path.stem)
                if not skip_qa:
                    cleaned = qa_pass_reasoning(raw_text, cleaned, bible_ctx, pdf_path.stem)

                chapter_title = pdf_path.stem.replace("_", " ").title()
                md_content = render_chapter_markdown(chapter_title, cleaned, vol_key)
                
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(md_content)
                
                chapter_mds[pdf_path.stem] = md_content
                with LOG_LOCK: total_processed += 1
                safe_log(f"  ✓ Finished {pdf_path.name}")

            except Exception as exc:
                safe_log(f"    [ERROR] Failed to process {pdf_path.name}: {exc}")
                with LOG_LOCK: total_errors += 1

        # Use ThreadPoolExecutor for chapters within a volume
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            executor.map(process_chapter, vol_pdfs)

        # After all chapters in volume are processed, create single Volume PDF
        if chapter_mds:
            # Sort by original chapter order
            sorted_stems = [p.stem for p in vol_pdfs if p.stem in chapter_mds]
            
            # Combine markdown with page breaks
            # We add a page break before every chapter except the first one
            combined_md_parts = []
            for i, stem in enumerate(sorted_stems):
                md = chapter_mds[stem]
                if i > 0:
                    # Inject page break div
                    md = "\n\n<div style='page-break-before: always;'></div>\n\n" + md
                combined_md_parts.append(md)
            
            combined_md = "\n\n".join(combined_md_parts)
            vol_pdf_path = vol_dir / f"{vol_key}.pdf"
            
            print(f"  [PDF] Generating volume PDF: {vol_pdf_path.name} ...", end=" ", flush=True)
            try:
                render_pdf(combined_md, vol_pdf_path)
                print("done")
            except Exception as pdf_err:
                print(f"FAILED: {pdf_err}")

    print(f"\n[DONE] Processed: {total_processed}  |  Skipped: {total_skipped}  |  Errors: {total_errors}")



# ─────────────────── MARKDOWN & PDF RENDERING ───────────────────────── #

CHAPTER_CSS = """
@import url('https://fonts.googleapis.com/css2?family=EB+Garamond:ital,wght@0,400;0,600;1,400&display=swap');

body {
    font-family: 'EB Garamond', 'Georgia', serif;
    font-size: 12pt;
    line-height: 1.85;
    color: #1a1a1a;
    max-width: 680px;
    margin: 0 auto;
    padding: 48px 36px;
    background: #fafaf8;
}
h1 {
    font-size: 20pt;
    font-weight: 600;
    text-align: center;
    margin-bottom: 0.3em;
    color: #2c2c2c;
    letter-spacing: 0.03em;
    border-bottom: 2px solid #c8a97a;
    padding-bottom: 0.4em;
}
h2 {
    font-size: 13pt;
    font-weight: 600;
    margin-top: 2em;
    color: #3a3a3a;
}
p {
    margin: 0.6em 0;
    text-indent: 1.5em;
}
p:first-of-type { text-indent: 0; }
blockquote {
    border-left: 3px solid #c8a97a;
    margin: 1em 0 1em 1em;
    padding-left: 1em;
    color: #444;
    font-style: italic;
}
hr {
    border: none;
    text-align: center;
    margin: 2em 0;
    color: #999;
}
hr::after { content: '✦  ✦  ✦'; }
.meta {
    font-size: 9pt;
    color: #888;
    text-align: center;
    margin-bottom: 2em;
    font-style: italic;
}
@page {
    size: A4;
    margin: 2.4cm 2.2cm;
    @bottom-center {
        content: counter(page);
        font-family: 'EB Garamond', serif;
        font-size: 9pt;
        color: #aaa;
    }
}
"""


def render_chapter_markdown(title: str, body: str, vol_label: str) -> str:
    """Wrap cleaned text in a well-structured Markdown document."""
    # Normalise scene breaks: lines with only * or – or — become proper hr
    body = re.sub(r'^\s*[\*\-\—\–]{2,}\s*$', '\n---\n', body, flags=re.MULTILINE)
    # Ensure blank lines around HR
    body = re.sub(r'\n---\n', '\n\n---\n\n', body)
    # Collapse 3+ blank lines to 2
    body = re.sub(r'\n{3,}', '\n\n', body)

    now = datetime.datetime.now().strftime("%Y-%m-%d")
    return (
        f"# {title}\n\n"
        f'<p class="meta">{vol_label.replace("_", " ").upper()} · Cleaned {now} · '
        f"The Wrong Way to Use Healing Magic</p>\n\n"
        f"{body.strip()}\n"
    )


def render_pdf(md_content: str, out_path: Path) -> None:
    """Convert a Markdown string to a styled PDF using weasyprint."""
    # Markdown → HTML
    html_body = md_lib.markdown(
        md_content,
        extensions=["extra", "nl2br"],
    )
    full_html = f"""
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>{CHAPTER_CSS}</style>
</head>
<body>
{html_body}
</body>
</html>
"""
    WeasyprintHTML(string=full_html).write_pdf(str(out_path))


# ─────────────────────────── COST REPORT ─────────────────────────────── #

def print_cost_report(start_time: float) -> None:
    """Print a token consumption and cloud cost-savings report."""
    elapsed = time.time() - start_time
    totals  = TRACKER.totals()
    total_p, total_o = TRACKER.total_tokens()
    local_p, local_o = TRACKER.local_totals()
    cloud_p, cloud_o = TRACKER.cloud_totals()
    total_tok = total_p + total_o

    # Estimated cloud cost if all local calls were cloud
    equivalent_cloud_cost = (
        (local_p / 1_000_000) * CLOUD_PRICE_INPUT_PER_M +
        (local_o / 1_000_000) * CLOUD_PRICE_OUTPUT_PER_M
    )
    
    # Actual cloud cost (script currently uses 0)
    actual_cloud_cost = (
        (cloud_p / 1_000_000) * CLOUD_PRICE_INPUT_PER_M +
        (cloud_o / 1_000_000) * CLOUD_PRICE_OUTPUT_PER_M
    )
    
    savings = equivalent_cloud_cost - 0.0 # Assuming local is $0

    sep = "─" * 62
    print(f"\n{'═'*62}")
    print("  TOKEN CONSUMPTION & CLOUD SAVINGS REPORT")
    print(f"{'═'*62}")
    print(f"  Run duration : {elapsed/60:.1f} min  ({elapsed:.0f}s)")
    print(f"  Total calls  : {len(TRACKER.local_calls) + len(TRACKER.cloud_calls)}")
    print(sep)
    print(f"  {'Type':<10} {'Prompt':>15} {'Output':>15} {'Total':>15}")
    print(sep)
    print(f"  {'Local':<10} {local_p:>15,} {local_o:>15,} {local_p+local_o:>15,}")
    print(f"  {'Cloud':<10} {cloud_p:>15,} {cloud_o:>15,} {cloud_p+cloud_o:>15,}")
    print(sep)
    print(f"  {'TOTAL':<10} {total_p:>15,} {total_o:>15,} {total_p+total_o:>15,}")
    print(f"\n  {'Model Breakdown':<20} {'Calls':>6} {'Tokens':>15}")
    print(sep)
    for model, data in totals.items():
        print(f"  {model:<20} {data['calls']:>6} {data['prompt']+data['completion']:>15,}")
    
    print(f"\n  {'Metric':<38} {'Value':>20}")
    print(sep)
    print(f"  {'Local inference cost (homelab GPU)':<38} {'$0.00':>20}")
    print(f"  {'Actual Cloud cost':<38} {'${:>18.4f}'.format(actual_cloud_cost):>20}")
    print(f"  {'Equivalent GPT-4o cost (if cloud)':<38} {'${:>18.4f}'.format(equivalent_cloud_cost):>20}")
    print(sep)
    print(f"  {'💰 TOTAL SAVINGS vs GPT-4o':<38} {'${:>18.4f}'.format(savings):>20}")
    print(f"{'═'*62}\n")

    # Also save as Markdown report
    report_path = CLEANED_DIR / "usage_report.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# Novel Cleaner Agent — Usage Report\n\n")
        f.write(f"**Run date:** {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  \n")
        f.write(f"**Duration:** {elapsed/60:.1f} minutes  \n")
        f.write(f"**Total LLM calls:** {len(TRACKER.local_calls) + len(TRACKER.cloud_calls)}  \n\n")
        
        f.write("## Token Usage\n\n")
        f.write("| Type | Prompt Tokens | Output Tokens | Total |\n")
        f.write("|------|--------------:|--------------:|------:|\n")
        f.write(f"| Local | {local_p:,} | {local_o:,} | {local_p+local_o:,} |\n")
        f.write(f"| Cloud | {cloud_p:,} | {cloud_o:,} | {cloud_p+cloud_o:,} |\n")
        f.write(f"| **TOTAL** | **{total_p:,}** | **{total_o:,}** | **{total_p+total_o:,}** |\n\n")
        
        f.write("## Cost Savings vs Cloud (GPT-4o)\n\n")
        f.write(f"| Metric | Cost (USD) |\n")
        f.write(f"|--------|----------:|\n")
        f.write(f"| Local homelab inference | $0.0000 |\n")
        f.write(f"| Actual Cloud cost | ${actual_cloud_cost:.4f} |\n")
        f.write(f"| Equivalent Cloud cost (if used) | ${equivalent_cloud_cost:.4f} |\n")
        f.write(f"| **💰 Total savings** | **${savings:.4f}** |\n")
        f.write(f"\n> Pricing reference: GPT-4o at $5/1M input tokens, $15/1M output tokens (April 2026)\n")
    print(f"  Report saved → {report_path}")


# ─────────────────────────────── MAIN ────────────────────────────────── #

def main():
    parser = argparse.ArgumentParser(
        description="Novel Proofreader Agent — The Wrong Way to Use Healing Magic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Build bible only:
              python novel_cleaner_agent.py --phase 1

              # Clean all chapters (uses existing bible):
              python novel_cleaner_agent.py --phase 2

              # Clean only volumes 10 and 11:
              python novel_cleaner_agent.py --phase 2 --volumes 10 11

              # Full pipeline (build bible + clean all):
              python novel_cleaner_agent.py --phase all

              # Rebuild bible and re-clean volume 12:
              python novel_cleaner_agent.py --phase all --rebuild-bible --force --volumes 12

              # Skip QA pass (faster, less GPU time):
              python novel_cleaner_agent.py --phase 2 --skip-qa
        """)
    )
    parser.add_argument(
        "--phase",
        choices=["1", "2", "all"],
        default="all",
        help="Which phase to run: 1=bible, 2=clean, all=both (default: all)"
    )
    parser.add_argument(
        "--volumes",
        nargs="+",
        type=int,
        metavar="N",
        help="Only process these volume numbers (e.g. 10 11 12). Default: all volumes."
    )
    parser.add_argument(
        "--rebuild-bible",
        action="store_true",
        help="Force rebuild of the character bible even if it already exists."
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite already-cleaned chapter files."
    )
    parser.add_argument(
        "--skip-qa",
        action="store_true",
        help="Skip the reasoning-model QA pass (faster, uses less GPU time)."
    )

    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Novel Proofreader Agent — The Wrong Way to Use Healing  ║")
    print("║  Magic                                                    ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print(f"  Analyst (Planning & QA) : {MODEL_PLANNER} @ {LITELLM_BASE}")
    print(f"  Builder (Execution)     : {MODEL_EXECUTOR} @ {LITELLM_BASE}")
    print(f"  (Phase 2 uses Builder for cleaning & Analyst for QA)")
    print(f"  Bible path    : {BIBLE_PATH}")
    print(f"  Output dir    : {CLEANED_DIR} (.md + .pdf per chapter)")

    # Suppress SSL warnings for self-signed homelab certs
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    start_time = time.time()
    
    # 1. Wake up models
    wakeup_models()
    
    # 2. Try to prevent sleep (using wakepy if available)
    try:
        from wakepy import keep
        sleep_context = keep.running()
        safe_log("[SLEEP] wakepy: Preventing system sleep during execution.")
    except ImportError:
        safe_log("[SLEEP] wakepy not found. For best results, run with 'systemd-inhibit python ...'")
        # Create a dummy context manager
        class DummyContext:
            def __enter__(self): return self
            def __exit__(self, *args): pass
        sleep_context = DummyContext()

    with sleep_context:
        bible = None

        if args.phase in ("1", "all"):
            bible = phase1_build_bible(force=args.rebuild_bible)

        if args.phase in ("2", "all"):
            if bible is None:
                if BIBLE_PATH.exists():
                    print(f"\n[INFO] Loading existing bible from {BIBLE_PATH}")
                    with open(BIBLE_PATH, "r", encoding="utf-8") as f:
                        bible = json.load(f)
                else:
                    print("[ERROR] No bible found. Run with --phase 1 or --phase all first.")
                    sys.exit(1)
            phase2_clean_chapters(
                bible,
                volumes=args.volumes,
                force=args.force,
                skip_qa=args.skip_qa,
            )

    print_cost_report(start_time)
    print("✓ Agent finished.")


if __name__ == "__main__":
    main()
