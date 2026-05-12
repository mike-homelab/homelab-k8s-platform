import os
import subprocess
from pathlib import Path
from typing import Dict, Any

class ToolExecutor:
    def __init__(self, workspace_root: str = "/home/workspace"):
        self.workspace_root = Path(workspace_root)

    def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        method = getattr(self, tool_name, None)
        if not method:
            return f"Error: Tool '{tool_name}' not found"
        try:
            return method(**args)
        except Exception as e:
            return f"Error executing {tool_name}: {str(e)}"

    def read_file(self, path: str) -> str:
        full_path = self.workspace_root / path
        if not full_path.exists():
            return f"Error: File {path} not found"
        return full_path.read_text()

    def write_file(self, path: str, content: str) -> str:
        full_path = self.workspace_root / path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        return f"Successfully wrote to {path}"

    def list_files(self, path: str = ".") -> str:
        full_path = self.workspace_root / path
        if not full_path.exists():
            return f"Error: Directory {path} not found"
        try:
            files = os.listdir(full_path)
            return "\n".join(files)
        except Exception as e:
            return f"Error listing {path}: {str(e)}"

    def search_files(self, query: str, path: str = ".") -> str:
        # Simple grep-like search
        full_path = self.workspace_root / path
        command = f"grep -rnE \"{query}\" {full_path}"
        return self.run_command(command)

    def run_command(self, command: str) -> str:
        # In MVP, this runs in the worker pod. In Phase 2+, it should run in a sandbox pod.
        try:
            result = subprocess.run(
                command, 
                shell=True, 
                cwd=self.workspace_root, 
                capture_output=True, 
                text=True, 
                timeout=300
            )
            return f"Stdout:\n{result.stdout}\nStderr:\n{result.stderr}"
        except subprocess.TimeoutExpired:
            return "Error: Command timed out"
        except Exception as e:
            return f"Error: {str(e)}"

    def run_tests(self, test_command: str = "pytest") -> Dict[str, Any]:
        """Execute tests and return structured results."""
        output = self.run_command(test_command)
        success = "failed" not in output.lower() and "error" not in output.lower()
        return {
            "success": success,
            "output": output
        }

    def browser_action(self, url: str, action: str = "screenshot") -> str:
        """Placeholder for Playwright browser automation."""
        # This will be implemented in Phase 4.5+ with a Chromium sidecar
        return f"Browser action '{action}' on {url} requested (Phase 4 skeleton)"
