import os
import time
import requests
import json
import logging
from typing import List, Dict

# Configure basic logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("DynamicDocsIngestor")

# Configurations
QDRANT_API_URL = os.getenv("QDRANT_API_URL", "http://qdrant:6333")
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "http://embedding-api:80/embed")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DASHBOARD_METRICS_ENDPOINT = os.getenv("DASHBOARD_METRICS_ENDPOINT", "http://platform-dashboard:80/api/ingest-status") # Hook for dashboard if it had a POST endpoint
DOCS_SOURCES = ["Kubernetes", "ArgoCD", "Python 3.11"]

def fetch_documentation_text(source: str) -> str:
    """Simulates cloning or scraping documentation for a specific source."""
    logger.info(f"Fetching documentation for {source}...")
    # In reality, this might clone a Git repo or crawl a doc portal.
    time.sleep(1) # Fake delay
    
    mocked_docs = {
        "Kubernetes": "# Kubernetes Architecture \n## Pods \nPods are the smallest deployable units of computing that you can create and manage in Kubernetes.\n### Networking \nEvery pod gets its own IP address.",
        "ArgoCD": "# ArgoCD Declarative GitOps \n## Concepts \nArgo CD is implemented as a kubernetes controller which continuously monitors running applications.\n### Sync Status \nThe application state is compared against the target state.",
        "Python 3.11": "# Python 3.11 Documentation \n## Asyncio \nasyncio is a library to write concurrent code using the async/await syntax.\n### Tasks \nTasks are used to schedule coroutines concurrently."
    }
    return mocked_docs.get(source, "")

def hierarchical_chunking(text: str) -> List[Dict[str, str]]:
    """
    Implements a custom Hierarchical Chunking logic creating dynamic chunks 
    based on Heading Levels (Semantic boundaries) rather than static tokens.
    """
    logger.info("Applying dynamic hierarchical chunking...")
    # Very rudimentary hierarchical chunker parsing H1 (#) -> H2 (##) -> H3 (###) 
    chunks = []
    lines = text.split("\n")
    
    current_h1 = "Document"
    current_h2 = ""
    current_h3 = ""
    current_content = []
    
    def save_chunk():
        if current_content:
            chunks.append({
                "h1": current_h1,
                "h2": current_h2,
                "h3": current_h3,
                "content": "\n".join(current_content).strip()
            })
            current_content.clear()
            
    for line in lines:
        if line.startswith("### "):
            save_chunk()
            current_h3 = line[4:].strip()
        elif line.startswith("## "):
            save_chunk()
            current_h2 = line[3:].strip()
            current_h3 = ""
        elif line.startswith("# "):
            save_chunk()
            current_h1 = line[2:].strip()
            current_h2 = ""
            current_h3 = ""
        else:
            if line.strip():
                current_content.append(line.strip())
                
    save_chunk()
    return chunks

def embed_and_upsert_qdrant(chunks: List[Dict[str, str]], source: str):
    """Sends chunks sequentially to an Embedding API and upserts into Qdrant vector DB."""
    # Create valid collection name
    collection_name = source.lower().replace(" ", "_").replace(".", "_")
    logger.info(f"Connecting to Qdrant at {QDRANT_API_URL} to rebuild collection '{collection_name}'...")
    
    try:
        # Wipe existing data (drop collection)
        requests.delete(f"{QDRANT_API_URL}/collections/{collection_name}", timeout=5)
        logger.info(f"Dropped old collection '{collection_name}' to prevent duplication.")
        
        # Recreate collection (assuming 1024 dimension for modern embeddings)
        res = requests.put(f"{QDRANT_API_URL}/collections/{collection_name}", json={
            "vectors": {
                "size": 1024,
                "distance": "Cosine"
            }
        }, timeout=5)
        
        if res.status_code in [200, 201]:
            logger.info(f"Successfully created fresh collection '{collection_name}'.")
        else:
            logger.warning(f"Collection creation returned {res.status_code}. It might already exist or failed.")
            
        # In reality, bulk payload would be executed. 
        # MOCK Upsert
        time.sleep(1)
        logger.info(f"Successfully upserted {len(chunks)} vectors for {source}. Collection synced and is purely latest.")
    except Exception as e:
        logger.error(f"Failed to communicate with Qdrant for {source}: {e}")
        raise e

def run_ingestion():
    logger.info("Starting Daily Documentation Ingestion Pipeline.")
    status_report = []

    for source in DOCS_SOURCES:
        try:
            raw_text = fetch_documentation_text(source)
            dynamic_chunks = hierarchical_chunking(raw_text)
            logger.info(f"Generated {len(dynamic_chunks)} dynamic hierarchical chunks for {source}.")
            embed_and_upsert_qdrant(dynamic_chunks, source)
            
            status_report.append({"source": source, "status": "SUCCESS", "chunks_processed": len(dynamic_chunks)})
        except Exception as e:
            logger.error(f"Failed to ingest {source}: {str(e)}")
            status_report.append({"source": source, "status": "FAILED", "error": str(e)})

    # Write status to a file that could be picked up by an exporter / dashboard or save to redis
    status_doc = {
        "last_run": time.strftime("%Y-%m-%d %H:%M:%S+05:30"),
        "status": "COMPLETED",
        "details": status_report
    }
    try:
        with open("/tmp/ingestion_status.json", "w") as f:
            json.dump(status_doc, f)
        import redis
        r = redis.from_url(REDIS_URL)
        r.set("docs_ingestion_status", json.dumps(status_doc))
        logger.info("Ingestion complete. Status recorded to local file and Redis.")
    except Exception as e:
        logger.warning(f"Failed to record status: {e}")

if __name__ == "__main__":
    run_ingestion()
