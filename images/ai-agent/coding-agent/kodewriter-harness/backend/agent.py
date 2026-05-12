import json
from typing import List, Dict, Any
from .llm import react_call, web_search
from .tools import ToolExecutor
from .retrieval import RetrievalEngine

class KodewriterAgent:
    def __init__(self, workspace_root: str = "/home/workspace"):
        self.executor = ToolExecutor(workspace_root)
        self.retrieval = RetrievalEngine()
        self.tools_desc = """
- read_file(path: str): Read content of a file.
- write_file(path: str, content: str): Write content to a file.
- list_files(path: str): List files in a directory.
- search_files(query: str, path: str): Search for a string in files (grep).
- run_command(command: str): Run a shell command in the workspace.
- run_tests(test_command: str): Run tests (defaults to pytest).
- web_search(query: str): Search the internet for information.
"""

    async def run_task(self, task: str):
        history = []
        max_steps = 10
        
        yield {"type": "status", "content": "Initializing ReAct reasoning loop..."}
        
        for step in range(max_steps):
            # 1. Get next action from LLM
            response = react_call(task, history, self.tools_desc)
            
            # Parse Thought and Action
            thought = ""
            action_json = ""
            if "Thought:" in response:
                thought = response.split("Thought:")[1].split("Action:")[0].strip()
            if "Action:" in response:
                action_json = response.split("Action:")[1].strip()
            
            yield {"type": "thought", "content": thought}
            
            if not action_json:
                yield {"type": "status", "content": "Error: Agent failed to provide an action. Terminating."}
                break
            
            try:
                action = json.loads(action_json)
            except json.JSONDecodeError:
                # Try to extract JSON from block if present
                if "```json" in action_json:
                    action_json = action_json.split("```json")[1].split("```")[0].strip()
                    action = json.loads(action_json)
                else:
                    yield {"type": "status", "content": "Error: Failed to parse action JSON."}
                    break

            tool_name = action.get("tool")
            args = action.get("args", {})

            if tool_name == "final_answer":
                yield {"type": "final_answer", "content": args.get("content", "Task complete.")}
                break
            
            # 2. Execute Tool
            yield {"type": "action", "content": f"Executing {tool_name} with args {args}..."}
            
            result = ""
            if tool_name == "web_search":
                results = web_search(args.get("query", ""))
                result = "\n".join(results)
            else:
                result = self.executor.execute(tool_name, args)
            
            yield {"type": "observation", "content": result}
            
            # 3. Update History
            history.append({"role": "assistant", "content": response})
            history.append({"role": "system", "content": f"Observation: {result}"})

        else:
            yield {"type": "status", "content": "Reached maximum steps without final answer."}
