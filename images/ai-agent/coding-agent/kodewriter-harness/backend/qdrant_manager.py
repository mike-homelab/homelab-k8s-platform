import os
from qdrant_client import QdrantClient
from qdrant_client.http import models
from typing import List, Dict, Any

QDRANT_HOST = os.getenv("QDRANT_HOST", "qdrant.ai-platform.svc")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))

class QdrantManager:
    def __init__(self, collection_name: str = "kodewriter_index"):
        self.client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)
        self.collection_name = collection_name
        self._ensure_collection()

    def _ensure_collection(self):
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)
        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE),
            )

    def upsert_points(self, points: List[Dict[str, Any]]):
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                models.PointStruct(
                    id=p["id"],
                    vector=p["vector"],
                    payload=p["payload"]
                ) for p in points
            ]
        )

    def search(self, vector: List[float], limit: int = 10) -> List[Dict[str, Any]]:
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=vector,
            limit=limit
        )
        return [r.dict() for r in results]
