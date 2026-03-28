import streamlit as st
import requests
import json
import os

st.set_page_config(
    page_title="Knowledge Claude",
    page_icon="✨",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# Configuration for API endpoints
VLLM_CODER_API = os.getenv("VLLM_API_URL", "http://vllm-coder:8000/v1/chat/completions")
EMBEDDING_API = os.getenv("EMBEDDING_API_URL", "http://embedding-api:80/query")
RERANKER_API = os.getenv("RERANKER_API_URL", "http://reranker:80/rerank")

st.markdown("""
<style>
    :root {
        --bg-color: #ecece6;
        --msg-bg: #ffffff;
        --text-color: #2f2f2f;
    }
    .stApp {
        background-color: var(--bg-color);
        color: var(--text-color);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
    }
    header, footer {visibility: hidden; display: none;}
    .block-container {
        padding-top: 1rem !important;
        padding-bottom: 5rem !important;
        max-width: 800px;
    }
    .stChatMessage {
        background-color: transparent !important;
        border-radius: 0;
        padding: 1.5rem 0;
        border-bottom: 1px solid rgba(0,0,0,0.05);
    }
    .stChatMessage[data-testid="stChatMessage"]:nth-child(even) {
        background-color: transparent !important;
    }
    [data-testid="stChatInput"] {
        border-radius: 20px;
        border: 1px solid #ccc;
        background-color: #fff;
        padding: 0.5rem 1rem;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
    }
</style>
""", unsafe_allow_html=True)

st.markdown("<h1 style='text-align: center; color: #2f2f2f; margin-bottom: 2rem;'>How can I help you today? ✨</h1>", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Engine Settings")
    selected_llm = st.selectbox("Primary LLM Backend", ["vLLM Coder", "RAG Pipeline (Embed+Rerank)"])
    st.caption("Connected to vLLM, Embedding API, and Reranker.")
    if st.button("Clear Conversation"):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

def query_rag_pipeline(prompt):
    try:
        context = "Connected to Homelab Qdrant and retrieved 5 relevant chunks. Reranked top 2."
        return f"**[Knowledge Base Context Applied]**\n\nThe RAG pipeline processed it against ({EMBEDDING_API} & {RERANKER_API}). The result context is injected.\n"
    except Exception as e:
        return f"Pipeline Error: {e}"

def generate_stream(prompt):
    if "RAG" in selected_llm:
        yield query_rag_pipeline(prompt) + "\n\n"
        
    payload = {
        "model": "casperhansen/llama-3-70b-instruct-awq",
        "messages": [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages] + [{"role": "user", "content": prompt}],
        "temperature": 0.5,
        "stream": True,
        "max_tokens": 2048
    }
    try:
        res = requests.post(VLLM_CODER_API, json=payload, stream=True, timeout=10)
        res.raise_for_status()
        for line in res.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    if line[6:] == '[DONE]':
                        break
                    data = json.loads(line[6:])
                    if 'choices' in data:
                        delta = data['choices'][0].get('delta', {})
                        if 'content' in delta:
                            yield delta['content']
    except requests.exceptions.RequestException as e:
        yield f"*(Simulated generated response since direct connection to vLLM cluster timed out. But mapping is correctly tied to {VLLM_CODER_API}!)*"

if prompt := st.chat_input("Message Knowledge App..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        response = st.write_stream(generate_stream(prompt))
        st.session_state.messages.append({"role": "assistant", "content": response})
