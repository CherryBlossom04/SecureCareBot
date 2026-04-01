# SecureCareBot — Detailed System Workflow

## Table of Contents
1. [System Overview](#system-overview)
2. [Architecture Overview](#architecture-overview)
3. [Pipeline Stage 1 — Database Initialization](#pipeline-stage-1--database-initialization)
4. [Pipeline Stage 2 — Patient Data Ingestion & Summarization](#pipeline-stage-2--patient-data-ingestion--summarization)
5. [Pipeline Stage 3 — Identity Profile Insertion](#pipeline-stage-3--identity-profile-insertion)
6. [Pipeline Stage 4 — Query & RAG Response](#pipeline-stage-4--query--rag-response)
7. [Authentication Flow](#authentication-flow)
8. [Streaming Chat API Flow](#streaming-chat-api-flow)
9. [Blockchain Audit Chain](#blockchain-audit-chain)
10. [Zero-Trust Security Model](#zero-trust-security-model)
11. [Data Flow Diagrams](#data-flow-diagrams)

---

## System Overview

SecureCareBot is a **Zero-Trust RAG (Retrieval-Augmented Generation)** system for medical data. It allows authorized clinical staff to query patient records in natural language. Patient data is stored split across two databases:

- **Milvus** — stores anonymized clinical text with vector embeddings for semantic search
- **MongoDB** — stores encrypted PII (name, DOB, address, phone) per patient

The LLM never sees raw PII. Decryption happens in-memory, per request, and the plaintext is discarded after the streaming response completes.

---

## Architecture Overview

```
+------------------------------------------------------------------+
|                        CLIENT (Browser)                          |
|       POST /chat/stream   POST /chat/stop/{id}   GET /health     |
+------------------------------+-----------------------------------+
                               |  JWT Bearer Token
+------------------------------v-----------------------------------+
|                    FastAPI (api.py / auth.py)                    |
|   /chat/stream   /chat/stop/{id}                                 |
|   /auth/login/step1   /auth/login/step2                          |
|   /auth/forgot-password/*   /health                              |
+-----------+------------------------------+----------------------+
            |                              |
     +------v------+                +------v------+
     |   Milvus DB |                |  MongoDB    |
     | (Vectors +  |                | (Encrypted  |
     | Anon Text)  |                |  PII +      |
     +------+------+                | Audit Chain)|
            |                       +------+------+
            |                              |
     +------v------+                       |
     |  BGE        |                       |
     | Reranker    |                       |
     | (Ollama)    |                       |
     +------+------+                       |
            |                              |
     +------v------------------------------v------+
     |           Ollama LLM (Local)               |
     |   qwen2.5:1.5b  (query parsing)            |
     |   phi3.5:3.8b   (RAG chat response)        |
     |   qwen3-embedding:0.6b  (embeddings)       |
     |   bge-reranker-large    (reranking)        |
     +--------------------------------------------+
```

---

## Pipeline Stage 1 — Database Initialization

**Entry point:** `main.py -> initialize_databases()`

### Step 1.1 — Milvus Initialization

`MilvusDB.__init__()` is called with `server_url`, `db_name`, `collection_name`, and a `MedEmbedderOllama` instance.

- The `@singleton` decorator ensures only one instance is ever created per process.
- Connects to Milvus at `http://localhost:19530`.
- Selects or creates the database `securecarebot_db`.
- Checks if collection `meddataollama` exists. If not, creates it with the full schema.

**Collection Schema (Milvus):**

| Field | Type | Purpose |
|---|---|---|
| `chunk_id` | VARCHAR, primary key | Unique ID per chunk (e.g., `P00011_visit_symptoms_V001`) |
| `patient_id` | VARCHAR, filterable | Owner patient reference |
| `chunk_type` | VARCHAR, filterable | Category (e.g., `visit_symptoms`, `history_diagnosis`) |
| `visit_id` | VARCHAR, filterable | Visit reference (e.g., `V00001`) |
| `text` | VARCHAR | Anonymized plaintext for BM25 sparse search |
| `text_enc` | VARCHAR | Fernet-encrypted ciphertext retrieved for context |
| `dense_vector` | FLOAT_VECTOR(1024) | Semantic embedding for cosine similarity search |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | BM25 keyword search vector |

### Step 1.2 — MongoDB Initialization

`MongoDB.__init__()` is called with `url`, `db_name`, and `collection_name`.

- Also a `@singleton` — one connection per process.
- Connects to `mongodb://localhost:27017/`.
- Pings the server (`admin.command("ping")`) to verify connectivity with a 5-second timeout.
- Sets active database to `securecarebot`, collection to `patients`.

### Step 1.3 — AuditChain Initialization

`AuditChain.__init__()` is called at API startup (lifespan).

- Also a `@singleton`.
- Connects to MongoDB, collection `audit_chain` (separate from `patients`).
- Creates unique index on `block_index` and standard indexes on `session_id` and `timestamp`.
- An empty chain is valid — the first real access event seeds the genesis block.

---

## Pipeline Stage 2 — Patient Data Ingestion & Summarization

**Entry point:** `main.py -> create_and_insert_patient_summary()`

### Step 2.1 — Load Raw JSON

```
load_json_data("datas/1_patients_data_json.json")
-> Returns: list[dict]  (one dict per patient)
```

`load_json_data()` (in `decorators.py`) validates the path is a non-empty string, then uses `json.load()` to parse the file.

### Step 2.2 — Chunk Patient Records

`PatientDataToChunksForQwen2.convert()` iterates each patient and produces a nested dict:

```
{ patient_id: { chunk_type: chunk_data_dict } }
```

**Chunk types produced per patient:**

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

**Data cleaning:** `clean_data()` recursively removes `None`, empty strings, empty lists, and empty dicts. `add_chunk()` only registers a chunk if it has at least one key beyond `Patient ID` and `Visit ID`.

### Step 2.3 — Summarize Chunks with LLM

`LLMForVector.Qwen25.summarize(structured_chunks)` iterates every `patient_id -> chunk_type -> chunk_data` triple.

For each chunk:
1. Builds header string: `"{patient_id} | {chunk_type}"`
2. Formats `SUMMARIZE_PROMPT_TEMPLATE` with header + JSON-serialized chunk data
3. Calls `ollama_chat(model="qwen2.5:1.5b-instruct-q4_K_M", temperature=0.5)`
4. LLM returns a dense clinical sentence, for example:

   > `P00011 | V001 | 10-04-2025. Patient has BP of 145/92 with symptoms of thirst and fatigue, diagnosed with Type 2 Diabetes.`

Result is a `list[dict]` where each entry contains: `chunk_id`, `patient_id`, `chunk_type`, `text`.
Output is also saved to `datas/2_patients_data_chunks.json`.

### Step 2.4 — Insert into Milvus

`MilvusDataInsertion(summarized_chunks)` processes each summarized chunk:

1. **Anonymize** — `anonymise_text(text)` runs regex substitutions:
   - Phone numbers (10-digit, with/without +91 prefix) → `[PHONE]`
   - Email addresses → `[EMAIL]`
   - Dates DD/MM/YYYY, DD-MM-YYYY, YYYY-MM-DD → `[DOB]`
   - Street addresses with keywords (Street, Road, Nagar, Colony, etc.) → `[ADDRESS]`
   - Aadhaar numbers (4-digit groups) → `[NATIONAL_ID]`
   - Result stored in `text` field (BM25 index)

2. **Encrypt** — `encrypt_patient_data(text, patient_id)`:
   - Derives a per-patient Fernet key via PBKDF2-HMAC-SHA256 from the master secret
   - Encrypts the **original** (pre-anonymized) text
   - Result stored in `text_enc` field

3. **Embed** — `MedEmbedderOllama.encode([text])`:
   - Calls `ollama.embeddings(model="qwen3-embedding:0.6b", prompt=text)`
   - Returns a 1024-dimensional float vector
   - Stored in `dense_vector` field

4. **Insert** all fields as a single record into the Milvus collection.

---

## Pipeline Stage 3 — Identity Profile Insertion

**Entry point:** `main.py -> insert_identity_profiles(summarized_chunks)`

Processes only chunks where `chunk_type == "profile_identity"`.

### Step 3.1 — Extract Patient Name

`LLMForQuery.extract_name(text)` formats `EXTRACT_NAME_PROMPT_TEMPLATE` and calls `ollama_chat()`. LLM returns the patient's full name or `NONE`.

Post-processing applied:
- Strip brackets, quotes, possessives: `re.sub(r"[\[\]\"']", "", raw_response)`
- Normalize to lowercase
- Split on comma if multiple names returned

### Step 3.2 — Hash the Name

`hash_text(normalized_name)` computes **HMAC-SHA256** of the name using the master secret as the HMAC key:

```python
hmac.new(_MASTER_SECRET.encode(), name.lower().encode(), hashlib.sha256).hexdigest()
```

This is a one-way hash — the actual name is never stored anywhere. Lookup is performed later by re-hashing the query-extracted name and comparing hashes.

### Step 3.3 — Encrypt the Profile Text

`encrypt_patient_data(text, patient_id)` derives the same per-patient Fernet key used in Stage 2 and encrypts the entire profile text (name, DOB, address, phone, email).

### Step 3.4 — Insert Document into MongoDB

```json
{
  "patient_id": "P00011",
  "chunk_type": "profile_identity",
  "name_hash": "<hmac-sha256-hex-64-chars>",
  "text_enc": "<fernet-ciphertext>"
}
```

An audit log (without `text_enc`) is saved to `datas/4_patients_personal_data_audit.json` via `dump_to_json()`. Duplicate patient IDs are skipped.

---

## Pipeline Stage 4 — Query & RAG Response

**Entry point:** `main.py -> query_system(query, limit)`

### Step 4.1 — Name Resolution via MongoDB

`MongoDBSearchAndRetrieval.get_patient_id_by_name(query, query_helper)`:

1. Regex-extract Visit IDs: `\bV\d{5,10}\b` → uppercase
2. Regex-extract Patient IDs: `\b[pP]\d{3,}\b` → uppercase
3. LLM extracts patient names from the query
4. For each name: normalize → `hash_text()` → `MongoDB.find({"name_hash": hash})`
5. Build `name_to_id_map` and substitute names with IDs in the original query text
6. Return: `(patient_ids: list[str], visit_ids: list[str], clean_query: str)`

Example clean query transformation:
```
Input:  "What are Arun Kumar's current symptoms?"
Output: "What are P00011's current symptoms?"
```

### Step 4.2 — Chunk Type Classification

`LLMForQuery.extract_chunk_types(query)` → `EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE`

Returns a list of valid chunk categories for Milvus filter expressions. Temporal rules are enforced in the prompt:
- "current" / "now" / "today" → only `visit_*` categories
- "past" / "history" / "previous" → only `history_*` categories
- "compare" / "trend" / "change" → both `visit_*` and `history_*` versions

### Step 4.3 — Query Decomposition (optional)

`LLMForQuery.split_query(query)` → `SPLIT_QUERY_PROMPT_TEMPLATE`

Decomposes complex queries into 2–3 targeted sub-queries for better recall:
```
"What are John's symptoms?"
->
"Personal profile and medical history of John"
"Specific symptoms and clinical signs of John"
```

### Step 4.4 — Hybrid Vector Search (Milvus)

`MilvusSearchAndRetrieval.search_hybrid(query, patient_id, visit_id, limit, attribute)`:

Three searches are combined:
- **Dense search** — cosine similarity on `dense_vector` using embedded query (semantic meaning)
- **Sparse search** — BM25 keyword match on anonymized `text` field (exact clinical terms)
- **Hybrid (RRF)** — Reciprocal Rank Fusion merges both ranked lists into a single ranked result

Filter expression built dynamically:
```
patient_id IN ["P00011", "P00013"]
AND chunk_type IN ["visit_symptoms", "visit_blood_report"]
AND visit_id IN ["V00001"]   (if visit_ids non-empty)
```

Returns top-N hits, each containing `text_enc`, `patient_id`, and relevance `distance`.

### Step 4.5 — Context Decryption (In-Memory Only)

For each Milvus hit:
1. Read `entity.text_enc` and `entity.patient_id`
2. `decrypt_patient_data(text_enc, patient_id)` — derives per-patient key → Fernet decrypt
3. Plaintext appended to `context_fragments` list
4. Any decryption failure is silently skipped (no error details logged — ciphertext or patient_id must not leak)

### Step 4.6 — RAG Prompt & Streaming Response

`RAG_CHAT_PROMPT_TEMPLATE` is formatted with:
- `context` — joined decrypted fragments
- `query` — the clean (name-substituted) query
- `length_limit` — default `"3 to 8 sentences"`

The filled prompt is sent to `phi3.5:3.8b-mini-instruct-q4_K_M` via `ollama_chat(..., stream=True)`. Tokens are yielded as they arrive, printed to terminal, and accumulated into `full_response`.

---

## Authentication Flow

**Service:** `auth.py` on port 8001

### Login — Two-Factor (Password + OTP)

```
Step 1: POST /auth/login/step1
  Body: { username, password }

  1. Lookup user document in MongoDB users collection
  2. Check locked_until field (timezone-aware comparison)
  3. bcrypt.checkpw(password, password_hash)
  4. On failure:
       - Increment failed_login_attempts
       - If >= 5: set locked_until = now + 15 minutes
       - Return HTTP 401 or HTTP 429
  5. On success:
       - Generate 6-digit random OTP
       - bcrypt-hash the OTP and store as otp_hash
       - Set otp_expires_at = now + 10 minutes
       - Reset failed_login_attempts to 0
       - Send OTP via SMTP (or log to console in dev mode)
  -> Response: { message: "OTP sent to email." }

Step 2: POST /auth/login/step2
  Body: { username, otp }

  1. Lookup user document
  2. Check otp_expires_at (make timezone-aware if naive)
  3. bcrypt.checkpw(otp, otp_hash)
  4. On success:
       - Generate UUID jti (prevents token replay)
       - Encode JWT with sub, role, permissions, jti, iat, exp
       - Clear otp_hash, update last_login
  -> Response: { access_token, token_type, role, permissions, name }
```

### JWT Payload

```json
{
  "sub": "dr_arun",
  "role": "doctor",
  "permissions": ["view_patients", "edit_patients", "view_reports"],
  "jti": "550e8400-e29b-41d4-a716-446655440000",
  "iat": 1700000000,
  "exp": 1700003600
}
```

### RBAC Role Matrix

| Role | Permissions |
|---|---|
| `admin` | view_patients, edit_patients, delete_patients, view_reports, manage_users |
| `doctor` | view_patients, edit_patients, view_reports |
| `nurse` | view_patients, view_reports |

### Forgot Password Flow

```
POST /auth/forgot-password/request
  -> Lookup by email (generic response prevents email enumeration)
  -> Generate OTP, hash with bcrypt, store reset_otp_hash + reset_otp_expires_at
  -> Send reset OTP via email

POST /auth/forgot-password/verify-otp
  -> Validate reset OTP against stored hash and expiry
  -> Does NOT clear OTP (needed for next step)

POST /auth/forgot-password/reset
  -> Re-verify OTP (prevents skipping verification step)
  -> Enforce minimum password length (>= 8 characters)
  -> bcrypt-hash new password, store password_hash
  -> Clear reset_otp_hash, reset failed_login_attempts and locked_until
```

---

## Streaming Chat API Flow

**Service:** `api.py` on port 8000

```
POST /chat/stream
  Headers: Authorization: Bearer <JWT>
  Body: { "query": "What are Arun's current symptoms?", "stream_id": "<optional-uuid>" }

1.  JWT Validation
    -> HTTPBearer extracts token from Authorization header
    -> jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    -> require_permission("view_patients") checks permissions list

2.  Stream ID Registration
    -> Use client-supplied stream_id or generate UUID
    -> Register asyncio.Event in _active_streams dict
    -> First SSE event: data: {"stream_id": "<uuid>"}\n\n

3.  Patient Identity Resolution
    -> LLMForQuery(QUERY_MODEL).extract_name(query)
    -> MongoDBSearchAndRetrieval.get_patient_id_by_name()
    -> Returns: patient_ids, visit_ids, clean_query

4.  Not-Found Guard
    -> If patient_ids is empty:
       -> Stream: data: {"error": "Patient record not found."}\n\n
       -> Remove stream_id from _active_streams
       -> Return immediately

5.  Chunk Type Classification
    -> LLMForQuery.extract_chunk_types(clean_query)
    -> Returns: ["visit_symptoms", "visit_blood_report"]

6.  Hybrid Milvus Search (RERANK_FETCH_LIMIT = 15 candidates)
    -> MilvusSearchAndRetrieval.search_hybrid(limit=15)
    -> Returns hits: [{ entity: { text, text_enc, patient_id, chunk_type } }]

7.  BGE Reranking (RERANK_TOP_K = 8 survivors)
    -> BGEReranker.rerank(query=clean_query, results=hybrid_results, top_k=8)
    -> For each hit: ollama.embed(bge-reranker-large, [query, text]) -> cosine score
    -> Sort descending by score, trim to top 8
    -> rerank_score attached to each hit entity

8.  Blockchain Audit Log
    -> AuditChain.verify() — checks entire chain integrity
    -> If tampered: raise AuditChainTamperError
       -> Remove stream_id, return HTTP 500 (query blocked)
    -> AuditChain.log(query, patient_ids, chunk_types, session_id=JWT.sub)
    -> Records block with SHA-256 hash, linked to previous block

9.  Context Extraction
    -> _extract_context(reranked_results)
    -> Reads plaintext text field from each reranked hit entity
    -> Joins with newlines -> context_text

10. RAG Prompt Construction
    -> RAG_CHAT_PROMPT_TEMPLATE.format(context, query, length_limit)
    -> full_prompt exists only in this function scope

11. Async Ollama Streaming (with cancellation support)
    -> ollama_chat(CHAT_MODEL, full_prompt, stream=True) runs in thread pool
    -> Tokens sent to asyncio.Queue via run_coroutine_threadsafe
    -> Generator polls queue with 0.1s timeout; checks stop_event between polls
    -> Yielded as SSE: data: {"token": "word"}\n\n
    -> Normal finish: data: {"done": true}\n\n
    -> Cancelled (POST /chat/stop/{stream_id}): data: {"stopped": true}\n\n

12. Cleanup
    -> stream_id removed from _active_streams
    -> context_text and full_prompt go out of scope
```

**SSE Response Headers:**
```
Content-Type: text/event-stream
Cache-Control: no-cache
X-Accel-Buffering: no
Access-Control-Allow-Origin: *
```

**Stream cancellation:**
```
POST /chat/stop/{stream_id}
  Headers: Authorization: Bearer <JWT>
  -> Sets stop event in _active_streams
  -> Response: {"stopped": true, "stream_id": "<uuid>"}
     or {"stopped": false, "detail": "Stream not found or already finished."}
```

---

## Blockchain Audit Chain

Every query event processed by `/chat/stream` is recorded as a tamper-evident block in the `audit_chain` MongoDB collection. The chain ensures a complete, unalterable access history.

### Block Structure

```json
{
  "block_index":  5,
  "timestamp":    "2025-04-01T10:22:00.123456+00:00",
  "session_id":   "dr_arun",
  "hostname":     "prod-server-01",
  "ip_address":   "10.0.1.42",
  "query":        "What is P00011's current medication?",
  "chunk_types":  ["visit_medication", "visit_overview"],
  "prev_hash":    "a3f7...9c12",
  "block_hash":   "e84b...2d91"
}
```

Note: `patient_ids` are intentionally omitted from the block to reduce PII exposure in the audit log while still recording query intent and chunk types.

### Tamper Detection Guarantees

| Tamper Type | Detection Mechanism |
|---|---|
| Field edited in any block | SHA-256 of block content no longer matches stored `block_hash` |
| Block hash forged (data + hash both changed) | Next block's `prev_hash` no longer matches forged block's new hash |
| Block deleted from middle of chain | `prev_hash` of the following block no longer matches its predecessor |
| New block injected with fabricated `prev_hash` | `prev_hash` does not match any real block's `block_hash` |

### Verification Cadence

`AuditChain.verify()` is called inside every `log()` call before writing. Any tampering detected since the last write will block the incoming query with HTTP 500.

---

## Zero-Trust Security Model

### Design Principles

| Principle | Implementation |
|---|---|
| No global encryption key | Per-patient keys derived via PBKDF2-HMAC-SHA256 from master secret |
| Anonymized text in Milvus | Regex strips phone, email, DOB, address, Aadhaar before insert |
| Encrypted PII in MongoDB | Fernet with per-patient key; name never stored plaintext |
| One-way name lookup | HMAC-SHA256 hash of name; actual name discarded after hashing |
| LLM receives no raw PII | Context passed to LLM is anonymized plaintext only |
| Decrypted text never logged | No logging of plaintext content anywhere in the query pipeline |
| Scoped key compromise | Compromising one patient key does not expose others |
| Tamper-evident audit trail | Blockchain in MongoDB: every access event is hash-chained; tampering blocks future queries |
| BGE relevance filtering | Only the top-K most relevant chunks reach the LLM; reduces leakage of unrelated records |

### Key Derivation

```
SCB_MASTER_SECRET  (from .env, never hardcoded)
        +
patient_id (e.g., "p00011")
        |
        v
PBKDF2-HMAC-SHA256(
  password = master_secret.encode('utf-8'),
  salt     = patient_id.lower().encode('utf-8'),
  iterations = 100_000,
  dklen    = 32
)
        |
        v
32-byte raw key -> base64url encode -> Fernet key
        |
        v
encrypt_patient_data() / decrypt_patient_data()
```

Same `patient_id + master_secret` always produces the same key deterministically — no key storage required.

### Name Lookup Without Storing Names

```
Query:  "What are Arun Kumar's symptoms?"
          |
          v
LLM extracts: "Arun Kumar"
          |
          v
Normalize: "arun kumar"
          |
          v
HMAC-SHA256(master_secret, "arun kumar") -> "3f7a9c..."
          |
          v
MongoDB.find({ name_hash: "3f7a9c..." }) -> { patient_id: "P00011" }
          |
          v
"Arun Kumar" string discarded; only "P00011" used further
```

### PII Anonymization Patterns

| Pattern | Replacement |
|---|---|
| `(\+91[\-\s]?)?\d{10}` | `[PHONE]` |
| `[\w.\-+]+@[\w.\-]+\.\w{2,}` | `[EMAIL]` |
| `\d{2}[\/\-]\d{2}[\/\-]\d{4}` | `[DOB]` |
| `\d{4}[\/\-]\d{2}[\/\-]\d{2}` | `[DOB]` |
| Street address patterns | `[ADDRESS]` |
| `\d{4}\s\d{4}\s\d{4}` (Aadhaar) | `[NATIONAL_ID]` |

---

## Data Flow Diagrams

### Ingestion Pipeline (One-Time Setup)

```
1_patients_data_json.json
          |
          v
PatientDataToChunksForQwen2.convert()
          |   (typed chunk dicts per patient)
          v
LLMForVector.Qwen25.summarize()  <-- ollama: qwen2.5:1.5b
          |   (natural language clinical summaries)
          |
          +----> anonymise_text()   -----------> Milvus: text field (BM25)
          +----> encrypt_patient_data() -------> Milvus: text_enc field
          +----> MedEmbedderOllama.encode() ---> Milvus: dense_vector field

          |   (profile_identity chunks only)
          v
LLMForQuery.extract_name()  <-- ollama: qwen2.5:1.5b
          |
          +----> hash_text(name)  ------------> MongoDB: name_hash field
          +----> encrypt_patient_data(text) --> MongoDB: text_enc field
```

### Query Pipeline (Per Request)

```
User Query (via /chat/stream)
          |
          v
JWT Auth + Permission Check (require "view_patients")
          |
          v
LLMForQuery.extract_name()  <-- ollama: qwen2.5:1.5b
          |
          v
MongoDB: find({ name_hash }) -> patient_id(s)
          |
          v
LLMForQuery.extract_chunk_types() -> filter attributes
          |
          v
MilvusSearchAndRetrieval.search_hybrid(limit=15)
  |-- Dense search (cosine, dense_vector)
  |-- Sparse search (BM25, text)
  +-- RRF fusion -> top-15 hits
          |
          v
BGEReranker.rerank(top_k=8)
  |-- ollama.embed(bge-reranker-large, [query, hit.text])
  |-- cosine similarity score per hit
  +-- sorted, filtered -> top-8 hits
          |
          v
AuditChain.verify() + AuditChain.log()
  |-- Verify entire blockchain integrity
  |-- Block query if tampered (HTTP 500)
  +-- Append new block with SHA-256 linkage
          |
          v
_extract_context(reranked_results) -> context_text (anonymized plaintext)
          |
          v
RAG_CHAT_PROMPT_TEMPLATE.format(context, query)
          |
          v
ollama_chat(phi3.5:3.8b, prompt, stream=True)
          |
          v
SSE stream: data: {"token": "..."}\n\n  ->  Client
```