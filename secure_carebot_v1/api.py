"""
api.py — SecureCareBot RAG API
================================
Pipeline per request:
  1. Resolve patient name → ID via MongoDB (HMAC hash lookup)
  2. Hybrid vector search in Milvus (dense + sparse)
  3. BGE reranker — re-scores top candidates, trims to top_k
  4. Blockchain audit log — records access event, blocks if tampered
  5. Stream LLM response token by token
"""

import json
import logging
import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Annotated

import jwt
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Depends, Security
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from rag.audit_chain import AuditChain, AuditChainTamperError
from rag.llm import LLMForQuery, ollama_chat
from rag.med_embedding_ollama import MedEmbedderOllama
from rag.milvus_db import MilvusDB, MilvusSearchAndRetrieval
from rag.mongo_db import MongoDB, MongoDBSearchAndRetrieval
from rag.reranker import BGEReranker

load_dotenv()

# ── Configuration ──────────────────────────────────────────────────────────────
MILVUS_URL      = "http://localhost:19530"
MILVUS_DB_NAME  = "securecarebot_db"
COLLECTION_NAME = "meddataollama"

MONGO_URL        = os.getenv("MONGO_URL", "mongodb://localhost:27017/")
MONGO_DB_NAME    = "securecarebot"
MONGO_COLLECTION = "patients"

QUERY_MODEL        = "qwen2.5:1.5b-instruct-q4_K_M"
CHAT_MODEL         = "phi3.5:3.8b-mini-instruct-q4_K_M"
DEFAULT_LIMIT      = 10
RERANK_FETCH_LIMIT = 15   # candidates fetched from hybrid before reranking
RERANK_TOP_K       = 8    # survivors passed to the LLM after reranking

JWT_SECRET    = os.getenv("JWT_SECRET", "")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")

if not JWT_SECRET:
    raise EnvironmentError("JWT_SECRET is not set in .env")

# ── Active stream registry ─────────────────────────────────────────────────────
# Maps stream_id → asyncio.Event. Setting the event signals the stream to stop.
_active_streams: dict[str, asyncio.Event] = {}

# ── Security ───────────────────────────────────────────────────────────────────
bearer_scheme = HTTPBearer()

def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Security(bearer_scheme)]
) -> dict:
    try:
        return jwt.decode(credentials.credentials, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def require_permission(permission: str):
    def _checker(current_user: dict = Depends(get_current_user)) -> dict:
        if permission not in current_user.get("permissions", []):
            raise HTTPException(status_code=403, detail=f"Missing permission: {permission}")
        return current_user
    return _checker


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.info(" Booting SecureCareBot RAG API...")
    MilvusDB(MILVUS_URL, MILVUS_DB_NAME, COLLECTION_NAME, MedEmbedderOllama())
    MongoDB(MONGO_URL, MONGO_DB_NAME, MONGO_COLLECTION)
    AuditChain(mongo_url=MONGO_URL, db_name=MONGO_DB_NAME)
    yield


app = FastAPI(title="SecureCareBot ZK-RAG API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)


# ── Schemas ────────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=1000)
    stream_id: str | None = Field(default=None)   # client-supplied cancellation token


# ── Context extractor ─────────────────────────────────────────────────────────

def _extract_context(results: list) -> str:
    """Extracts plain text from reranked Milvus hits."""
    hits = results[0] if results and isinstance(results[0], list) else []
    fragments = []
    for hit in hits:
        try:
            entity = hit.entity if hasattr(hit, "entity") else hit.get("entity", {})
            text   = entity.get("text", "")
            if text:
                fragments.append(text)
        except Exception:
            continue
    return "\n".join(fragments) if fragments else "No records found."


# ── Streaming chat endpoint ────────────────────────────────────────────────────

@app.post("/chat/stream")
async def chat_stream(
    body: ChatRequest,
    current_user: dict = Depends(require_permission("view_patients")),
):
    user_query = body.query.strip()

    # Use client-supplied stream_id or generate one
    stream_id  = body.stream_id or str(uuid.uuid4())
    stop_event = asyncio.Event()
    _active_streams[stream_id] = stop_event

    # Step 1 — Resolve patient name → ID via MongoDB
    query_helper = LLMForQuery(model_name=QUERY_MODEL)
    patient_ids, visit_ids, clean_query = MongoDBSearchAndRetrieval().get_patient_id_by_name(
        user_query, query_helper
    )

    if not patient_ids:
        async def _not_found():
            yield f"data: {json.dumps({'error': 'Patient record not found.'})}\n\n"
        _active_streams.pop(stream_id, None)
        return StreamingResponse(_not_found(), media_type="text/event-stream")

    # Step 2 — Classify query into chunk types
    attributes = query_helper.extract_chunk_types(user_query)
    print(attributes)

    # Step 3 — Hybrid search: fetch more candidates than needed for reranking
    searcher       = MilvusSearchAndRetrieval()
    hybrid_results = searcher.search_hybrid(
        query=clean_query,
        patient_id=patient_ids,
        visit_id=visit_ids if visit_ids else None,
        limit=RERANK_FETCH_LIMIT,
        attribute=attributes,
    )

    # Step 4 — Rerank: re-score with BGE and trim to top_k
    reranker         = BGEReranker()
    reranked_results = reranker.rerank(
        query=clean_query,
        results=hybrid_results,
        top_k=RERANK_TOP_K,
    )

    # Step 5 — Audit: verify chain integrity and record access event
    # If tampered, block the query entirely — no response generated
    audit = AuditChain()
    try:
        audit.log(
            query=user_query,
            patient_ids=patient_ids,
            chunk_types=attributes,
            session_id=current_user.get("sub"),
        )
    except AuditChainTamperError as e:
        logging.error(f"[AUDIT TAMPER] {e}")
        _active_streams.pop(stream_id, None)
        raise HTTPException(status_code=500, detail="Audit chain integrity violation. Query blocked.")

    # Step 6 — Build RAG prompt from reranked plain text context
    context_text = _extract_context(reranked_results)

    from rag.prompt_templates import RAG_CHAT_PROMPT_TEMPLATE
    full_prompt = RAG_CHAT_PROMPT_TEMPLATE.format(
        context=context_text,
        query=clean_query,
        length_limit="3 to 8 sentences",
    )
    # Step 7 — Stream LLM response with stop support
    async def token_stream() -> AsyncGenerator[str, None]:
        loop  = asyncio.get_event_loop()
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        # Send stream_id first so client can cancel by ID
        yield f"data: {json.dumps({'stream_id': stream_id})}\n\n"

        def _run_ollama():
            try:
                gen = ollama_chat(
                    model=CHAT_MODEL,
                    prompt=full_prompt,
                    temperature=0.2,
                    stream=True,
                )
                for chunk in gen:
                    # Check stop flag from the ollama thread
                    if stop_event.is_set():
                        break
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        asyncio.run_coroutine_threadsafe(queue.put(token), loop)
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(None), loop)

        loop.run_in_executor(None, _run_ollama)

        try:
            while True:
                # Poll queue while also watching the stop event
                try:
                    token = await asyncio.wait_for(queue.get(), timeout=0.1)
                except asyncio.TimeoutError:
                    if stop_event.is_set():
                        yield f"data: {json.dumps({'stopped': True})}\n\n"
                        break
                    continue

                if token is None:
                    yield f"data: {json.dumps({'done': True})}\n\n"
                    break

                if stop_event.is_set():
                    yield f"data: {json.dumps({'stopped': True})}\n\n"
                    break

                yield f"data: {json.dumps({'token': token})}\n\n"
        finally:
            _active_streams.pop(stream_id, None)

    return StreamingResponse(
        token_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )

@app.post("/chat/stop/{stream_id}")
async def stop_stream(
    stream_id: str,
    current_user: dict = Depends(require_permission("view_patients")),
):
    event = _active_streams.get(stream_id)
    if event:
        event.set()
        return {"stopped": True, "stream_id": stream_id}
    return {"stopped": False, "detail": "Stream not found or already finished."}

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "hybrid-rag-rerank-audit"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)