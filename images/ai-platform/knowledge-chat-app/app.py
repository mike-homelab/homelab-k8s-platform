import streamlit as st
import requests
import json
import os

st.set_page_config(
    page_title="Knowledge App",
    page_icon="✨",
    layout="centered",
    initial_sidebar_state="expanded"
)

VLLM_API_URL = os.getenv("VLLM_API_URL", "http://agent-api:8000/v1/chat/completions")

st.markdown("""
<style>
    /* Claude-like Beige/Earthy Theme */
    [data-testid="stAppViewContainer"] {
        background-color: #f7f5f0 !important;
        color: #3b3a36 !important;
    }
    [data-testid="stSidebar"] {
        background-color: #efece5 !important;
        border-right: 1px solid #dcd8cf;
    }
    header {visibility: hidden;}
    [data-testid="stChatMessage"] {
        background-color: transparent !important;
        border: none !important;
        padding: 1rem 0 !important;
    }
    [data-testid="stChatInput"] {
        background-color: #ffffff;
        border: 1px solid #dcd8cf;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.03);
    }
    .stMarkdown p {
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
        font-size: 1.05rem;
        line-height: 1.6;
        color: #3b3a36;
    }
</style>
""", unsafe_allow_html=True)

st.title("Knowledge App ✨")
st.markdown("*A Claude-like earthy interface connected to your Homelab RAG pipeline.*", unsafe_allow_html=True)

with st.sidebar:
    st.header("⚙️ Agent Settings")
    selected_llm = st.selectbox("Primary Model", ["homelab-rag", "casperhansen/llama-3-70b-instruct-awq"])
    st.caption(f"Backend strictly bound to Agent API: {VLLM_API_URL}")
    if st.button("Clear Conversation"):
        st.session_state.messages = []
        st.rerun()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

def generate_stream(prompt):
    payload = {
        "model": selected_llm,
        "messages": [{"role": m["role"], "content": m["content"]} for m in st.session_state.messages] + [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "stream": True,
        "max_tokens": 2048
    }
    try:
        res = requests.post(VLLM_API_URL, json=payload, stream=True, timeout=60)
        res.raise_for_status()
        for line in res.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data_str = line[6:]
                    if data_str.strip() == '[DONE]':
                        break
                    try:
                        data = json.loads(data_str)
                        if 'choices' in data and len(data['choices']) > 0:
                            delta = data['choices'][0].get('delta', {})
                            if 'content' in delta and delta['content']:
                                yield delta['content']
                    except Exception:
                        pass
    except requests.exceptions.RequestException as e:
        yield f"**API Error:** Failed to connect to the backend agent API. Error: {str(e)}"

if prompt := st.chat_input("Ask a question..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        response = st.write_stream(generate_stream(prompt))
        st.session_state.messages.append({"role": "assistant", "content": response})
