import os
from smolagents import CodeAgent, OpenAIServerModel
from .tools import read_file, list_dir, write_file, replace_file_content, run_bash, ask_gemini, create_pull_request

vllm_base_url = os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")
model_id = os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-3B-Instruct")

os.environ["HF_TOKEN"] = "mock-token-for-vllm"

model = OpenAIServerModel(
    model_id=model_id,
    api_base=vllm_base_url,
    api_key="mock-token-for-vllm"
)

agent = CodeAgent(
    tools=[read_file, list_dir, write_file, replace_file_content, run_bash, ask_gemini, create_pull_request], 
    model=model,
    add_base_tools=False,
    additional_authorized_imports=["json", "yaml", "statistics", "math", "re", "unicodedata", "datetime", "queue", "time", "itertools", "stat", "random", "collections", "os"]
)
