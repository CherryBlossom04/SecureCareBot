import json
import time
import os
import re
from datetime import datetime

# --- Import your RAG components ---
from main import (
    initialize_databases,
    query_system,
    ACTIVE_LLM_MODEL,
    DEFAULT_LIMIT
)
from rag.milvus_db import MilvusSearchAndRetrieval
from rag.llm import LLMForQuery
from rag.mongo_db import MongoDBSearchAndRetrieval


# ---------------------------------------------------------
# 🔹 SCORING LOGIC (Fact & Chunk Accuracy)
# ---------------------------------------------------------
def extract_facts(text):
    if not text: return set()
    text = text.lower()
    numbers = re.findall(r'\d+\.?\d*', text)
    words = re.findall(r'[a-zA-Z]+', text)
    stopwords = {"the", "is", "was", "are", "and", "of", "to", "in", "with", "a", "an", "patient", "level", "value",
                 "report", "visit", "mg", "ml"}
    keywords = [w for w in words if w not in stopwords and len(w) > 3]
    return set(numbers + keywords)


def get_answer_accuracy(expected, actual):
    if not actual or not expected: return "None", 0.0
    exp_facts, act_facts = extract_facts(expected), extract_facts(actual)
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


# ---------------------------------------------------------
# 🔹 BATCH EVALUATION ENGINE
# ---------------------------------------------------------
def run_integrated_evaluation(input_path, output_path, batch_size=5):
    initialize_databases()

    if not os.path.exists(input_path):
        print(f"❌ Error: {input_path} not found.")
        return

    with open(input_path, 'r') as f:
        test_cases = json.load(f)

    # Initialize RAG Helpers
    query_helper = LLMForQuery(model_name=ACTIVE_LLM_MODEL)
    searcher = MilvusSearchAndRetrieval()
    mongo_search = MongoDBSearchAndRetrieval()

    total_cases = len(test_cases)
    print(f"\n🧪 Starting Integrated Evaluation: {total_cases} cases.")
    print(f"📦 Batch Size: {batch_size}")
    print("=" * 80)

    for i in range(0, total_cases, batch_size):
        batch = test_cases[i: i + batch_size]
        print(f"\n▶️ Processing Batch: {i // batch_size + 1} (Items {i + 1} to {min(i + batch_size, total_cases)})")

        for case in batch:
            query_text = case.get("query")
            start_time = time.perf_counter()

            try:
                # 1. Extraction & Querying
                extraction = mongo_search.get_patient_id_by_name(query_text, query_helper)
                p_id = extraction[0] if isinstance(extraction, (list, tuple)) else extraction
                p_name = extraction[1] if isinstance(extraction, (list, tuple)) and len(extraction) > 1 else "Unknown"

                actual_ans = query_system(query_text, limit=DEFAULT_LIMIT)

                # 2. Chunk Retrieval for Validation
                attributes = query_helper.extract_chunk_types(query_text)
                chunk_ids = []
                if p_id:
                    hits = searcher.search_hybrid(query=query_text, patient_id=p_id, limit=DEFAULT_LIMIT,
                                                  attribute=attributes)
                    for hit_list in hits:
                        for hit in hit_list:
                            c_id = hit.entity.get('chunk_id') if hasattr(hit, 'entity') else hit.get('chunk_id')
                            if c_id: chunk_ids.append(c_id)

                # 3. Scoring (The Integrated Logic)
                ans_lvl, ans_score = get_answer_accuracy(case.get("expected_answer"), actual_ans)
                chk_lvl, chk_score = get_chunk_accuracy(case.get("expected_chunks", []), chunk_ids)

                # 4. Update Case Data
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
                print(f" ✅ Case {case['test_case_id']} | Score: {ans_score} | Retrieval: {chk_lvl}")

            except Exception as e:
                print(f" ❌ Case {case.get('test_case_id')} Failed: {str(e)}")
                case["worked"] = f"Error: {str(e)}"

        # Intermediate Save (Save after every batch)
        with open(output_path, 'w') as f:
            json.dump(test_cases, f, indent=4)
        print(f"💾 Batch checkpoint saved to {output_path}")

    print("\n" + "=" * 80)
    print(f"🏁 Final Evaluation Complete. Results: {output_path}")


if __name__ == "__main__":
    # Update these paths to your actual locations
    INPUT_FILE = "../datas/queries.json"
    OUTPUT_FILE = "retrieval_eval_results.json"

    run_integrated_evaluation(INPUT_FILE, OUTPUT_FILE, batch_size=5)