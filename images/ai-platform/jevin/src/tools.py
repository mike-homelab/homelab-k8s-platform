import os
import subprocess
from smolagents import tool

GITHUB_PAT = os.environ.get("GITHUB_PAT")
GITHUB_USERNAME = os.environ.get("GITHUB_USERNAME")
REPO_OWNER = os.environ.get("REPO_OWNER", "mike-homelab")
REPO_NAME = os.environ.get("REPO_NAME", "homelab-k8s-platform")
WORKSPACE_DIR = "/workspace"

# Tool methods
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
    """The ultimate fallback tool for high-level advice."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key: return "Error: External API Key not configured."
    return "Simulated Gemini Response..."

@tool
def replace_file_content(path: str, target: str, replacement: str) -> str:
    """Replaces a specific string match inside a file with new contents."""
    try:
        with open(path, 'r') as f: content = f.read()
        if target not in content: return f"Error: Target string not found in {path}"
        with open(path, 'w') as f: f.write(content.replace(target, replacement))
        return f"Successfully replaced content in {path}."
    except Exception as e: return f"Error replacing content: {str(e)}"

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
        subprocess.run(f'git push "{repo_url}" {branch_name}', shell=True, cwd=WORKSPACE_DIR, check=True)
        
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
