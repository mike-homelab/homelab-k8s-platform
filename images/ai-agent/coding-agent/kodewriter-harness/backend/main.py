from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
from typing import List, Optional, Dict
import asyncio
import json
import uuid

from .agent import KodewriterAgent

app = FastAPI(title="Kodewriter Harness API")
agent = KodewriterAgent()

class Session(BaseModel):
    id: str
    status: str
    workspace: str

class Message(BaseModel):
    role: str
    content: str

sessions: Dict[str, Session] = {}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "kodewriter-harness"}

@app.post("/api/session")
async def create_session(workspace: str = "default"):
    session_id = str(uuid.uuid4())
    session = Session(id=session_id, status="starting", workspace=workspace)
    sessions[session_id] = session
    return session

@app.get("/api/session/{session_id}")
async def get_session(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id]

@app.websocket("/api/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    if session_id not in sessions:
        await websocket.close(code=4004)
        return
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            async for event in agent.run_task(message.get("content")):
                await websocket.send_text(json.dumps({
                    "type": "agent_event",
                    **event
                }))
    except WebSocketDisconnect:
        print(f"Session {session_id} disconnected")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
