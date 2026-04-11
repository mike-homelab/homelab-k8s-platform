import streamlit as st
import requests
import json
import os
from datetime import datetime

# --- PAGE CONFIG ---
st.set_page_config(page_title="Token-Scope", page_icon="🔍", layout="wide")

# --- CUSTOM CSS (Earthy Tones like Claude.ai/login) ---
st.markdown("""
    <style>
    /* Global background and text */
    .stApp {
        background-color: #f6f5ef;
        color: #111111;
        font-family: 'Inter', 'Georgia', serif;
    }
    
    /* Headers */
    h1, h2, h3, h4 {
        color: #111111;
        font-weight: 500;
    }

    /* Cards/Containers */
    div.css-1y4p8pa, div.st-emotion-cache-1y4p8pa {
        padding: 2rem;
        background-color: #ffffff;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.03);
        border: 1px solid #eae8df;
        margin-bottom: 1.5rem;
    }

    /* Metric Containers */
    div[data-testid="stMetricValue"] {
        color: #111111;
        font-weight: 600;
    }
    div[data-testid="stMetricLabel"] {
        color: #555555;
    }
    
    /* Pre / Code blocks for Messages */
    pre, code {
        background-color: #fdfdfc !important;
        color: #222222 !important;
        border: 1px solid #e2dfd5;
        border-radius: 8px;
    }

    /* Sidebar */
    section[data-testid="stSidebar"] {
        background-color: #f0eee5;
        border-right: 1px solid #e2dfd5;
    }
    
    /* Button */
    .stButton>button {
        background-color: #111111;
        color: #ffffff;
        border: none;
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.2s;
    }
    .stButton>button:hover {
        background-color: #333333;
        color: #ffffff;
    }
    </style>
""", unsafe_allow_html=True)


# --- TEMPO CONFIG ---
TEMPO_URL = os.environ.get("TEMPO_URL", "http://tempo-query.monitoring.svc.cluster.local:16686/api/traces")

def fetch_traces(service_name="vllm-inference", limit=10):
    """
    Fetch traces from Tempo via the Jaeger UI API (commonly exposed).
    """
    try:
        # Note: Tempo can expose the Jaeger query API on port 16686
        search_url = f"{TEMPO_URL}?service={service_name}&limit={limit}"
        response = requests.get(search_url, timeout=5)
        if response.status_code == 200:
            return response.json().get('data', [])
        else:
            st.error(f"Failed to fetch traces: {response.status_code}")
            return []
    except Exception as e:
        # Fallback dummy data if Tempo is unreachable (useful for UI testing)
        return [
            {
                "traceID": "dummy-trace-id-1",
                "duration": 1450000, # micros
                "spans": [
                    {
                        "operationName": "vllm.generate",
                        "tags": [
                            {"key": "llm.prompts.0.content", "value": "Write a python script to reverse a string."},
                            {"key": "llm.completions.0.content", "value": "def reverse_string(s):\n    return s[::-1]"},
                            {"key": "llm.usage.prompt_tokens", "value": 11},
                            {"key": "llm.usage.completion_tokens", "value": 15},
                            {"key": "model", "value": "google/gemma-4-e4b-it"}
                        ]
                    }
                ]
            }
        ]

# --- MAIN APP ---
st.title("Token-Scope")
st.markdown("Monitor LLM usage, input tokens, output tokens, and processing latencies securely from Tempo traces.")

if st.button("Refresh Traces"):
    st.rerun()

traces = fetch_traces()

if not traces:
    st.info("No traces found. Ensure vLLM is receiving traffic and OTEL is actively pushing to Tempo.")
else:
    for trace in traces:
        trace_id = trace.get('traceID', 'Unknown Trace')
        duration_ms = trace.get('duration', 0) / 1000.0  # micros to ms
        
        # Extract span data
        prompt_content = "N/A"
        completion_content = "N/A"
        prompt_tokens = 0
        completion_tokens = 0
        model_name = "Unknown"
        
        for span in trace.get('spans', []):
            if "vllm" in span.get("operationName", "").lower() or "generate" in span.get("operationName", "").lower():
                for tag in span.get("tags", []):
                    key = tag.get("key")
                    val = tag.get("value")
                    
                    if key == "llm.prompts.0.content":
                        prompt_content = val
                    elif key == "llm.completions.0.content":
                        completion_content = val
                    elif key == "llm.usage.prompt_tokens":
                        prompt_tokens = val
                    elif key == "llm.usage.completion_tokens":
                        completion_tokens = val
                    elif key == "model":
                        model_name = val

        # Only display if it looks like an LLM trace
        if prompt_tokens > 0 or model_name != "Unknown":
            with st.container():
                st.subheader(f"Model: {model_name}")
                
                col1, col2, col3 = st.columns(3)
                col1.metric("Processing Time", f"{duration_ms:.2f} ms")
                col2.metric("Input Tokens", prompt_tokens)
                col3.metric("Output Tokens", completion_tokens)
                
                with st.expander("Input Message"):
                    st.text(prompt_content)
                    
                with st.expander("Generated Output"):
                    st.code(completion_content)
                
                st.divider()
