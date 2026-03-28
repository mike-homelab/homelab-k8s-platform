import streamlit as st
import requests
import json
import os

# Configuration for the API endpoints
VLLM_API_URL = os.getenv("VLLM_API_URL", "http://vllm-coder:8000/v1/chat/completions")
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "http://embedding-api:80/query") # Adjust endpoint as needed
RERANKER_API_URL = os.getenv("RERANKER_API_URL", "http://reranker:80/rerank") # Adjust endpoint as needed

st.set_page_config(
    page_title="Knowledge Chat",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for a modern Claude-like dark theme
st.markdown("""
<style>
    /* Main background */
    .stApp {
        background-color: #1a1a1c;
        color: #e4e4e6;
    }
    
    /* Header/Title */
    header {
        background-color: transparent !important;
    }

    /* Chat Messages */
    .stChatMessage {
        background-color: transparent;
        padding: 1.5rem;
        border-radius: 0.5rem;
        margin-bottom: 1rem;
    }
    
    .stChatMessage[data-testid="stChatMessage"]:nth-child(even) {
        background-color: #242528;
    }

    /* Input box */
    .stChatInputContainer {
        border-color: #3b3c40;
    }
    
    .stTextInput > div > div > input {
        background-color: #2a2b2e;
        color: #fff;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #1e1e21;
        border-right: 1px solid #333;
    }
</style>
""", unsafe_allow_html=True)


with st.sidebar:
    st.title("🧠 Knowledge App")
    st.markdown("---")
    st.markdown("### Settings")
    system_prompt = st.text_area("System Prompt", "You are a helpful AI assistant. You will answer questions based on the provided context.", height=150)
    use_rag = st.toggle("Enable RAG (Qdrant)", value=True)
    temperature = st.slider("Temperature", 0.0, 1.0, 0.7)
    
    if st.button("Clear Chat History"):
        st.session_state.messages = []
        st.rerun()

st.title("Knowledge Chat")
st.caption("A modern AI platform interface powered by your Homelab RAG pipeline.")

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

def query_rag(prompt: str) -> str:
    \"\"\"Simulated or real RAG query function.\"\"\"
    context = ""
    try:
        # 1. Fetch from embedding API
        # res = requests.post(EMBEDDING_API_URL, json={"query": prompt, "top_k": 5})
        # docs = res.json().get("documents", [])
        
        # 2. Rerank the documents
        # rerank_res = requests.post(RERANKER_API_URL, json={"query": prompt, "documents": docs})
        # final_docs = rerank_res.json().get("reranked_documents", [])
        
        # context = "\n\n".join(final_docs)
        
        # Placeholder for real integration
        context = "Context from RAG goes here..."
    except Exception as e:
        st.error(f"RAG Error: {e}")
    return context

def generate_response(prompt: str, context: str):
    \"\"\"Stream response from vLLM.\"\"\"
    messages = [{"role": "system", "content": system_prompt}]
    
    if use_rag and context:
        messages[0]["content"] += f"\n\nContext:\n{context}"
        
    for msg in st.session_state.messages:
        messages.append({"role": msg["role"], "content": msg["content"]})
        
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": "casperhansen/llama-3-70b-instruct-awq", # Default or fallback
        "messages": messages,
        "temperature": temperature,
        "stream": True,
        "max_tokens": 4096
    }
    
    try:
        # Assuming vLLM OpenAI compatible API
        response = requests.post(VLLM_API_URL, json=payload, stream=True)
        response.raise_for_status()
        
        for line in response.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    line = line[6:]
                if line == '[DONE]':
                    break
                try:
                    data = json.loads(line)
                    if "choices" in data and len(data["choices"]) > 0:
                        delta = data["choices"][0].get("delta", {})
                        if "content" in delta:
                            yield delta["content"]
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        yield f"Error communicating with vLLM: {e}"

if prompt := st.chat_input("Ask something about your data..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    context = ""
    if use_rag:
        with st.spinner("Searching knowledge base..."):
            context = query_rag(prompt)

    with st.chat_message("assistant"):
        response_stream = generate_response(prompt, context)
        full_response = st.write_stream(response_stream)
        
    st.session_state.messages.append({"role": "assistant", "content": full_response})
