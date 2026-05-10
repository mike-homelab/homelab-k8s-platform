import os
import subprocess
from pathlib import Path

class GitManager:
    def __init__(self, workspace_root: str = "/home/workspace"):
        self.workspace_root = Path(workspace_root)

    def clone_repo(self, repo_url: str, branch: str = "main"):
        """Clone a repository into the workspace root."""
        try:
            # If workspace is not empty, we might need to handle it.
            # For MVP, assume it's clean or we use subdirectories.
            result = subprocess.run(
                ["git", "clone", "-b", branch, repo_url, "."],
                cwd=self.workspace_root,
                capture_output=True,
                text=True,
                check=True
            )
            return "Successfully cloned repository"
        except subprocess.CalledProcessError as e:
            return f"Error cloning repository: {e.stderr}"

    def create_branch(self, branch_name: str):
        try:
            subprocess.run(["git", "checkout", "-b", branch_name], cwd=self.workspace_root, check=True)
            return f"Created branch {branch_name}"
        except subprocess.CalledProcessError as e:
            return f"Error creating branch: {e.stderr}"

    def commit_changes(self, message: str):
        try:
            subprocess.run(["git", "add", "."], cwd=self.workspace_root, check=True)
            subprocess.run(["git", "commit", "-m", message], cwd=self.workspace_root, check=True)
            return "Changes committed"
        except subprocess.CalledProcessError as e:
            return f"Error committing changes: {e.stderr}"

    def push_changes(self, branch: str):
        try:
            subprocess.run(["git", "push", "origin", branch], cwd=self.workspace_root, check=True)
            return "Changes pushed to origin"
        except subprocess.CalledProcessError as e:
            return f"Error pushing changes: {e.stderr}"
