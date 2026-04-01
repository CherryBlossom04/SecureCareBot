# SecureCareBot — Process Documentation

## Table of Contents
1. [Module Reference](#module-reference)
2. [decorators.py](#decoratorspy)
3. [encrypt_decrypt.py](#encrypt_decryptpy)
4. [med_embedding_ollama.py](#med_embedding_ollamapy)
5. [patient_data_to_chunks.py](#patient_data_to_chunkspy)
6. [prompt_templates.py](#prompt_templatespy)
7. [llm.py](#llmpy)
8. [milvus_db.py](#milvus_dbpy)
9. [mongo_db.py](#mongo_dbpy)
10. [reranker.py](#rerankerpy)
11. [audit_chain.py](#audit_chainpy)
12. [audit_test.py](#audit_testpy)
13. [main.py](#mainpy)
14. [api.py](#apipy)
15. [auth.py](#authpy)
16. [Configuration Reference](#configuration-reference)
17. [Environment Variables](#environment-variables)
18. [Error Handling Guide](#error-handling-guide)
19. [Extending the System](#extending-the-system)

---

## Module Reference

| Module | Role | Key Dependency |
|---|---|---|
| `decorators.py` | Utility functions (singleton, JSON I/O, result printing) | stdlib only |
| `encrypt_decrypt.py` | Per-patient key derivation, Fernet encrypt/decrypt, HMAC hashing, PII anonymization | `cryptography`, `python-dotenv` |
| `med_embedding_ollama.py` | Dense vector embedding via Ollama | `ollama` |
| `patient_data_to_chunks.py` | Decompose raw patient JSON into typed chunk dicts | stdlib only |
| `prompt_templates.py` | All LLM prompt templates and category constants | stdlib only |
| `llm.py` | LLM wrappers for summarization, name extraction, query decomposition, chat | `ollama` |
| `milvus_db.py` | Milvus connection, schema creation, data insertion, hybrid search | `pymilvus` |
| `mongo_db.py` | MongoDB connection, encrypted PII insertion, HMAC-based name lookup | `pymongo` |
| `reranker.py` | BGE cross-encoder reranker: re-scores hybrid results by cosine similarity | `ollama`, `numpy` |
| `audit_chain.py` | Tamper-evident blockchain audit log stored in MongoDB | `pymongo`, stdlib |
| `audit_test.py` | Test suite validating audit chain tamper detection (6 scenarios) | `pymongo`, `audit_chain` |
| `main.py` | Pipeline entry point and orchestration | All above |
| `api.py` | FastAPI RAG streaming endpoint with JWT auth, reranking, and audit logging | `fastapi`, `uvicorn` |
| `auth.py` | FastAPI auth service: 2FA login, RBAC, forgot-password | `fastapi`, `bcrypt`, `jwt` |

---

## decorators.py

### Purpose

A collection of standalone utility functions used across the pipeline. Contains no business logic — pure infrastructure helpers.

### Functions

#### `singleton(cls)`

A class decorator that enforces single-instance creation. Uses a module-level `instances` dict keyed by class type. If the class is already instantiated, the existing instance is returned without calling `__init__` again.

**Used on:** `MilvusDB`, `MongoDB`

**Behavior:** Thread-safety is not explicitly enforced. In a single-process FastAPI deployment with a startup lifespan, this is sufficient.

#### `dump_to_json(data, path)`

Serializes any JSON-serializable Python object to a file using `json.dump` with `indent=4`.

**Validation:** Raises `ValueError` if `path` is not a non-empty string, or if `data` is `None`.

**Used for:** audit logs, intermediate pipeline output (`2_patients_data_chunks.json`, `4_patients_personal_data_audit.json`).

#### `print_result(results)`

Pretty-prints Milvus search results to stdout. Expects `results[0]` to be a list of hit dicts, each having `distance` (float) and `entity.text` (str). Handles the empty result case gracefully.

#### `load_json_data(file)`

Loads and returns Python objects from a JSON file using `json.load()`.

**Validation:** Raises `ValueError` if `file` is not a non-empty string.

---

## encrypt_decrypt.py

### Purpose

Implements the zero-trust storage layer. All cryptographic operations are centralized here. No other module performs encryption/decryption directly.

### Design Decisions

1. Milvus BM25 requires plaintext in `text` so `text` is anonymized but not encrypted.
2. Milvus `text_enc` stores the Fernet-encrypted original text for retrieval.
3. MongoDB stores only encrypted PII — the LLM never receives it.
4. Keys are derived per-patient — compromising one key exposes only one patient.
5. Names are never stored; HMAC-SHA256 of the name is used for lookup.

### Master Secret

Loaded from the environment variable `SCB_MASTER_SECRET`. If absent, a startup `EnvironmentError` is raised with instructions to generate one:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### Functions

#### `_derive_key(patient_id) -> Fernet`

**Private.** Derives a deterministic, unique Fernet key for the given `patient_id` using PBKDF2-HMAC-SHA256 with the master secret as the password and the patient_id (lowercased) as the salt. 100,000 iterations meets the NIST SP 800-132 recommended minimum. The resulting 32-byte key is base64url-encoded and wrapped in a `Fernet` instance.

The same `patient_id + master_secret` always yields the same key. No key storage is needed anywhere.

#### `anonymise_text(text) -> str`

Applies each `_PII_PATTERNS` regex in order, replacing matches with placeholder tokens. Returns the sanitized string.

**PII Patterns:**

| Regex | Replacement |
|---|---|
| 10-digit phone, optional +91 prefix | `[PHONE]` |
| Email address | `[EMAIL]` |
| DD/MM/YYYY or DD-MM-YYYY | `[DOB]` |
| YYYY-MM-DD or YYYY/MM/DD | `[DOB]` |
| Street address with road keywords | `[ADDRESS]` |
| Aadhaar (4-4-4 digit groups) | `[NATIONAL_ID]` |

Patient names are NOT automatically stripped. The system uses Patient IDs in clinical text. The RAG prompt's privacy shield handles residual cases.

#### `encrypt_patient_data(plain_text, patient_id) -> str`

Derives the Fernet key for `patient_id`, encrypts the UTF-8-encoded text, returns ciphertext as a UTF-8 string. Returns `""` for empty input without error.

#### `decrypt_patient_data(encrypted_text, patient_id) -> str`

Derives the same Fernet key and decrypts. Raises `ValueError` wrapping `InvalidToken` if decryption fails. The API layer catches this and skips the hit silently.

#### `hash_text(text) -> str`

Computes HMAC-SHA256 of the lowercased text using the master secret as the HMAC key. Returns a 64-character hex digest. Used for hashing patient names before storing in MongoDB and for re-hashing query-extracted names during lookup.

#### `generate_and_save_key() -> bytes`

Legacy utility shim. Returns a new `Fernet.generate_key()` for initial setup or testing.

---

## med_embedding_ollama.py

### Purpose

Provides dense vector embeddings for text using a local Ollama model. Abstracts the embedding interface behind `IMedEmbedding` so the implementation can be swapped.

### Classes

#### `IMedEmbedding` (ABC)

Abstract base class with three required methods: `get_dims()`, `encode()`, and `get_med_embedding_model()`. Constructor validates that both `model_name` and `device` are non-empty strings.

#### `MedEmbedderOllama(IMedEmbedding)`

Concrete implementation using Ollama.

**Default model:** `qwen3-embedding:0.6b`
**Embedding dimension:** 1024 (class constant `EMBEDDING_DIM`)
**Device:** `cpu` (CUDA is disabled via `os.environ["CUDA_VISIBLE_DEVICES"] = ""`)

`encode(chunks)` validates input, then loops over each text calling `ollama.embeddings(model=..., prompt=text)`, returning a `list[list[float]]` — one 1024-dim vector per input string. Raises `ValueError` for whitespace-only strings.

Encoding is sequential (one Ollama call per chunk). For large ingestion batches, batching support could improve performance if the model API supports it.

---

## patient_data_to_chunks.py

### Purpose

Pure data transformation: takes raw patient JSON dicts and produces a nested structure of typed chunk dicts ready for summarization.

### Functions

`_is_empty(value)` returns `True` for `None`, empty strings, empty lists, and empty dicts.

`clean_data(data)` recursively removes empty values from nested dicts and lists.

`add_chunk(chunks, chunk_name, chunk_data)` calls `clean_data()` then only adds to `chunks` if there are useful keys beyond `Patient ID` and `Visit ID`, preventing empty chunks from being summarized.

### Classes

#### `IPatientDataToChunks` (ABC)

Abstract base. Constructor validates `patients` is a non-empty list. Subclasses implement `convert() -> dict[str, dict]`.

#### `PatientDataToChunksForQwen2(IPatientDataToChunks)`

`convert()` iterates each patient and produces typed chunks:

| Chunk Type | Source Fields |
|---|---|
| `profile_identity` | Name, DOB, Address, Phone, Email |
| `profile_risk` | Allergies, Hereditary Diseases |
| `visit_overview_{visit_id}` | Date, BP, Sugar, Weight, Height, Pulse, Temperature |
| `visit_symptoms_{visit_id}` | Symptoms list |
| `visit_medication_{visit_id}` | Current medications |
| `visit_blood_report_{visit_id}` | HbA1c, CBC, lipids, electrolytes, etc. |
| `visit_scan_reports_{visit_id}` | MRI, CT, X-Ray, Ultrasound, ECG, etc. |
| `history_overview_{visit_id}` | Past vitals |
| `history_symptoms_{visit_id}` | Past symptoms |
| `history_prev_medication_{visit_id}` | Past medications |
| `history_diagnosis_{visit_id}` | Past diagnoses |
| `history_treatment_{visit_id}` | Past treatment plan and diet |

Returns `{ patient_id: { chunk_type: chunk_data_dict } }`.

---

## prompt_templates.py

### Purpose

Central repository for all LLM prompt templates and the valid chunk category set. No logic — only string constants.

### Templates

#### `SUMMARIZE_PROMPT_TEMPLATE`

Converts structured medical JSON into a dense semantic clinical sentence. Constraints: always start with Patient ID/Visit ID/Date; no conversational filler; no inferences; keep clinical terms isolated.

Variables: `{header}`, `{data}`

#### `SPLIT_QUERY_PROMPT_TEMPLATE`

Decomposes a user query into 2-3 targeted search strings. Schema: profile+history first, symptoms second, diagnosis/treatment third. Output is newline-separated, no numbering.

Variable: `{query}`

#### `EXTRACT_NAME_PROMPT_TEMPLATE`

Extracts full patient names from a query. Returns comma-separated names or `NONE`. Rules: strip possessives, no brackets/quotes/Markdown, proper capitalization, no IDs or titles.

Variable: `{query}`

#### `EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE`

Maps a user query to valid chunk category names. Enforces temporal rules: "current/now/today" returns only `visit_*` categories; "past/history/previous" returns only `history_*`; "compare/trend/change" returns both. Returns comma-separated category names.

Variable: `{query}`

#### `RAG_CHAT_PROMPT_TEMPLATE`

Final RAG response generation. Paragraph 1 is a clinical synthesis narrative. Paragraph 2 is treatment/medication suggestions only if explicitly requested. If PII is requested, returns `"Permission not given."` Response limited to `{length_limit}` sentences.

Variables: `{context}`, `{query}`, `{length_limit}`

### `VALID_CHUNK_CATEGORIES`

A `frozenset` of all 11 valid chunk category strings. Used to validate LLM output — any string not in this set is discarded.

```
visit_symptoms, visit_medication, visit_overview, visit_blood_report,
visit_scan_reports, history_symptoms, history_prev_medication,
history_diagnosis, history_treatment, history_overview, profile_risk
```

---

## llm.py

### Purpose

All LLM interaction logic. Low-level `ollama_chat()` function and higher-level classes for summarization and query operations.

### `ollama_chat(model, prompt, temperature, num_predict, stream)`

Wrapper around `ollama.chat()`. Passes a single user message. Returns the content string when `stream=False`, or an iterable generator of chunk dicts when `stream=True`.

### Classes

#### `ILLMForVector` (ABC)

Abstract base for LLM-based summarization. Constructor accepts `model_name` and optional `patients` list (defaults to `[]` if `None`). Subclasses implement `summarize_chunk()` and `summarize()`.

#### `LLMforSummarization(ILLMForVector)`

Implements `summarize(structured_chunks)`. Iterates `patient_id -> chunk_type -> chunk_data`, calls `summarize_chunk()` for each, collects results into `list[dict]`, and dumps to `datas/2_patients_data_chunks.json`.

#### `LLMForVector.Qwen25(LLMforSummarization)`

Concrete summarizer. `summarize_chunk()` formats `SUMMARIZE_PROMPT_TEMPLATE` and calls `ollama_chat(..., temperature=0.5)`.

#### `LLMForQuery`

Query-time LLM operations (not extending `ILLMForVector`).

**`extract_name(query)`:** Calls LLM with `EXTRACT_NAME_PROMPT_TEMPLATE`, returns `None` if result is `"none"`.

**`extract_chunk_types(query)`:** Calls LLM with `EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE`, filters to valid categories, falls back to `["general"]`.

**`split_query(query)`:** Calls LLM with `SPLIT_QUERY_PROMPT_TEMPLATE`, returns raw newline-separated result.

**`generate_chat_response(search_results, query, length_limit)`:** Extracts text from Milvus hits, builds context, formats `RAG_CHAT_PROMPT_TEMPLATE`, calls `ollama_chat(..., stream=True)`. Streams tokens to stdout and accumulates full response string.

---

## milvus_db.py

### Purpose

All Milvus operations: connection management, collection schema creation, vector insertion, and hybrid search.

### Classes

#### `MilvusDB` (singleton)

**Initialization:** Connects to Milvus, creates database if absent, creates collection with full schema if absent.

**Schema fields:**

| Field | Type | Purpose |
|---|---|---|
| `chunk_id` | VARCHAR(512), primary key | Unique per chunk |
| `patient_id` | VARCHAR(64), indexed | Owner reference |
| `chunk_type` | VARCHAR(128), indexed | Category filter |
| `visit_id` | VARCHAR(64), indexed | Visit filter |
| `text` | VARCHAR(65535), analyzer enabled | BM25 plaintext (anonymized) |
| `text_enc` | VARCHAR(65535) | Fernet ciphertext for retrieval |
| `dense_vector` | FLOAT_VECTOR(1024) | Cosine similarity search |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | BM25 keyword search |

**Indexes:** HNSW on `dense_vector` (COSINE), SPARSE_INVERTED_INDEX on `sparse_vector` (BM25), scalar indexes on `patient_id`, `chunk_type`, `visit_id`.

#### `MilvusDataManipulation`

`clear_collection()` deletes all entities. `delete_collection(name)` drops the collection.

#### `MilvusDataInsertion`

Constructor accepts `summarized_chunks: list[dict]` and immediately processes each:
1. `anonymise_text()` on text
2. `encrypt_patient_data()` on original text
3. `MedEmbedderOllama.encode()` for dense vector
4. `BM25EmbeddingFunction` for sparse vector
5. `client.insert(collection_name, row_dict)`

Per-chunk insert errors are caught and logged; pipeline continues.

#### `MilvusSearchAndRetrieval`

**`search_dense()`:** Embeds query, runs cosine search on `dense_vector` with patient/chunk/visit filters.

**`search_sparse()`:** Computes BM25 sparse vector for query, runs keyword search on `sparse_vector`.

**`search_hybrid()`:** Issues both searches as `AnnSearchRequest` objects, calls `client.hybrid_search()` with `RRFRanker()` (Reciprocal Rank Fusion). Returns fused top-N hits.

**Filter builder:**
```
patient_id in [...]
AND chunk_type in [...]    (if attributes provided)
AND visit_id in [...]      (if visit_ids provided)
```

---

## mongo_db.py

### Purpose

All MongoDB operations. Stores and retrieves encrypted PII. Provides HMAC-based name lookup that never exposes the actual patient name.

### Classes

#### `MongoBase`

Shared base. Constructor initializes the `MongoDB` singleton, verifies db is ready, resolves collection name, stores direct collection reference.

#### `MongoDB` (singleton)

Creates `MongoClient` with 5-second timeout, pings admin to verify connectivity, stores db handle. `require_db()` and `require_collection_name()` raise exceptions if not initialized.

#### `MongoDBManipulation(MongoBase)`

`list_collections()`, `clear_collection(name)`, `delete_collection(name)`.

#### `MongoDBDataInsertion(MongoBase)`

**`insert_profile_chunks(json_data)`:** Processes `profile_identity` chunks only. Extracts name via LLM, cleans to lowercase, hashes with HMAC, encrypts text with Fernet, inserts document. Saves audit log without `text_enc`. Skips duplicates.

Document stored:
```json
{
  "patient_id": "P00011",
  "chunk_type": "profile_identity",
  "name_hash": "<64-char hex>",
  "text_enc": "<fernet-ciphertext>"
}
```

#### `MongoDBSearchAndRetrieval(MongoBase)`

**`extract_and_clean_query(name_to_id_map, query)`:** Substitutes patient names with IDs in query string. Names sorted longest-first to prevent partial match. Regex pattern handles possessives.

**`get_patient_id_by_name(query, query_helper)`:** Full name resolution pipeline:
1. Regex-extract Visit IDs (`\bV\d{5,10}\b`) and Patient IDs (`\b[pP]\d{3,}\b`)
2. LLM extracts names, clean, split by comma
3. Filter out strings matching patient ID pattern
4. For each name: normalize lowercase, `hash_text()`, `MongoDB.find({"name_hash": hash})`
5. Build `name_to_id_map`, substitute in query
6. Return `(patient_ids: list, visit_ids: list, cleaned_query: str)`

---

## reranker.py

### Purpose

Re-scores the top candidates returned by the hybrid Milvus search using a BGE cross-encoder model before they are passed to the LLM. This ensures the LLM receives the most semantically relevant chunks rather than the raw RRF-fused ranking.

### Design Decisions

1. A separate rerank step allows fetching more candidates from Milvus (e.g., 15) and trimming to a smaller top-k (e.g., 8), improving precision without hurting recall.
2. Cosine similarity between query and document embeddings is used as the rerank score.
3. Results below `score_threshold` (default `0.0`) are filtered out entirely.
4. The `rerank_score` is attached to each hit's `entity` dict for downstream inspection and logging.

### Classes

#### `IReranker` (ABC)

Abstract base class with one required method: `rerank(query, results, top_k) -> list`.

#### `BGEReranker(IReranker)`

Concrete implementation using `qllama/bge-reranker-large:q4_k_m` via Ollama.

**Constructor parameters:**

| Parameter | Default | Description |
|---|---|---|
| `model_name` | `qllama/bge-reranker-large:q4_k_m` | Ollama model to use for scoring |
| `score_threshold` | `0.0` | Minimum cosine score; lower hits are discarded |

**`rerank(query, results, top_k)`**

1. Flattens the nested `[[hit, hit, ...]]` structure from hybrid search into a flat list of hits.
2. For each hit, extracts `entity.text` via `_extract_text()` and calls `_score()`.
3. Attaches `rerank_score` to `hit.entity` (or `hit["entity"]` for dict hits).
4. Filters out hits below `score_threshold`, sorts descending by score.
5. Returns the top `top_k` survivors wrapped in a list: `[top_hits]`.

**`_score(query, document) -> float`**

Calls `ollama.embed(model=..., input=[query, document])`, receives two embedding vectors, computes cosine similarity via `numpy`. Returns `0.0` on any exception.

**`_extract_text(hit) -> str`**

Static helper. Supports both Milvus hit objects (with `.entity` attribute) and plain dicts.

---

## audit_chain.py

### Purpose

Implements a tamper-evident blockchain audit log stored in MongoDB. Every query event is recorded as a block linked to the previous block via SHA-256 hash. Any modification, deletion, or injection of blocks is detected before the next write, blocking the query.

### Design Decisions

1. The audit log records query intent and patient IDs accessed — never the actual patient data.
2. Every `log()` call verifies the entire chain before writing. A tampered chain blocks all further writes.
3. The genesis block uses a fixed 64-zero `prev_hash` sentinel.
4. SHA-256 is computed over all fields except `block_hash` itself, with `sort_keys=True` for determinism.
5. The `@singleton` decorator ensures one chain instance per process.

### Exception

#### `AuditChainTamperError`

Raised when blockchain integrity verification fails. Caught in `api.py` to return HTTP 500 and block the query.

### Helper Functions

**`_get_local_ip() -> str`** — Best-effort local IP via a UDP socket probe to 8.8.8.8. Never raises.

**`_compute_hash(block_data) -> str`** — SHA-256 of all fields except `block_hash`, serialized with `json.dumps(sort_keys=True, default=str)`.

### Class `AuditChain` (singleton)

**Constructor parameters:**

| Parameter | Default | Description |
|---|---|---|
| `mongo_url` | `mongodb://localhost:27017/` | MongoDB connection URI |
| `db_name` | `securecarebot` | Database name |
| `collection_name` | `audit_chain` | Collection name (separate from `patients`) |

Creates unique index on `block_index`, and standard indexes on `session_id` and `timestamp`.

**Block document structure:**

| Field | Type | Description |
|---|---|---|
| `block_index` | int | Sequential, unique — gaps indicate deletion |
| `timestamp` | str (ISO 8601 UTC) | When the block was recorded |
| `session_id` | str | JWT `sub` (username) or UUID if absent |
| `hostname` | str | Server hostname at time of access |
| `ip_address` | str | Server IP at time of access |
| `query` | str | The original user query text |
| `chunk_types` | list[str] | Sorted, deduplicated chunk categories accessed |
| `prev_hash` | str | SHA-256 of the previous block |
| `block_hash` | str | SHA-256 of this block's content |

**`log(query, patient_ids, chunk_types, session_id) -> dict`**

1. Validates all inputs.
2. Calls `verify()` — raises `AuditChainTamperError` if chain is compromised.
3. Retrieves the latest block to get `prev_hash` and `block_index`.
4. Builds and hashes the new block.
5. Inserts into MongoDB and returns the block dict (without `_id`).

**`verify() -> bool`**

Loads all blocks sorted by `block_index`. For each block:
- Recomputes hash and compares against stored `block_hash` (detects field edits).
- Compares `prev_hash` against previous block's `block_hash` (detects insertion/deletion).

Returns `True` for an empty or valid chain. Raises `AuditChainTamperError` with a descriptive message identifying the violating block index.

**`get_audit_trail(session_id, patient_id, limit) -> list[dict]`**

Returns up to `limit` blocks (default 50) sorted newest-first. Supports filtering by `session_id` and/or `patient_id`.

**`chain_length() -> int`**

Returns the total number of blocks via `count_documents({})`.

**`_get_latest_block() -> dict | None`**

Returns the most recent block sorted by `block_index` descending, or `None` if the chain is empty.

---

## audit_test.py

### Purpose

Standalone test suite that validates all tamper-detection scenarios for `AuditChain`. Uses an isolated MongoDB collection (`audit_chain_test`) that is dropped before each test and cleaned up after all tests complete.

### Test Infrastructure

**`fresh_chain() -> AuditChain`** — Drops the test collection, bypasses the singleton by constructing `AuditChain` via `__new__`, and patches `_col` to point at the test collection. Returns a clean chain instance.

**`seed_chain(chain, n) -> None`** — Inserts `n` audit blocks with sequential test queries and patient IDs.

**`run_test(name, fn) -> None`** — Runs a test function, prints `PASSED` on success or `FAILED`/`UNEXPECTED ERROR` with details on failure.

### Test Cases

| # | Test | Tamper Method | Expected Detection |
|---|---|---|---|
| 1 | Clean chain passes verification | None (clean seed) | `verify()` returns `True` |
| 2 | Data edit detected | Overwrite `query` field in block #1 | Hash mismatch at block #1 |
| 3 | Hash forge detected | Edit data AND recompute hash in block #1 | Chain linkage break at block #2 |
| 4 | Block deletion detected | Delete block #2 from collection | `prev_hash` mismatch detected |
| 5 | Fake block injection detected | Insert block #99 with fabricated `prev_hash` | Linkage violation at block #99 |
| 6 | `log()` blocked after tamper | Corrupt block #0, then call `log()` | `log()` raises `AuditChainTamperError` |

**Run:**
```bash
python audit_test.py
```
Expected: all 6 tests print `PASSED`. The test collection is dropped automatically on completion.

---

## main.py

### Purpose

Pipeline orchestration entry point. All pipeline stages defined as top-level functions. Stages run sequentially on first setup, then commented out as data is persisted.

### Functions

`initialize_databases()` — boots both singletons, creates schemas.

`reset_milvus()` — clears and drops Milvus collection. Use before re-ingesting.

`reset_mongo()` — clears and drops MongoDB collection. Use before re-inserting identity profiles.

`create_and_insert_patient_summary()` — full ingestion: load JSON -> chunk -> LLM summarize -> insert Milvus. Returns `summarized_chunks` list.

`insert_identity_profiles(summarized_chunks)` — extracts profile_identity entries, hashes names, encrypts, inserts MongoDB.

`insert_prebuilt_chunks()` — loads pre-built chunks from JSON and inserts into Milvus directly, skipping LLM summarization.

`query_system(query, limit)` — full query: name resolution -> chunk type extraction -> hybrid search -> decrypt -> LLM stream. Returns full answer string.

---

## api.py

### Purpose

Production FastAPI service exposing the RAG pipeline as a streaming HTTP endpoint with JWT-based authentication, RBAC, BGE reranking, and blockchain audit logging.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `MILVUS_URL` | `http://localhost:19530` | Milvus endpoint |
| `MILVUS_DB_NAME` | `securecarebot_db` | Milvus database |
| `COLLECTION_NAME` | `meddataollama` | Milvus collection |
| `MONGO_URL` | `mongodb://localhost:27017/` | MongoDB URI |
| `MONGO_DB_NAME` | `securecarebot` | MongoDB database |
| `MONGO_COLLECTION` | `patients` | MongoDB collection |
| `QUERY_MODEL` | `qwen2.5:1.5b-instruct-q4_K_M` | Name extraction and chunk classification LLM |
| `CHAT_MODEL` | `phi3.5:3.8b-mini-instruct-q4_K_M` | Response generation LLM |
| `DEFAULT_LIMIT` | `10` | Default Milvus result limit |
| `RERANK_FETCH_LIMIT` | `15` | Candidates fetched from hybrid search before reranking |
| `RERANK_TOP_K` | `8` | Survivors passed to the LLM after reranking |
| `JWT_SECRET` | from `.env` (required) | JWT signing secret |

### Lifespan

`asynccontextmanager lifespan(app)` boots `MilvusDB`, `MongoDB`, and `AuditChain` singletons at startup.

### Security

`get_current_user(credentials)` decodes JWT using `JWT_SECRET`. Raises HTTP 401 on `PyJWTError`.

`require_permission(permission)` returns a FastAPI dependency checking `permission` in the JWT's `permissions` list. Raises HTTP 403 if not present.

### Stream Registry

`_active_streams: dict[str, asyncio.Event]` maps `stream_id` to a stop event. Clients supply a `stream_id` in the request body; if absent, the server generates a UUID. Setting the event signals the streaming generator and Ollama thread to stop early.

### Request Schema

```python
class ChatRequest(BaseModel):
    query: str        # 3–1000 characters
    stream_id: str | None  # optional client-supplied cancellation token
```

### `POST /chat/stream`

Requires `view_patients` permission. Returns `text/event-stream` SSE.

**Request flow:**

1. **JWT validation** — `require_permission("view_patients")`
2. **Stream ID registration** — uses client-supplied `stream_id` or generates UUID; registers `asyncio.Event` in `_active_streams`
3. **Patient identity resolution** — `LLMForQuery` + `MongoDBSearchAndRetrieval.get_patient_id_by_name()` → `patient_ids`, `visit_ids`, `clean_query`
4. **Not-found guard** — if `patient_ids` is empty, stream `{"error": "Patient record not found."}` and return
5. **Chunk type classification** — `LLMForQuery.extract_chunk_types(query)` → filter attributes list
6. **Hybrid Milvus search** — `MilvusSearchAndRetrieval.search_hybrid(limit=RERANK_FETCH_LIMIT)` → top 15 candidates
7. **BGE reranking** — `BGEReranker.rerank(query, hybrid_results, top_k=RERANK_TOP_K)` → top 8 re-scored hits
8. **Audit log** — `AuditChain.log(query, patient_ids, chunk_types, session_id)` — verifies chain integrity before writing; raises HTTP 500 and blocks query on `AuditChainTamperError`
9. **Context extraction** — `_extract_context(reranked_results)` reads plaintext `text` field from reranked hits
10. **RAG prompt construction** — `RAG_CHAT_PROMPT_TEMPLATE.format(context, query, length_limit)`
11. **Async Ollama streaming** — `ollama_chat(CHAT_MODEL, prompt, stream=True)` runs in thread pool; tokens queued via `asyncio.run_coroutine_threadsafe`; yielded as SSE `{"token": "..."}` events; stop event checked on each iteration
12. **Stream completion** — sends `{"done": true}` on normal finish or `{"stopped": true}` on cancellation; cleans up `_active_streams` entry

**SSE event format:**
```
data: {"stream_id": "<uuid>"}\n\n   ← first event, client uses for cancellation
data: {"token": "word"}\n\n
...
data: {"done": true}\n\n
```

**SSE Response Headers:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
Access-Control-Allow-Origin: *
```

### `POST /chat/stop/{stream_id}`

Requires `view_patients` permission. Sets the stop event for the given `stream_id`, signaling the generator to terminate after the next token poll. Returns `{"stopped": true}` if found, or `{"stopped": false}` if the stream has already ended.

### `_extract_context(results) -> str`

Reads the plaintext `text` field (anonymized, not encrypted) from each reranked Milvus hit entity. Joins fragments with newlines. Returns `"No records found."` if no valid hits exist.

> **Note:** This replaces the previous `_extract_and_decrypt_context()` approach. The current pipeline stores anonymized plaintext in `text` for direct context use; the encrypted `text_enc` field is no longer decrypted at query time.

### `GET /health`

Returns `{"status": "ok", "mode": "hybrid-rag-rerank-audit"}`.

---

## auth.py

### Purpose

Standalone FastAPI authentication service on port 8001. Two-factor login, JWT issuance, RBAC, and password reset.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `JWT_EXPIRE_MINUTES` | `60` | JWT lifetime |
| `OTP_EXPIRE_MINUTES` | `10` | OTP validity window |
| `MAX_FAILED_ATTEMPTS` | `5` | Login failures before lockout |
| `LOCKOUT_MINUTES` | `15` | Account lockout duration |

### Database

Direct `MongoClient` connection to `securecarebot.users`.

**User document fields:** `username`, `password_hash`, `email`, `role`, `name`, `otp_hash`, `otp_expires_at`, `reset_otp_hash`, `reset_otp_expires_at`, `failed_login_attempts`, `locked_until`, `last_login`.

### Helper Functions

`_now()` returns `datetime.now(timezone.utc)` — always timezone-aware.

`_verify_password(plain, hashed)` wraps `bcrypt.checkpw()` in try/except.

`_hash_payload(data)` applies `bcrypt.hashpw()` with fresh salt. Used for both passwords and OTPs.

`_create_jwt(username, role, permissions, jti)` encodes `sub`, `role`, `permissions`, `jti`, `iat`, `exp` using PyJWT.

`_send_email(to, subject, otp, name)` sends HTML email via STARTTLS SMTP. Falls back to console logging if SMTP credentials are absent (dev mode).

### RBAC Role Matrix

| Role | Permissions |
|---|---|
| `admin` | view_patients, edit_patients, delete_patients, view_reports, manage_users |
| `doctor` | view_patients, edit_patients, view_reports |
| `nurse` | view_patients, view_reports |

### `POST /auth/login/step1`

Validates password with bcrypt. On failure, increments `failed_login_attempts`; locks account for 15 minutes after 5 failures. On success, generates 6-digit OTP, bcrypt-hashes it, stores `otp_hash` and `otp_expires_at` (10-minute window), emails OTP.

Lockout check is timezone-safe: stored `locked_until` may be naive; code adds `tzinfo=timezone.utc` before comparison.

### `POST /auth/login/step2`

Validates OTP against stored hash and expiry. Generates UUID `jti` (for future token revocation). Issues JWT with full permissions list. Clears `otp_hash`, updates `last_login`. Returns `access_token`, `role`, `permissions`, and `name` (required by frontend).

### `POST /auth/forgot-password/request`

Looks up user by email. Always returns the same generic success message to prevent email enumeration. Generates and stores `reset_otp_hash` + `reset_otp_expires_at`.

### `POST /auth/forgot-password/verify-otp`

Validates reset OTP. Does NOT clear the OTP — it must survive for re-verification in the next step.

### `POST /auth/forgot-password/reset`

Re-verifies OTP (prevents skipping verification step). Enforces 8-character minimum password. Updates `password_hash`, clears all OTP and lockout fields atomically.

### `GET /auth/health`

Returns `{"status": "running"}`.

---

## Configuration Reference

### Ollama Models

| Model | Purpose | Temperature | Stream |
|---|---|---|---|
| `qwen2.5:1.5b-instruct-q4_K_M` | Name extraction, chunk classification, query decomposition | 0.1 | No |
| `qwen2.5:1.5b-instruct-q4_K_M` | Chunk summarization | 0.5 | No |
| `phi3.5:3.8b-mini-instruct-q4_K_M` | RAG chat response generation | 0.2 | Yes |
| `qwen3-embedding:0.6b` | Dense vector embedding (1024-dim) | N/A | No |

### File Paths

| Path | Contents |
|---|---|
| `datas/1_patients_data_json.json` | Raw patient data input |
| `datas/2_patients_data_chunks.json` | LLM-summarized chunks (intermediate) |
| `datas/4_patients_personal_data_audit.json` | Audit log: patient_id, chunk_type, name_hash only |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `SCB_MASTER_SECRET` | YES | 32-byte hex master secret for key derivation |
| `JWT_SECRET` | YES | JWT signing secret (shared between api.py and auth.py) |
| `JWT_ALGORITHM` | No (HS256) | JWT algorithm |
| `JWT_EXPIRE_MINUTES` | No (60) | JWT lifetime in minutes |
| `MONGO_URL` | No (localhost) | MongoDB connection URI |
| `SMTP_HOST` | No | SMTP server hostname |
| `SMTP_PORT` | No (587) | SMTP port |
| `SMTP_USER` | No | Sender email address |
| `SMTP_PASSWORD` | No | SMTP password or app password |

Generate secrets:
```bash
python -c "import secrets; print(secrets.token_hex(32))"       # SCB_MASTER_SECRET
python -c "import secrets; print(secrets.token_urlsafe(32))"   # JWT_SECRET
```

---

## Error Handling Guide

| Location | Error Condition | Handling |
|---|---|---|
| `encrypt_decrypt.py` | `SCB_MASTER_SECRET` not set | `EnvironmentError` at import time — startup fails immediately |
| `encrypt_decrypt.py` | Wrong key or corrupt ciphertext | `InvalidToken` wrapped as `ValueError` |
| `api.py` JWT | `jwt.PyJWTError` | HTTP 401 |
| `api.py` permissions | Missing permission | HTTP 403 |
| `api.py` patient not found | Empty `patient_ids` | SSE error event: `{"error": "Patient record not found."}` |
| `api.py` audit tamper | `AuditChainTamperError` during `audit.log()` | HTTP 500; query blocked entirely; error logged |
| `reranker.py` scoring | Ollama embed call fails | Returns `0.0` for that hit; hit may be filtered by threshold |
| `reranker.py` empty input | Empty results list | Returns results as-is without scoring |
| `audit_chain.py` tamper | Hash mismatch or linkage break | `AuditChainTamperError` with block index in message |
| `audit_chain.py` connection | MongoDB unreachable at init | `RuntimeError` wrapping `ConnectionFailure`; startup fails |
| `auth.py` login | Wrong password | HTTP 401; increments `failed_login_attempts` |
| `auth.py` lockout | >= 5 failures | HTTP 429 with minutes remaining |
| `auth.py` OTP expired | `otp_expires_at` in past | HTTP 400 |
| `auth.py` forgot-password | Email not found | Same HTTP 200 response (prevents enumeration) |
| `mongo_db.py` | Connection failure | `ConnectionFailure` propagated; startup fails |
| `milvus_db.py` insert | Per-chunk error | Caught, logged; pipeline continues |
| `med_embedding_ollama.py` | Empty or whitespace text | `ValueError` raised before Ollama call |
| `patient_data_to_chunks.py` | Missing Patient ID | Prints skip message and continues |

---

## Extending the System

### Adding a New Chunk Type

1. In `patient_data_to_chunks.py`, add a new `add_chunk()` call in `convert()` with a descriptive key name.
2. In `prompt_templates.py`, add the new category name to `VALID_CHUNK_CATEGORIES` and add a definition line to `EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE`.
3. Re-run the ingestion pipeline (Stage 2).

### Adding a New RBAC Role

1. In `auth.py`, add an entry to `ROLE_PERMISSIONS` with the desired permissions list.
2. Assign new users this role in the MongoDB `users` collection.

### Swapping the Embedding Model

1. Update `EMBEDDING_DIM` in `MedEmbedderOllama` to match the new model's output dimension.
2. Change the default `model_name` in `MedEmbedderOllama.__init__()`.
3. Drop and recreate the Milvus collection using `reset_milvus()` in `main.py` — the `dense_vector` dimension is fixed at schema creation time.
4. Re-run the full ingestion pipeline (Stages 1-3).

### Swapping the Chat LLM

1. Change `CHAT_MODEL` in `main.py` and `api.py`.
2. Pull the new model: `ollama pull <model>`.
3. No schema changes required.

### Adding Redis Token Deny-List (Production)

The current `_denied_tokens: set[str]` in `auth.py` is in-memory and cleared on restart. To make it persistent:
1. Add a Redis client.
2. On logout: `redis.setex(jti, JWT_EXPIRE_SECONDS, "1")`.
3. In `get_current_user()` in `api.py`, check: if `redis.exists(payload["jti"])` raise HTTP 401.

### Scaling Embedding (Batch Processing)

Current `MedEmbedderOllama.encode()` calls Ollama once per text string. If the deployed Ollama model supports multi-text embedding in a single call, replace the `for text in chunks` loop with a single batch API call for significant ingestion speedup on large patient datasets.