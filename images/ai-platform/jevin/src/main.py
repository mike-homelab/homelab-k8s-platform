import os
import subprocess
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from smolagents import CodeAgent, OpenAIServerModel, tool

app = FastAPI(title="Jevin Agent API", version="1.0.3")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
REPO_OWNER = "mike-homelab"
REPO_NAME = "homelab-k8s-platform"
WORKSPACE_DIR = "/workspace"

# On boot, clone the repository into the ephemeral volume
if GITHUB_PAT and GITHUB_USERNAME:
    if not os.path.exists(os.path.join(WORKSPACE_DIR, ".git")):
        print(f"[*] Cloning repository to {WORKSPACE_DIR}")
        repo_url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}.git"
        try:
            subprocess.run(["git", "clone", repo_url, WORKSPACE_DIR], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as e:
            # Mask the PAT in the output for security
            safe_url = repo_url.replace(GITHUB_PAT, "***")
            stderr_msg = e.stderr.replace(GITHUB_PAT, "***") if e.stderr else "No stderr returned."
            raise RuntimeError(f"Git clone failed (exit {e.returncode}) for {safe_url}. STDERR: {stderr_msg}")
else:
    print("[!] Warning: GITHUB_PAT or GITHUB_USERNAME is missing. GitOps features may fail.")

def generate_repo_map(root_dir='/workspace', max_depth=2):
    """Generates a compressed folder tree of the workspace."""
    if not os.path.exists(root_dir):
        return "Workspace is empty."
    tree = "Workspace Architecture Map:\n"
    for root, dirs, files in os.walk(root_dir):
        # Filter out noisy directories
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'venv', 'dist', '__pycache__', 'build')]
        depth = root[len(root_dir):].count(os.sep)
        if depth >= max_depth:
            dirs.clear()
            continue
        indent = '  ' * depth
        folder = os.path.basename(root)
        if depth == 0:
            tree += f"/\n"
        else:
            tree += f"{indent}{folder}/\n"
        # Optional: Add files if depth < max_depth
        if depth < max_depth:
            for f in files:
                if not f.startswith('.') and not f.endswith(('.pyc', '.png', '.jpg', '.jpeg', '.pdf', '.bin')):
                    tree += f"{indent}  {f}\n"
    return tree

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
def write_file(path: str, content: str) -> str:
    """Writes the given textual content to a file at the absolute path. Overwrites the file if it exists.
    Args:
        path: The absolute path to the file.
        content: The textual content to be written to the file.
    """
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to file {path}"
    except Exception as e:
        return f"Error writing file {path}: {e}"

@tool
def run_bash(command: str) -> str:
    """Executes a bash command in the terminal and returns the standard output and standard error.
    Extremely useful for running tests, git commands, grepping, or creating files.
    Args:
        command: The raw bash command string to execute.
    """
    try:
        result = subprocess.run(
            command, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=WORKSPACE_DIR
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
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Error: External API Key not configured in the pod environment."
    return "Simulated Gemini Response: Here is the code/answer you requested..."

@tool
def create_pull_request(title: str, body: str, branch_name: str) -> str:
    """Creates a git commit of all current changes, pushes them to a new branch, and opens a GitHub Pull Request.
    This should be your FINAL step after you have modified all the necessary files to fulfill the user's request.
    Args:
        title: A short summary of the Pull Request changes.
        body: A longer description explaining what bugs were fixed or features added.
        branch_name: A new branch name safely formatted (e.g., feature/update-agent-logic)
    """
    if not GITHUB_PAT or not GITHUB_USERNAME:
        return "Error: GitOps credentials are missing."

    try:
        # Configure Git
        subprocess.run(f"git config --global user.email '{GITHUB_USERNAME}@users.noreply.github.com'", shell=True, cwd=WORKSPACE_DIR)
        subprocess.run(f"git config --global user.name '{GITHUB_USERNAME} AI Agent'", shell=True, cwd=WORKSPACE_DIR)
        
        # Branch, Commit, Push
        subprocess.run(f"git checkout -B {branch_name}", shell=True, cwd=WORKSPACE_DIR, check=True)
        subprocess.run("git add .", shell=True, cwd=WORKSPACE_DIR, check=True)
        subprocess.run(f"git commit -m '{title}'", shell=True, cwd=WORKSPACE_DIR, check=True)
        
        repo_url = f"https://{GITHUB_USERNAME}:{GITHUB_PAT}@github.com/{REPO_OWNER}/{REPO_NAME}.git"
        subprocess.run(f"git push {repo_url} {branch_name}", shell=True, cwd=WORKSPACE_DIR, check=True)
        
        # Open PR using GitHub REST API
        import httpx
        pr_data = {
            "title": title,
            "body": body,
            "head": branch_name,
            "base": "main"
        }
        headers = {
            "Authorization": f"token {GITHUB_PAT}",
            "Accept": "application/vnd.github.v3+json"
        }
        resp = httpx.post(
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/pulls",
            json=pr_data,
            headers=headers
        )
        if resp.status_code == 201:
            return f"Successfully created Pull Request! URL: {resp.json().get('html_url')}"
        else:
            return f"Failed to create PR. HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        return f"Error during GitOps PR flow: {e}"

@tool
def ask_local_mcp(tool_name: str, arguments: dict) -> str:
    """A tool that proxies execution back to the user's remote PC via Model Context Protocol (MCP).
    Use this to read, write, or list live files on the user's actual desktop if GitOps is too slow!
    Args:
        tool_name: The name of the MCP tool (e.g. "read_local_file", "write_local_file", "list_local_dir").
        arguments: The dictionary arguments required by the tool (e.g., {"path": "src/main.py"}).
    """
    import asyncio
    try:
        from mcp import ClientSession
        from mcp.client.sse import sse_client
    except ImportError:
        return "Error: mcp sdk not installed in container"
        
    host_ip = os.getenv("HOST_IP", "127.0.0.1")
    url = f"http://{host_ip}:8080/sse"

    async def _fetch():
        async with sse_client(url) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                res = await session.call_tool(tool_name, arguments)
                return res.content[0].text
                
    try:
        return asyncio.run(_fetch())
    except Exception as e:
        return f"MCP Connection Error to {url}: {e}"

# --------- Agent Setup ---------

# We connect smolagents to the existing homelab vLLM Coder endpoint.
vllm_base_url = os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")
model_id = os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-3B-Instruct")

# We mock the API key since vLLM doesn't require one internally, but the framework expects it.
os.environ["HF_TOKEN"] = "mock-token-for-vllm"

model = OpenAIServerModel(
    model_id=model_id,
    api_base=vllm_base_url,
    api_key="mock-token-for-vllm"
)

agent = CodeAgent(
    tools=[read_file, list_dir, write_file, run_bash, ask_gemini, ask_local_mcp, create_pull_request], 
    model=model,
    add_base_tools=False,
    additional_authorized_imports=["statistics", "math", "re", "unicodedata", "datetime", "queue", "time", "itertools", "stat", "random", "collections", "os"]
)


# --------- API Routes ---------

class ChatRequest(BaseModel):
    prompt: str

class ChatResponse(BaseModel):
    response: str

# OpenAI-Compatible Models
class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int = 1677610602
    owned_by: str = "jevin"

class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]

class OpenAIMessage(BaseModel):
    role: str
    content: str

class OpenAIChatRequest(BaseModel):
    model: str = "jevin"
    messages: list[OpenAIMessage]
    stream: bool = False

class OpenAIChatChoice(BaseModel):
    index: int = 0
    message: OpenAIMessage
    finish_reason: str = "stop"

class OpenAIChatResponse(BaseModel):
    id: str = "chatcmpl-jevin"
    object: str = "chat.completion"
    created: int = 1677610602
    model: str = "jevin"
    choices: list[OpenAIChatChoice]

@app.get("/v1/models", response_model=OpenAIModelsResponse)
async def list_models():
    return OpenAIModelsResponse(data=[OpenAIModel(id="jevin")])

@app.post("/v1/chat/completions", response_model=OpenAIChatResponse)
def openai_chat_endpoint(req: OpenAIChatRequest):
    # Extract the last user message as the task for Jevin
    last_msg = next((m.content for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg:
        raise HTTPException(status_code=400, detail="No user message found")
    
    try:
        # 1. Sync the workspace with latest GitOps changes so the agent always
        # sees what the user most recently pushed from their local PC!
        if os.path.exists(os.path.join(WORKSPACE_DIR, ".git")):
            subprocess.run("git fetch && git reset --hard origin/main && git clean -fd", shell=True, cwd=WORKSPACE_DIR)
            
        repo_map = generate_repo_map()
            
        sys_prompt = (
            "You are Jevin, an autonomous coding agent. You have access to the user's workspace "
            "at /workspace. Your goal is to fulfill the user's request by modifying files in the codebase.\n\n"
            f"{repo_map}\n\n"
            "When you are finished completing the user's edits, you MUST use the `create_pull_request` tool "
            f"to persist your work to GitHub! Request: {last_msg}"
        )
        # We run the agent synchronously for now as smolagents .run is blocking
        result = agent.run(sys_prompt)
        
        return OpenAIChatResponse(
            choices=[
                OpenAIChatChoice(
                    message=OpenAIMessage(role="assistant", content=str(result))
                )
            ]
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/agent/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    try:
        if os.path.exists(os.path.join(WORKSPACE_DIR, ".git")):
            subprocess.run("git fetch && git reset --hard origin/main && git clean -fd", shell=True, cwd=WORKSPACE_DIR)
            
        repo_map = generate_repo_map()
        
        sys_prompt = (
            "You are Jevin, an autonomous coding agent. You have access to the user's workspace "
            "at /workspace. Your goal is to fulfill the user's request by modifying files in the codebase.\n\n"
            f"{repo_map}\n\n"
            "When you are finished completing the user's edits, you MUST use the `create_pull_request` tool "
            f"to persist your work to GitHub! Request: {req.prompt}"
        )
        result = agent.run(sys_prompt)
        return ChatResponse(response=str(result))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "ok"}
