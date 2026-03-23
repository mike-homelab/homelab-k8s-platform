from typing import Optional
from pydantic import BaseModel

class ChatRequest(BaseModel):
    prompt: str
    system_prompt: Optional[str] = None

class ChatResponse(BaseModel):
    response: str

class OpenAIModel(BaseModel):
    id: str
    object: str = "model"
    created: int = 1677610602
    owned_by: str = "jevin"

class OpenAIModelsResponse(BaseModel):
    object: str = "list"
    data: list[OpenAIModel]

class OpenAIMessage(BaseModel):
    role: str
    content: str

class OpenAIChatRequest(BaseModel):
    model: str = "jevin"
    messages: list[OpenAIMessage]
    stream: bool = False

class OpenAIChatChoice(BaseModel):
    index: int = 0
    message: OpenAIMessage
    finish_reason: str = "stop"

class OpenAIChatResponse(BaseModel):
    id: str = "chatcmpl-jevin"
    object: str = "chat.completion"
    created: int = 1677610602
    model: str = "jevin"
    choices: list[OpenAIChatChoice]
