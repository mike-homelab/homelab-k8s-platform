from typing import List, Dict, Any
from .llm import planner_call, coder_call, llm_call, MODEL_PLANNER, web_search
from .tools import ToolExecutor
from .retrieval import RetrievalEngine

class KodewriterAgent:
    def __init__(self, workspace_root: str = "/home/workspace"):
        self.executor = ToolExecutor(workspace_root)
        self.retrieval = RetrievalEngine()

    async def run_task(self, task: str):
        # 0. Start Langfuse Trace
        from .llm import langfuse_client
        root_trace = None
        if langfuse_client:
            root_trace = langfuse_client.trace(
                name="kodewriter-task",
                input=task
            )

        # 1. Retrieval (Local + Web)
        yield {"type": "status", "content": "Analyzing request and searching local workspace..."}
        context_docs = self.retrieval.search(task)
        
        yield {"type": "status", "content": "Researching on the internet for additional context..."}
        web_docs = web_search(task)
        
        local_context = "\n".join([d["payload"]["content"] for d in context_docs])
        web_context = "\n".join(web_docs)
        
        context = f"""
### LOCAL WORKSPACE CONTEXT
{local_context if local_context else "No relevant local files found."}

### WEB RESEARCH CONTEXT
{web_context if web_context else "No relevant web information found."}
"""
        
        # 2. Planning
        yield {"type": "status", "content": "Formulating execution plan..."}
        plan = planner_call(task, context, parent=root_trace)
        yield {"type": "plan", "content": plan}

        # 3. Execution Loop
        code_solution = coder_call(task, f"Plan:\n{plan}\n\nContext:\n{context}", parent=root_trace)
        yield {"type": "code", "content": code_solution}
        
        # 4. Apply Changes
        yield {"type": "status", "content": "Applying changes to workspace..."}
        self._apply_patch(code_solution)

        # 4. Validation
        yield {"type": "status", "content": "Running tests..."}
        test_result = self.executor.run_tests()
        
        if not test_result["success"]:
            # 5. Reflection
            yield {"type": "status", "content": "Tests failed. Reflecting..."}
            reflection_system = "You are a senior debugger. Analyze the test failure and suggest a fix."
            reflection_user = f"Task: {task}\n\nFailure:\n{test_result['output']}\n\nCode:\n{code_solution}"
            fix_suggestion = llm_call(MODEL_PLANNER, reflection_system, reflection_user, parent=root_trace)
            yield {"type": "reflection", "content": fix_suggestion}
            
            # Re-run coding with fix suggestion
            final_code = coder_call(task, f"Previous Attempt:\n{code_solution}\n\nFix Suggestion:\n{fix_suggestion}", parent=root_trace)
            yield {"type": "code", "content": final_code}
            if root_trace:
                root_trace.update(output=final_code)
        else:
            yield {"type": "status", "content": "Tests passed!"}
            if root_trace:
                root_trace.update(output=code_solution)
        
        if langfuse_client:
            langfuse_client.flush()

    def _apply_patch(self, code_solution: str):
        """Rudimentary parser to extract code blocks and write them to disk."""
        # In a real scenario, this would use a more robust parser or XML tags
        if "```" in code_solution:
            parts = code_solution.split("```")
            for i in range(1, len(parts), 2):
                content = parts[i]
                # Skip language identifier
                lines = content.split("\n")
                if lines[0] and not lines[0].strip().startswith(" "):
                    # Check if the first line is likely a language tag (e.g., python)
                    if any(lang in lines[0].lower() for lang in ["python", "javascript", "typescript", "yaml", "html", "css"]):
                        content = "\n".join(lines[1:])
                
                # For MVP, we assume the agent provides a filename in the first line of the block
                # or we just write it to a default location if not found.
                self.executor.write_file("generated_code.py", content)
