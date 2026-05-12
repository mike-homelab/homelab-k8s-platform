import json
import asyncio
from typing import List, Dict, Any, TypedDict, Annotated, Union
import operator

from .llm import async_react_call, web_search
from .tools import ToolExecutor
from .retrieval import RetrievalEngine

# LangGraph imports (will be active after rebuild)
try:
    from langgraph.graph import StateGraph, END
except ImportError:
    StateGraph = None

class AgentState(TypedDict):
    task: str
    history: Annotated[List[Dict[str, str]], operator.add]
    current_thought: str
    current_action: Dict[str, Any]
    current_observation: str
    final_answer: str

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

    async def _call_model(self, state: AgentState):
        response = await async_react_call(state['task'], state['history'], self.tools_desc)
        
        thought = ""
        action_json = ""
        if "Thought:" in response:
            thought = response.split("Thought:")[1].split("Action:")[0].strip()
        if "Action:" in response:
            action_json = response.split("Action:")[1].strip()
        
        try:
            action = json.loads(action_json)
        except:
            if "```json" in action_json:
                action_json = action_json.split("```json")[1].split("```")[0].strip()
                action = json.loads(action_json)
            else:
                action = {"tool": "error", "args": {"message": "Failed to parse action JSON"}}

        return {
            "current_thought": thought,
            "current_action": action,
            "history": [{"role": "assistant", "content": response}]
        }

    async def _execute_tool(self, state: AgentState):
        action = state['current_action']
        tool_name = action.get("tool")
        args = action.get("args", {})

        if tool_name == "final_answer":
            return {"final_answer": args.get("content", "Task complete.")}
        
        if tool_name == "web_search":
            results = web_search(args.get("query", ""))
            observation = "\n".join(results)
        else:
            observation = self.executor.execute(tool_name, args)
        
        return {
            "current_observation": observation,
            "history": [{"role": "system", "content": f"Observation: {observation}"}]
        }

    async def run_task(self, task: str):
        yield {"type": "status", "content": "Initializing LangGraph reasoning engine..."}
        
        state: AgentState = {
            "task": task,
            "history": [],
            "current_thought": "",
            "current_action": {},
            "current_observation": "",
            "final_answer": ""
        }

        # Manual execution of the "Graph" for MVP stability, but structured as nodes
        max_steps = 10
        for i in range(max_steps):
            # Node 1: Call Model
            print(f"DEBUG: Starting step {i+1}")
            yield {"type": "status", "content": f"Step {i+1}: Reasoning..."}
            try:
                print(f"DEBUG: Calling model...")
                update = await self._call_model(state)
                print(f"DEBUG: Model returned: {update}")
            except Exception as e:
                print(f"DEBUG: Model call failed: {e}")
                yield {"type": "status", "content": f"Reasoning failed: {e}"}
                break
            
            state.update(update)
            state['history'].extend(update['history'])
            
            yield {"type": "thought", "content": state['current_thought']}
            
            if state['current_action'].get("tool") == "final_answer":
                yield {"type": "final_answer", "content": state['current_action']['args'].get("content")}
                break
            
            if state['current_action'].get("tool") == "error":
                yield {"type": "status", "content": f"Error: {state['current_action']['args'].get('message')}"}
                break

            # Node 2: Execute Tool
            yield {"type": "action", "content": f"Action: {state['current_action']['tool']}"}
            update = await self._execute_tool(state)
            
            if "final_answer" in update:
                 yield {"type": "final_answer", "content": update['final_answer']}
                 break
                 
            state.update(update)
            state['history'].extend(update['history'])
            yield {"type": "observation", "content": state['current_observation']}

        else:
            yield {"type": "status", "content": "Reached maximum reasoning depth."}
