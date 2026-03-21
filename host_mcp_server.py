import os
from mcp.server.fastmcp import FastMCP

# Create an MCP server instance
mcp = FastMCP("Michael-Homelab")

# Determine the absolute path of the user's workspace
WORKSPACE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__)))

@mcp.tool()
def read_local_file(path: str) -> str:
    """Reads a file directly from the user's live local PC."""
    safe_path = os.path.abspath(os.path.join(WORKSPACE_DIR, path.lstrip("/")))
    if not safe_path.startswith(WORKSPACE_DIR):
        return "Error: Path traversal outside of workspace."
    try:
        with open(safe_path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file {path}: {e}"

@mcp.tool()
def write_local_file(path: str, content: str) -> str:
    """Writes to a file directly on the user's live local PC."""
    safe_path = os.path.abspath(os.path.join(WORKSPACE_DIR, path.lstrip("/")))
    if not safe_path.startswith(WORKSPACE_DIR):
        return "Error: Path traversal outside of workspace."
    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
    try:
        with open(safe_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file {path}: {e}"

@mcp.tool()
def list_local_dir(path: str = "") -> str:
    """Lists directory contents strictly on the local PC."""
    safe_path = os.path.abspath(os.path.join(WORKSPACE_DIR, path.lstrip("/")))
    if not safe_path.startswith(WORKSPACE_DIR):
        return "Error: Path traversal outside of workspace."
    try:
        items = os.listdir(safe_path)
        return "\n".join(items)
    except Exception as e:
        return f"Error listing directory {path}: {e}"

if __name__ == "__main__":
    print(f"[*] Starting Local FastMCP Server targeting {WORKSPACE_DIR}")
    print("[*] Jevin will securely connect to this server over SSE.")
    mcp.run(transport="sse", host="0.0.0.0", port=8080)
