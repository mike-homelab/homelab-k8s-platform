import os
import subprocess
from fastapi import APIRouter, HTTPException
from .models import OpenAIChatRequest, OpenAIChatResponse, OpenAIChatChoice, OpenAIMessage, OpenAIModelsResponse, OpenAIModel, ChatRequest, ChatResponse
from .agent_setup import agent
from .tools import WORKSPACE_DIR

router = APIRouter()

def generate_repo_map(root_dir=WORKSPACE_DIR, max_depth=1):
    import os
    if not os.path.exists(root_dir):
        return "Workspace is empty."
    tree = "Workspace Architecture Map:\n"
    for root, dirs, files in os.walk(root_dir):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('node_modules', 'venv', 'dist', '__pycache__', 'build')]
        depth = root[len(root_dir):].count(os.sep)
        if depth >= max_depth:
            dirs.clear()
            continue
        indent = '  ' * depth
        if depth == 0: tree += "/\n"
        else: tree += f"{indent}{os.path.basename(root)}/\n"
        if depth < max_depth:
            for f in files:
                if not f.startswith('.') and not f.endswith(('.pyc', '.png', '.jpg', '.jpeg', '.pdf', '.bin')):
                    tree += f"{indent}  {f}\n"
    return tree


@router.get("/v1/models", response_model=OpenAIModelsResponse)
async def list_models():
    return OpenAIModelsResponse(data=[OpenAIModel(id="jevin")])

@router.post("/v1/chat/completions", response_model=OpenAIChatResponse)
def openai_chat_endpoint(req: OpenAIChatRequest):
    last_msg = next((m.content for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg: raise HTTPException(status_code=400, detail="No user message found")
    
    custom_sys = next((m.content for m in req.messages if m.role == "system"), None)
    
    try:
        if os.path.exists(os.path.join(WORKSPACE_DIR, ".git")):
            try: subprocess.run("git fetch && git reset --hard origin/main && git clean -fd", shell=True, cwd=WORKSPACE_DIR, timeout=15)
            except: pass
            
        repo_map = generate_repo_map()
        
        if custom_sys:
            sys_prompt = f"{custom_sys}\n\nRequest: {last_msg}"
        else:
            sys_prompt = (
                "You are Jevin, an expert Full-Stack Developer and Kubernetes Infrastructure Specialist. \n"
                f"{repo_map}\n\nRequest: {last_msg}"
            )
        result = agent.run(sys_prompt)
        return OpenAIChatResponse(choices=[OpenAIChatChoice(message=OpenAIMessage(role="assistant", content=str(result)))])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/agent/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest):
    try:
        repo_map = generate_repo_map()
        sys_prompt = f"{req.system_prompt if req.system_prompt else 'You are Jevin Agent'}\n\n{repo_map}\n\nRequest: {req.prompt}"
        result = agent.run(sys_prompt)
        return ChatResponse(response=str(result))
    except Exception as e:
         raise HTTPException(status_code=500, detail=str(e))
