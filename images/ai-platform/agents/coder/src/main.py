from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import httpx
import os
import re
from mcp import ClientSession
from mcp.client.sse import sse_client

app = FastAPI(title="Coder Agent API", description="Specialized coding subagent natively utilizing RAG and MCP.")

class TaskRequest(BaseModel):
    prompt: str

class TaskResponse(BaseModel):
    result: str

VLLM_API_URL = os.getenv("VLLM_API_URL", "http://agent-api:8000/v1/chat/completions")
HOST_IP = os.getenv("HOST_IP", "10.0.1.1")
MCP_SSE_URL = f"http://{HOST_IP}:8080/sse"

SYSTEM_PROMPT = """You are a senior software engineer specialized in writing code and modifying files.
To write a file to the user's live system, you MUST output a raw exact TOOL invocation strictly enclosed in special XML brackets:
<TOOL_WRITE>{"path": "/clusters/...", "content": "..."}</TOOL_WRITE>
If you emit this, the backend will parse it, write the code via MCP, and return the execution success back to the user! Answer the logic efficiently before firing the tool!"""

async def execute_mcp_write(path: str, content: str) -> str:
    try:
        async with sse_client(MCP_SSE_URL) as (read, write):
            async with ClientSession(read, write) as mcp_session:
                await mcp_session.initialize()
                res = await mcp_session.call_tool("write_local_file", arguments={"path": path, "content": content})
                return str(res.content[0].text if res.content else "Success")
    except Exception as e:
        return f"MCP Exception Output: {e}"

@app.post("/task", response_model=TaskResponse)
async def handle_task(request: TaskRequest):
    payload = {
        "model": "casperhansen/llama-3-70b-instruct-awq",
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request.prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 4096
    }
    
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            res = await client.post(VLLM_API_URL, json=payload)
            res.raise_for_status()
            data = res.json()
            llm_text = data["choices"][0]["message"]["content"]
            
            # Simple REGEX parser hunting for MCP tooling requests
            import json
            match = re.search(r'<TOOL_WRITE>(.*?)</TOOL_WRITE>', llm_text, re.DOTALL)
            if match:
                try:
                    tool_args = json.loads(match.group(1).strip())
                    write_res = await execute_mcp_write(tool_args.get("path"), tool_args.get("content"))
                    return TaskResponse(result=f"Coder successfully identified and applied MCP modifications!\nLLM Summary:\n{llm_text}\n\nMCP System Check:\n{write_res}")
                except Exception as e:
                    return TaskResponse(result=f"JSON Tool Parsing failed internally! LLM response was standard:\n{llm_text}\nError: {e}")

            return TaskResponse(result=llm_text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM call strictly failed over RAG / Base Pipeline: {e}")

@app.get("/health")
def health_check():
    return {"status": "ok"}
