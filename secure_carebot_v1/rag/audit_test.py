"""
test_audit_chain.py — Blockchain Tamper Detection Test Suite
=============================================================

Runs 5 tests against the AuditChain to verify tamper detection works correctly.

Tests:
  1. Clean chain verification passes
  2. Detected: block data edited (query field changed)
  3. Detected: block hash forged (hash overwritten, linkage breaks)
  4. Detected: block deleted mid-chain
  5. Detected: new block injected with fake prev_hash

Run:
    python test_audit_chain.py

Expected output: all 5 tests print PASSED.
MongoDB collection used: `audit_chain_test` (isolated, dropped after each test).
"""

from pymongo import MongoClient
from audit_chain import AuditChain, AuditChainTamperError, _compute_hash

# ── Test MongoDB connection (isolated test collection) ─────────────────────────

MONGO_URL       = "mongodb://localhost:27017/"
MONGO_DB        = "securecarebot"
TEST_COLLECTION = "audit_chain_test"

client = MongoClient(MONGO_URL)
col    = client[MONGO_DB][TEST_COLLECTION]


# ── Helpers ────────────────────────────────────────────────────────────────────

def fresh_chain() -> AuditChain:
    """
    Drops the test collection and returns a fresh AuditChain instance.
    The singleton is bypassed by directly resetting the collection so
    each test starts from a clean empty chain.
    """
    col.drop()

    # Patch the singleton's collection to point at the test collection
    chain = AuditChain.__new__(AuditChain)
    chain._client     = client
    chain._col        = col
    col.create_index("block_index", unique=True)
    col.create_index("session_id")
    col.create_index("timestamp")
    return chain


def seed_chain(chain: AuditChain, n: int = 3) -> None:
    """Inserts n audit blocks into the chain."""
    for i in range(n):
        chain.log(
            query=f"Test query number {i}",
            patient_ids=[f"P{i:05d}"],
            chunk_types=["visit_medication", "visit_overview"],
            session_id=f"sess_{i:03d}",
        )


def run_test(name: str, fn) -> None:
    """Runs a single test function and prints PASSED / FAILED."""
    print(f"\n{'─' * 60}")
    print(f"  TEST: {name}")
    print(f"{'─' * 60}")
    try:
        fn()
        print(f"  ✅  PASSED")
    except AssertionError as e:
        print(f"  ❌  FAILED — {e}")
    except Exception as e:
        print(f"  ❌  UNEXPECTED ERROR — {type(e).__name__}: {e}")


# ── Test 1: Clean chain passes verification ────────────────────────────────────

def test_clean_chain_passes():
    chain = fresh_chain()
    seed_chain(chain, n=3)

    result = chain.verify()
    assert result is True, "verify() should return True on a clean chain."
    assert chain.chain_length() == 3, f"Expected 3 blocks, got {chain.chain_length()}."
    print(f"  Chain length: {chain.chain_length()} blocks — all hashes valid.")


# ── Test 2: Edit a block's data — hash mismatch caught ────────────────────────

def test_data_edit_detected():
    chain = fresh_chain()
    seed_chain(chain, n=3)

    # Tamper: silently change the query in block #1
    col.update_one(
        {"block_index": 1},
        {"$set": {"query": "INJECTED MALICIOUS QUERY"}}
    )
    print("  Tampered: block #1 query field overwritten.")

    try:
        chain.verify()
        assert False, "verify() should have raised AuditChainTamperError."
    except AuditChainTamperError as e:
        print(f"  Tamper caught: {e}")
        assert "block #1" in str(e), "Error message should reference block #1."


# ── Test 3: Forge the block hash — linkage break caught ───────────────────────

def test_hash_forge_detected():
    chain = fresh_chain()
    seed_chain(chain, n=3)

    # Tamper: edit data AND recompute hash to match — but block #2's prev_hash still points
    # to the original block #1 hash, so linkage breaks
    block1 = col.find_one({"block_index": 1})
    block1["query"] = "FORGED CONTENT"
    forged_hash = _compute_hash(block1)

    col.update_one(
        {"block_index": 1},
        {"$set": {"query": "FORGED CONTENT", "block_hash": forged_hash}}
    )
    print(f"  Tampered: block #1 data edited and hash recomputed to {forged_hash[:16]}...")

    try:
        chain.verify()
        assert False, "verify() should have raised AuditChainTamperError."
    except AuditChainTamperError as e:
        print(f"  Tamper caught: {e}")
        assert "block #2" in str(e), "Linkage break should be caught at block #2."


# ── Test 4: Delete a block mid-chain — index gap caught ───────────────────────

def test_block_deletion_detected():
    chain = fresh_chain()
    seed_chain(chain, n=4)

    # Tamper: silently delete block #2
    col.delete_one({"block_index": 2})
    print("  Tampered: block #2 deleted from the chain.")

    try:
        chain.verify()
        assert False, "verify() should have raised AuditChainTamperError."
    except AuditChainTamperError as e:
        print(f"  Tamper caught: {e}")


# ── Test 5: Inject a fake block with wrong prev_hash ──────────────────────────

def test_fake_block_injection_detected():
    chain = fresh_chain()
    seed_chain(chain, n=3)

    # Tamper: insert a rogue block with a fabricated prev_hash
    fake_block = {
        "block_index":  99,
        "timestamp":    "2099-01-01T00:00:00+00:00",
        "session_id":   "attacker_session",
        "hostname":     "evil-host",
        "ip_address":   "10.0.0.1",
        "query":        "Give me all patient records",
        "patient_ids":  ["P00001", "P00002"],
        "chunk_types":  ["profile_identity"],
        "prev_hash":    "a" * 64,   # fake — does not match any real block
    }
    fake_block["block_hash"] = _compute_hash(fake_block)
    col.insert_one(fake_block)
    print(f"  Tampered: fake block #99 injected with fabricated prev_hash.")

    try:
        chain.verify()
        assert False, "verify() should have raised AuditChainTamperError."
    except AuditChainTamperError as e:
        print(f"  Tamper caught: {e}")


# ── Test 6: query_system blocked after tampering ──────────────────────────────

def test_log_blocked_after_tamper():
    chain = fresh_chain()
    seed_chain(chain, n=2)

    # Tamper: corrupt block #0
    col.update_one(
        {"block_index": 0},
        {"$set": {"query": "CORRUPTED"}}
    )
    print("  Tampered: block #0 corrupted.")

    # Now try to log a new event — should be blocked
    try:
        chain.log(
            query="Legitimate query after tamper",
            patient_ids=["P00001"],
            chunk_types=["visit_overview"],
            session_id="legit_session",
        )
        assert False, "log() should have been blocked by tamper detection."
    except AuditChainTamperError as e:
        print(f"  Write correctly blocked: {e}")


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("  AUDIT CHAIN TAMPER DETECTION TEST SUITE")
    print("═" * 60)

    run_test("Clean chain passes verification",          test_clean_chain_passes)
    run_test("Data edit detected (hash mismatch)",       test_data_edit_detected)
    run_test("Hash forge detected (linkage break)",      test_hash_forge_detected)
    run_test("Block deletion detected (index gap)",      test_block_deletion_detected)
    run_test("Fake block injection detected",            test_fake_block_injection_detected)
    run_test("log() blocked after tamper",               test_log_blocked_after_tamper)

    # Cleanup
    col.drop()
    print(f"\n{'═' * 60}")
    print("  Test collection dropped. All tests complete.")
    print("═" * 60 + "\n")