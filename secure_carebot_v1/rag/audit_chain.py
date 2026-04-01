"""
audit_chain.py — Blockchain Audit Log for SecureCareBot
========================================================

Records every query event as a tamper-evident blockchain stored in MongoDB.
Each block contains:
  - Query text (what was asked)
  - Patient IDs accessed (never the data itself)
  - Chunk types accessed
  - System info (hostname + IP)
  - User / session ID
  - Timestamp (UTC)
  - SHA-256 hash of this block's contents
  - Previous block's hash (chain linkage)

Tamper detection:
  - Before every write, the entire chain is verified.
  - If any block's stored hash doesn't match its recomputed hash,
    or if a block's prev_hash doesn't match the previous block,
    an AuditChainTamperError is raised and the write is blocked.

MongoDB collection: `audit_chain` (separate from `patients`)

Usage:
    from rag.audit_chain import AuditChain

    audit = AuditChain()
    audit.log(
        session_id="sess_abc123",
        query="What is Arun Kumar's current medication?",
        patient_ids=["P00011"],
        chunk_types=["visit_medication"],
    )
"""


import hashlib
import json
import socket
import uuid
from datetime import datetime, timezone

from pymongo import MongoClient, DESCENDING
from pymongo.errors import ConnectionFailure

from rag.decorators import singleton



class AuditChainTamperError(Exception):
    """Raised when blockchain integrity verification fails."""
    pass


def _get_local_ip() -> str:
    """Best-effort local IP detection — never fails."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "unknown"


def _compute_hash(block_data: dict) -> str:
    """
    Deterministic SHA-256 hash of a block's content fields.
    Uses sorted keys so insertion order never affects the hash.
    `block_hash` itself is excluded before hashing.
    """
    hashable = {k: v for k, v in block_data.items() if k != "block_hash"}
    serialized = json.dumps(hashable, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@singleton
class AuditChain:
    """
    Append-only blockchain audit log stored in MongoDB.

    Each query event is recorded as a block linked to the previous one
    via its SHA-256 hash. The chain is verified before every write —
    any tampering raises AuditChainTamperError and blocks the write.
    """

    GENESIS_HASH = "0" * 64

    def __init__(
        self,
        mongo_url: str = "mongodb://localhost:27017/",
        db_name: str = "securecarebot",
        collection_name: str = "audit_chain",
    ):
        if not mongo_url or not isinstance(mongo_url, str):
            raise ValueError("mongo_url must be a non-empty string.")
        if not db_name or not isinstance(db_name, str):
            raise ValueError("db_name must be a non-empty string.")
        if not collection_name or not isinstance(collection_name, str):
            raise ValueError("collection_name must be a non-empty string.")

        try:
            self._client = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
            self._client.admin.command("ping")
        except ConnectionFailure as e:
            raise RuntimeError("AuditChain could not connect to MongoDB.") from e

        self._col = self._client[db_name][collection_name]

        # Ensure block_index is unique and queries on session_id are fast
        self._col.create_index("block_index", unique=True)
        self._col.create_index("session_id")
        self._col.create_index("timestamp")

        print(f"✅ AuditChain ready — collection: '{collection_name}'")

    # ── Public API ─────────────────────────────────────────────────────────────

    def log(
        self,
        query: str,
        patient_ids: list[str],
        chunk_types: list[str],
        session_id: str | None = None,
    ) -> dict:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string.")
        if not isinstance(patient_ids, list):
            raise TypeError("patient_ids must be a list.")
        if not isinstance(chunk_types, list):
            raise TypeError("chunk_types must be a list.")

        # Step 1 — verify integrity before writing
        self.verify()

        # Step 2 — gather chain state
        prev_block  = self._get_latest_block()
        prev_hash   = prev_block["block_hash"] if prev_block else self.GENESIS_HASH
        block_index = (prev_block["block_index"] + 1) if prev_block else 0
        block = {
            "block_index":   block_index,
            "timestamp":     datetime.now(timezone.utc).isoformat(),
            "session_id":    session_id or str(uuid.uuid4()),
            "hostname":      socket.gethostname(),
            "ip_address":    _get_local_ip(),
            "query":         query.strip(),
            "chunk_types":   sorted(set(str(c) for c in chunk_types)),
            "prev_hash":     prev_hash,
        }

        # Step 4 — hash the block and attach
        block["block_hash"] = _compute_hash(block)

        # Step 5 — persist
        self._col.insert_one({**block})
        print(f"🔗 Audit block #{block_index} recorded | session={block['session_id']}")

        return {k: v for k, v in block.items() if k != "_id"}

    def verify(self) -> bool:
        blocks = list(self._col.find({}, {"_id": 0}).sort("block_index", 1))

        if not blocks:
            return True  # Empty chain is valid

        for i, block in enumerate(blocks):
            # Check 1 — hash integrity
            expected_hash = _compute_hash(block)
            if block.get("block_hash") != expected_hash:
                raise AuditChainTamperError(
                    f"Chain integrity violation at block #{block.get('block_index', i)}: "
                    f"stored hash does not match recomputed hash. "
                    f"The audit log may have been tampered with."
                )

            # Check 2 — chain linkage
            if i == 0:
                if block.get("prev_hash") != self.GENESIS_HASH:
                    raise AuditChainTamperError(
                        f"Genesis block has an unexpected prev_hash: {block.get('prev_hash')!r}"
                    )
            else:
                expected_prev = blocks[i - 1]["block_hash"]
                if block.get("prev_hash") != expected_prev:
                    raise AuditChainTamperError(
                        f"Chain linkage broken at block #{block.get('block_index', i)}: "
                        f"prev_hash does not match block #{block.get('block_index', i) - 1}'s hash."
                    )

        print(f"✅ Audit chain verified — {len(blocks)} block(s) intact.")
        return True

    def get_audit_trail(
        self,
        session_id: str | None = None,
        patient_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:

        query_filter: dict = {}

        if session_id:
            query_filter["session_id"] = session_id
        if patient_id:
            query_filter["patient_ids"] = patient_id  # MongoDB checks if value is in array

        blocks = list(
            self._col
            .find(query_filter, {"_id": 0})
            .sort("block_index", DESCENDING)
            .limit(limit)
        )
        return blocks

    def chain_length(self) -> int:

        return self._col.count_documents({})

    def _get_latest_block(self) -> dict | None:
        """Returns the most recently inserted block, or None if chain is empty."""
        return self._col.find_one({}, {"_id": 0}, sort=[("block_index", DESCENDING)])