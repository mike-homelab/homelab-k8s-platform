import uuid
from typing import Optional
from pydantic import BaseModel, Field

# chat sub models
class ChatRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = Field(default="general", pattern="^(general|coder)$")
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, ge=1, le=2048)
    session_id: str | None = None


class ChatResponse(BaseModel):
    model: str
    text: str


class RagIndexRequest(BaseModel):
    text: str = Field(..., min_length=1)
    doc_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    collection: str = "rag-docs"
    chunk_size: int = Field(default=900, ge=200, le=4000)
    chunk_overlap: int = Field(default=120, ge=0, le=1000)


class RagIndexResponse(BaseModel):
    collection: str
    doc_id: str
    chunks_indexed: int


class RagAskRequest(BaseModel):
    question: str = Field(..., min_length=1)
    model: str = Field(default="general", pattern="^(general|coder)$")
    collection: str = "rag-docs"
    top_k: int = Field(default=8, ge=1, le=64)
    page_k: int = Field(default=6, ge=2, le=20)
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=32, le=8192)
    service: str | None = None
    session_id: str | None = None


class RagSource(BaseModel):
    doc_id: str
    chunk_index: int
    score: float
    text: str
    url: str = ""
    collection: str = ""


class RagAskResponse(BaseModel):
    model: str
    answer: str
    sources: list[RagSource]
    tokens: int = 0


class QueryTelemetryRequest(BaseModel):
    timestamp: str


class QueryTelemetryResponse(BaseModel):
    tokens: float = 0.0
    gpu_cache: float = 0.0
    vram_used_mb: int = 0
    vram_free_mb: int = 0
    cpu_vllm: float = 0.0
    cpu_api: float = 0.0
    ram_vllm: float = 0.0
    ram_api: float = 0.0


class TriggerIngestorRequest(BaseModel):
    source: str = Field(..., pattern="^(aws|azure|kubernetes)$")


class TriggerIngestorResponse(BaseModel):
    source: str
    cronjob: str
    job_name: str


# OpenAI compatible models
class OAIMessage(BaseModel):
    role: str
    content: str


class OAIChatRequest(BaseModel):
    model: str = "homelab-rag"
    messages: list[OAIMessage]
    stream: bool = False
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=32, le=8192)
    top_k: int = Field(default=10, ge=1, le=64)
