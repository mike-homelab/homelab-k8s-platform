import uuid
from typing import List, Dict, Any
from .qdrant_manager import QdrantManager
from .llm import get_embedding, rerank

class RetrievalEngine:
    def __init__(self, collection_name: str = "kodewriter_index"):
        self.qdrant = QdrantManager(collection_name)

    def index_file(self, path: str, content: str):
        # Basic chunking by lines for now
        chunks = self._chunk_content(content)
        points = []
        for i, chunk in enumerate(chunks):
            vector = get_embedding(chunk)
            points.append({
                "id": str(uuid.uuid4()),
                "vector": vector,
                "payload": {
                    "path": path,
                    "chunk_index": i,
                    "content": chunk
                }
            })
        self.qdrant.upsert_points(points)

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query_vector = get_embedding(query)
        initial_results = self.qdrant.search(query_vector, limit=limit)
        
        # Reranking
        docs = [r["payload"]["content"] for r in initial_results]
        reranked = rerank(query, docs, top_n=5)
        
        final_results = []
        for r in reranked:
            idx = r["index"]
            final_results.append(initial_results[idx])
            
        return final_results

    def _chunk_content(self, content: str, chunk_size: int = 1000) -> List[str]:
        # Simple character-based chunking for MVP
        return [content[i:i+chunk_size] for i in range(0, len(content), chunk_size)]
