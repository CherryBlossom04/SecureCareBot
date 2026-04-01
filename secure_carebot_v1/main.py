"""
main.py — SecureCareBot RAG pipeline entry point.

Pipeline stages (run in order on first setup, then comment out as needed):
  Stage 1 — Initialize databases          : initialize_databases()
  Stage 2 — Summarise & insert chunks     : create_and_insert_patient_summary()
  Stage 3 — Insert identity profiles      : insert_identity_profiles()
  Stage 4 — Query the system              : query_system()

Milvus web UI : http://127.0.0.1:9091/webui/collections
"""

from rag.audit_chain import AuditChain, AuditChainTamperError
from rag.decorators import load_json_data, print_result
from rag.llm import LLMForVector, LLMForQuery
from rag.med_embedding_ollama import MedEmbedderOllama
from rag.milvus_db import MilvusDB, MilvusDataInsertion, MilvusDataManipulation, MilvusSearchAndRetrieval
from rag.mongo_db import MongoDB, MongoDBDataInsertion, MongoDBManipulation, MongoDBSearchAndRetrieval
from rag.patient_data_to_chunks import PatientDataToChunksForQwen2
from rag.reranker import BGEReranker

# ── Configuration ─────────────────────────────────────────────────────────────

MILVUS_URL        = "http://localhost:19530"
MILVUS_DB_NAME    = "securecarebot_db"
COLLECTION_NAME   = "meddataollama"

MONGO_URL         = "mongodb://localhost:27017/"
MONGO_DB_NAME     = "securecarebot"
MONGO_COLLECTION  = "patients"

ACTIVE_LLM_MODEL = "qwen2.5:1.5b-instruct-q4_K_M"
CHAT_MODEL = "phi3.5:3.8b-mini-instruct-q4_K_M"

RERANK_FETCH_LIMIT = 15   # fetch more candidates from hybrid before reranking
RERANK_TOP_K       = 5    # how many to pass to the LLM after reranking

PATIENTS_JSON     = "datas/1_patients_data_json.json"
CHUNKS_JSON       = "datas/2_patients_data_chunks.json"

DEFAULT_LIMIT     = 10


# ── Stage 1: Initialise databases ─────────────────────────────────────────────

def initialize_databases() -> None:

    print("\n── Initialising databases ──────────────────────────────────────")

    MilvusDB(
        server_url=MILVUS_URL,
        db_name=MILVUS_DB_NAME,
        collection_name=COLLECTION_NAME,
        dense_embedder=MedEmbedderOllama(),
    )
    print("✅ Milvus ready.")

    MongoDB(
        url=MONGO_URL,
        db_name=MONGO_DB_NAME,
        collection_name=MONGO_COLLECTION,
    )
    print("✅ MongoDB ready.")

    AuditChain(
        mongo_url=MONGO_URL,
        db_name=MONGO_DB_NAME,
    )
    print("✅ Audit chain ready.")

def reset_milvus() -> None:
    """Clears all vectors from the Milvus collection (keeps schema)."""
    print("\n── Resetting Milvus collection ─────────────────────────────────")
    MilvusDataManipulation().clear_collection()
    MilvusDataManipulation().delete_collection(COLLECTION_NAME)


def reset_mongo() -> None:
    """Clears all documents from the MongoDB collection (keeps indexes)."""
    print("\n── Resetting MongoDB collection")
    MongoDBManipulation().clear_collection()
    MongoDBManipulation().delete_collection(MONGO_COLLECTION)


# ── Stage 2: Summarise patient data and insert into Milvus ───────────────────

def create_and_insert_patient_summary() -> list[dict]:
    raw_data = load_json_data(PATIENTS_JSON)
    print(f"\n── Processing data for {len(raw_data)} patients")

    converter = PatientDataToChunksForQwen2(raw_data)
    structured_chunks = converter.convert()

    if not structured_chunks:
        print("No valid chunks generated. Aborting.")
        return []

    print(f"── Summarising data with {ACTIVE_LLM_MODEL}")
    summarizer = LLMForVector.Qwen25(model_name=ACTIVE_LLM_MODEL)
    summarized_chunks = summarizer.summarize(structured_chunks)

    print("\n── Inserting medical chunks into Milvus ")
    MilvusDataInsertion(summarized_chunks)

    return summarized_chunks

def insert_identity_profiles(summarized_chunks: list[dict]) -> None:
    print("\n── Inserting identity profiles into MongoDB")
    if not summarized_chunks:
        print("No chunks available to insert into MongoDB.")
        return
    MongoDBDataInsertion().insert_profile_chunks(summarized_chunks)

def insert_prebuilt_chunks() -> None:
    print("\nInserting pre-built chunks into Milvus ")
    MilvusDataInsertion(load_json_data(CHUNKS_JSON))


def query_system(query: str, limit: int = DEFAULT_LIMIT, session_id: str | None = None):
    print(f"\n── Query: {query!r} ")

    query_helper = LLMForQuery(model_name=ACTIVE_LLM_MODEL)
    searcher     = MilvusSearchAndRetrieval()
    reranker     = BGEReranker()
    audit        = AuditChain()

    patient_ids, visit_id, clean_query = MongoDBSearchAndRetrieval().get_patient_id_by_name(query, query_helper)
    if not patient_ids:
        print("No patient found.")

    attributes = query_helper.extract_chunk_types(query)
    print(attributes)
    print(clean_query)

    hybrid_results = searcher.search_hybrid(
        query=query,
        patient_id=patient_ids,
        visit_id=visit_id,
        limit=RERANK_FETCH_LIMIT,
        attribute=attributes,
    )

    # Step 2 — Rerank: re-score with BGE and trim to top_k
    reranked_results = reranker.rerank(
        query=clean_query,
        results=hybrid_results,
        top_k=RERANK_TOP_K,
    )

    print_result(reranked_results)

    # Step 3 — Audit: record event before generating response
    # If chain is tampered, block the query entirely
    try:
        audit.log(
            query=query,
            patient_ids=patient_ids,
            chunk_types=attributes,
            session_id=session_id,
        )
    except AuditChainTamperError as e:
        print(f"\n🚨 AUDIT CHAIN TAMPER DETECTED — query blocked.\n{e}")
        return None

    # Step 4 — Generate response from reranked context
    chat_helper = LLMForQuery(model_name=CHAT_MODEL)
    answer = chat_helper.generate_chat_response(
        query=clean_query,
        search_results=reranked_results,
    )

    return answer



def main() -> None:
    initialize_databases()

    # reset_milvus()
    # reset_mongo()

    # chunks = create_and_insert_patient_summary()
    # insert_identity_profiles(load_json_data(CHUNKS_JSON))   # uses Qwen 2.5 to summarise
    # insert_prebuilt_chunks()              # skip LLM — load from disk directly

    query_system(
        query="What is the symptoms for Arun Kumar as of his most recent visit?",
        limit=DEFAULT_LIMIT,
    )


if __name__ == "__main__":
    main()