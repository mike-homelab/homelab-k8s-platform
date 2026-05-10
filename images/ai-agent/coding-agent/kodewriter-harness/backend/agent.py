from typing import List, Dict, Any
from .llm import planner_call, coder_call, llm_call, MODEL_PLANNER
from .tools import ToolExecutor
from .retrieval import RetrievalEngine

class KodewriterAgent:
    def __init__(self, workspace_root: str = "/home/workspace"):
        self.executor = ToolExecutor(workspace_root)
        self.retrieval = RetrievalEngine()

    async def run_task(self, task: str):
        # 1. Retrieval
        context_docs = self.retrieval.search(task)
        context = "\n".join([d["payload"]["content"] for d in context_docs])
        
        # 2. Planning
        plan = planner_call(task, context)
        yield {"type": "plan", "content": plan}

        # 3. Execution Loop
        # For MVP, we'll just do one round of coding
        code_solution = coder_call(task, f"Plan:\n{plan}\n\nContext:\n{context}")
        yield {"type": "code", "content": code_solution}

        # 4. Validation
        yield {"type": "status", "content": "Running tests..."}
        test_result = self.executor.run_tests()
        
        if not test_result["success"]:
            # 5. Reflection
            yield {"type": "status", "content": "Tests failed. Reflecting..."}
            reflection_system = "You are a senior debugger. Analyze the test failure and suggest a fix."
            reflection_user = f"Task: {task}\n\nFailure:\n{test_result['output']}\n\nCode:\n{code_solution}"
            fix_suggestion = llm_call(MODEL_PLANNER, reflection_system, reflection_user)
            yield {"type": "reflection", "content": fix_suggestion}
            
            # Re-run coding with fix suggestion
            final_code = coder_call(task, f"Previous Attempt:\n{code_solution}\n\nFix Suggestion:\n{fix_suggestion}")
            yield {"type": "code", "content": final_code}
        else:
            yield {"type": "status", "content": "Tests passed!"}

    def _apply_patch(self, patch: str):
        # Placeholder for patch application logic
        pass
