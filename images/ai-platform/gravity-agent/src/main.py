import os
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from smolagents import CodeAgent, HfApiModel, tool

app = FastAPI(title="Gravity Agent API")

# --------- Tools ---------

@tool
def read_file(path: str) -> str:
    """Reads the textual content of a file located at the absolute path.
    Args:
        path: The absolute path to the file.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {path}: {e}"

@tool
def list_dir(path: str) -> str:
    """Lists the contents of a directory.
    Args:
        path: The absolute path to the directory.
    """
    try:
        items = os.listdir(path)
        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory {path}: {e}"

@tool
def edit_code(path: str, instruction: str) -> str:
    """A powerful tool that uses the agent's intelligence to apply a change to a file.
    This tool should be used when you want to modify an existing file.
    Args:
        path: The absolute path to the file perfectly matching the workspace.
        instruction: A precise description of what to change in the file.
    """
    # In a full setup, this would use a localized diffing/editing algorithm or specialized LLM call.
    # For now, we will return a string prompting the agent to use run_bash to write the code natively.
    return (
        f"To edit {path}, please use the `run_bash` tool with python, sed, or standard bash echoed "
        f"commands to implement the following instruction: {instruction}"
    )

@tool
def run_bash(command: str) -> str:
    """Executes a bash command in the terminal and returns the standard output and standard error.
    Extremely useful for running tests, git commands, grepping, or creating files.
    Args:
        command: The raw bash command string to execute.
    """
    try:
        result = subprocess.run(
            command, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
    except Exception as e:
        return f"Error running command: {e}"

@tool
def ask_gemini(query: str) -> str:
    """The ultimate fallback tool. If you do not have the fundamental knowledge or context to answer
    a programming question, write a script, or fix a bug, use this tool to ask an external, higher-level AI.
    Args:
        query: A highly detailed question explaining what you need help with.
    """
    # In reality, this would make an HTTPS request to the Gemini API using an injected K8s secret.
    # For this scaffolding, we simulate the hook.
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: External API Key not configured in the pod environment."
    return "Simulated Gemini Response: Here is the code/answer you requested..."


# --------- Agent Setup ---------

# We connect smolagents to the existing homelab vLLM Coder endpoint.
vllm_base_url = os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")
model_id = os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-3B-Instruct")

# We mock the API key since vLLM doesn't require one internally, but the framework expects it.
os.environ["HF_TOKEN"] = "mock-token-for-vllm"

model = HfApiModel(
    model_id=model_id,
    provider="vllm", # custom provider string if using OpenAI compatible base_url in smolagents > 1.3
    base_url=vllm_base_url,
)

agent = CodeAgent(
    tools=[read_file, list_dir, edit_code, run_bash, ask_gemini], 
    model=model,
    add_base_tools=True
)


# --------- API Routes ---------

class ChatRequest(BaseModel):
    prompt: str

class ChatResponse(BaseModel):
    response: str

@app.post("/agent/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    try:
        sys_prompt = (
            "You are Gravity, an autonomous coding agent. You have access to the user's workspace "
            "at /workspace. Use your tools to read files, navigate, and execute bash commands to fulfill "
            f"the user's request: {req.prompt}"
        )
        result = agent.run(sys_prompt)
        return ChatResponse(response=str(result))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
