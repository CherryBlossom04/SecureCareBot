import json
import time
import os
import re
import sys
from datetime import datetime

# -----------------------------
# 🔹 SETUP PROJECT PATHS
# -----------------------------
# This allows evaluation.py to import from the parent directory (secure_carebot_v1)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(CURRENT_DIR)
sys.path.append(PARENT_DIR)

from main import (
    initialize_databases,
    query_system,
    ACTIVE_LLM_MODEL,
    DEFAULT_LIMIT
)
from rag.milvus_db import MilvusSearchAndRetrieval
from rag.llm import LLMForQuery
from rag.mongo_db import MongoDBSearchAndRetrieval


# -----------------------------
# 🔹 SCORING LOGIC
# -----------------------------
def extract_facts(text):
    if not text: return set()
    text = text.lower()
    numbers = re.findall(r'\d+\.?\d*', text)
    words = re.findall(r'[a-zA-Z]+', text)
    stopwords = {"the", "is", "was", "are", "and", "of", "to", "in", "with", "a", "an", "patient", "visit", "mg", "ml"}
    keywords = [w for w in words if w not in stopwords and len(w) > 2]
    return set(numbers + keywords)


def get_answer_accuracy(expected, actual):
    if not actual or not expected: return "None", 0.0
    exp_facts = extract_facts(expected)
    act_facts = extract_facts(actual)
    if not exp_facts: return "None", 0.0
    matched = exp_facts.intersection(act_facts)
    score = round(len(matched) / len(exp_facts), 3)
    level = "High" if score >= 0.8 else "Medium" if score >= 0.5 else "Low" if score > 0 else "None"
    return level, score


def get_chunk_accuracy(expected_chunks, actual_chunks):
    exp, act = set(expected_chunks), set(actual_chunks)
    if not act or not exp: return "None", 0.0
    intersection = exp.intersection(act)
    score = round(len(intersection) / len(exp), 3)
    level = "Exact" if score == 1.0 else "Partial" if score >= 0.5 else "Low" if score > 0 else "None"
    return level, score


# -----------------------------
# 🔹 BATCH EVALUATION ENGINE
# -----------------------------
def run_evaluation(input_path, output_path, batch_size=5):
    initialize_databases()

    # 1. LOAD DATA (Prioritize the output/checkpoint file)
    if os.path.exists(output_path):
        print(f"🔄 Found existing results at {output_path}. Checking for missing answers...")
        with open(output_path, 'r') as f:
            test_cases = json.load(f)
    elif os.path.exists(input_path):
        print(f"📂 No checkpoint found. Starting fresh from {input_path}.")
        with open(input_path, 'r') as f:
            test_cases = json.load(f)
    else:
        print(f"❌ Error: Input file {input_path} not found.")
        return

    # Initialize RAG Helpers
    query_helper = LLMForQuery(model_name=ACTIVE_LLM_MODEL)
    searcher = MilvusSearchAndRetrieval()
    mongo_search = MongoDBSearchAndRetrieval()

    total_cases = len(test_cases)

    # 2. IDENTIFY REMAINING WORK
    # We skip if 'actual_answer' exists and is not empty/None
    completed_indices = [i for i, c in enumerate(test_cases) if c.get("actual_answer")]
    print(f"📊 Progress: {len(completed_indices)}/{total_cases} cases already have answers.")
    print("=" * 80)

    for i in range(0, total_cases, batch_size):
        batch_indices = range(i, min(i + batch_size, total_cases))

        # Check if the whole batch is already done
        if all(test_cases[idx].get("actual_answer") for idx in batch_indices):
            continue

        print(f"\n▶️ Processing Batch {i // batch_size + 1}")

        for idx in batch_indices:
            case = test_cases[idx]

            # 3. INDIVIDUAL SKIP CHECK
            if case.get("actual_answer"):
                print(f" ⏩ ID {case.get('test_case_id')} | Answer exists. Skipping.")
                continue

            query_text = case.get("query")
            start_time = time.perf_counter()

            try:
                # Identity Extraction
                extraction = mongo_search.get_patient_id_by_name(query_text, query_helper)
                p_id = extraction[0] if isinstance(extraction, (list, tuple)) else extraction
                p_name = extraction[1] if isinstance(extraction, (list, tuple)) and len(extraction) > 1 else "Unknown"

                # RAG Query
                actual_ans = query_system(query_text, limit=DEFAULT_LIMIT)

                # Retrieval Check (Milvus)
                attributes = query_helper.extract_chunk_types(query_text)
                chunk_ids = []
                if p_id:
                    hits = searcher.search_hybrid(query_text, p_id, limit=DEFAULT_LIMIT, attribute=attributes)
                    for hit_list in hits:
                        for hit in hit_list:
                            c_id = hit.entity.get('chunk_id') if hasattr(hit, 'entity') else hit.get('chunk_id')
                            if c_id: chunk_ids.append(c_id)

                # Scoring
                ans_lvl, ans_score = get_answer_accuracy(case.get("expected_answer"), actual_ans)
                chk_lvl, chk_score = get_chunk_accuracy(case.get("expected_chunks", []), chunk_ids)

                # Update Record
                case.update({
                    "actual_answer": actual_ans,
                    "target_chunks": chunk_ids,
                    "extracted_patient_id": p_id,
                    "extracted_patient_name": p_name,
                    "time_taken_ms": round((time.perf_counter() - start_time) * 1000, 2),
                    "answer_accuracy": ans_lvl,
                    "answer_fact_score": ans_score,
                    "chunk_accuracy": chk_lvl,
                    "chunk_score": chk_score,
                    "worked": "Yes" if (actual_ans and "None" not in str(actual_ans)) else "No"
                })
                print(f" ✅ ID {case['test_case_id']} | Score: {ans_score}")

            except Exception as e:
                print(f" ❌ ID {case.get('test_case_id')} Error: {str(e)}")
                # We don't set actual_answer here so it retries on next run
                case["worked"] = f"Error: {str(e)}"

        # 4. SAVE AFTER EVERY BATCH
        with open(output_path, 'w') as f:
            json.dump(test_cases, f, indent=4)
        print(f"💾 Checkpoint saved to {output_path}")

    print("\n" + "=" * 80)
    print(f"🏁 Final Results saved to: {output_path}")


if __name__ == "__main__":
    INPUT_FILE = "/home/sowmya/Documents/main-project/secure_carebot_v1/datas/queries2.json"
    OUTPUT_FILE = "/home/sowmya/Documents/main-project/secure_carebot_v1/evaluation/retrieval_eval_results.json"

    run_evaluation(INPUT_FILE, OUTPUT_FILE, batch_size=5)