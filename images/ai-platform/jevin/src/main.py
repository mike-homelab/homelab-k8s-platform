import os
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from smolagents import CodeAgent, HfApiModel, tool

app = FastAPI(title="Jevin Agent API")

GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
REPO_NAME = "homelab-k8s-platform"
WORKSPACE_DIR = "/workspace"

# On boot, clone the repository into the ephemeral volume
if GITHUB_PAT and GITHUB_USERNAME:
    if not os.path.exists(os.path.join(WORKSPACE_DIR, ".git")):
        print(f"[*] Cloning repository to {WORKSPACE_DIR}")
        repo_url = f"https://{GITHUB_USERNAME}:{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
        subprocess.run(["git", "clone", repo_url, WORKSPACE_DIR], check=True)
else:
    print("[!] Warning: GITHUB_PAT or GITHUB_USERNAME is missing. GitOps features may fail.")

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
        subprocess.run(f"git checkout -b {branch_name}", shell=True, cwd=WORKSPACE_DIR, check=True)
        subprocess.run("git add .", shell=True, cwd=WORKSPACE_DIR, check=True)
        subprocess.run(f"git commit -m '{title}'", shell=True, cwd=WORKSPACE_DIR, check=True)
        
        repo_url = f"https://{GITHUB_USERNAME}:{GITHUB_PAT}@github.com/{GITHUB_USERNAME}/{REPO_NAME}.git"
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
            f"https://api.github.com/repos/{GITHUB_USERNAME}/{REPO_NAME}/pulls",
            json=pr_data,
            headers=headers
        )
        if resp.status_code == 201:
            return f"Successfully created Pull Request! URL: {resp.json().get('html_url')}"
        else:
            return f"Failed to create PR. HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        return f"Error during GitOps PR flow: {e}"

# --------- Agent Setup ---------

# We connect smolagents to the existing homelab vLLM Coder endpoint.
vllm_base_url = os.getenv("VLLM_CODER_BASE_URL", "http://vllm-coder.ai-platform.svc.cluster.local:8000/v1")
model_id = os.getenv("VLLM_CODER_MODEL_ID", "Qwen/Qwen2.5-Coder-7B-Instruct")

# We mock the API key since vLLM doesn't require one internally, but the framework expects it.
os.environ["HF_TOKEN"] = "mock-token-for-vllm"

model = HfApiModel(
    model_id=model_id,
    provider="vllm", # custom provider string if using OpenAI compatible base_url in smolagents > 1.3
    base_url=vllm_base_url,
)

agent = CodeAgent(
    tools=[read_file, list_dir, edit_code, run_bash, ask_gemini, create_pull_request], 
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
            "You are Jevin, an autonomous coding agent. You have access to the user's workspace "
            "at /workspace. Your goal is to fulfill the user's request by modifying files in the codebase. "
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
