from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer
import os
import time

app = FastAPI(title="Custom Reranker Service", version="1.0.0")

# Load model and tokenizer
MODEL_ID = os.getenv("MODEL_ID", "BAAI/bge-reranker-v2-m3")
device = "cuda" if torch.cuda.is_available() else "cpu"

print(f"[*] Loading model {MODEL_ID} on {device}...")
tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
model.to(device)
model.eval()
print("[*] Model loaded successfully.")

class RerankRequest(BaseModel):
    query: str
    texts: list[str]
    raw_scores: bool = False
    return_text: bool = False

@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "device": device}

@app.post("/rerank")
def rerank(req: RerankRequest):
    if not req.texts:
        return []

    try:
        # Prepare pairs for sequence classification
        pairs = [[req.query, text] for text in req.texts]
        
        with torch.no_grad():
            inputs = tokenizer(
                pairs, 
                padding=True, 
                truncation=True, 
                return_tensors="pt", 
                max_length=1024
            ).to(device)
            
            outputs = model(**inputs)
            # bge-reranker returns raw logits, usually shape (batch, 1) or just logits
            scores = outputs.logits.view(-1).float()
            
            # Apply sigmoid to get probabilities if desired, or keep raw
            # TEI usually returns sigmoid scores for classification
            scores = torch.sigmoid(scores).cpu().numpy().tolist()

        # Create response format expected by agent-api
        results = []
        for i, score in enumerate(scores):
            item = {"index": i, "score": score}
            if req.return_text:
                item["text"] = req.texts[i]
            results.append(item)

        # Sort by score in descending order
        results.sort(key=lambda x: x["score"], reverse=True)
        return results

    except Exception as e:
        print(f"Rerank error: {e}")
        raise HTTPException(status_code=500, detail=f"Reranking failed: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
