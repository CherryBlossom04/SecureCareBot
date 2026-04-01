
import ast
import re
from abc import ABC, abstractmethod

from pymongo import MongoClient
from pymongo.errors import ConnectionFailure

from rag.decorators import dump_to_json, singleton
from rag.encrypt_decrypt import encrypt_patient_data, hash_text
from rag.llm import LLMForQuery


class MongoBase:

    def __init__(self):
        self.mongo    = MongoDB()
        self.mongo.require_db()
        self._col_name = self.mongo.require_collection_name()
        self.collection = self.mongo.db[self._col_name]


@singleton
class MongoDB:

    def __init__(
        self,
        url: str = "mongodb://localhost:27017/",
        db_name: str = "securecarebot",
        collection_name: str | None = None,
    ):
        if not url or not isinstance(url, str):
            raise ValueError("url must be a non-empty string.")
        if not db_name or not isinstance(db_name, str):
            raise ValueError("db_name must be a non-empty string.")
        if collection_name is not None and not isinstance(collection_name, str):
            raise TypeError("collection_name must be a string or None.")

        self.url             = url
        self.db_name         = db_name
        self.collection_name = collection_name
        self.client: MongoClient | None = None
        self.db = None
        self.init_client()

    def init_client(self) -> None:
        try:
            if self.client is None:
                self.client = MongoClient(self.url, serverSelectionTimeoutMS=5000)
                self.client.admin.command("ping")
            self.db = self.client[self.db_name]
        except ConnectionFailure:
            print("Could not connect to MongoDB.")
            raise

    def list_collections(self) -> list[str]:
        self.require_db()
        return self.db.list_collection_names()

    def require_db(self) -> None:
        if self.db is None:
            raise RuntimeError("MongoDB database handle is not initialised.")

    def require_collection_name(self) -> str:
        if not self.collection_name:
            raise ValueError("collection_name is not set on the MongoDB singleton.")
        return self.collection_name


class MongoDBManipulation(MongoBase):
    def list_collections(self) -> list[str]:
        collections = self.mongo.list_collections()
        print(f"Collections in DB: {collections}")
        return collections

    def clear_collection(self, collection_name: str | None = None) -> None:
        target = collection_name or self._col_name
        result = self.mongo.db[target].delete_many({})
        print(f"Cleared {result.deleted_count} documents from '{target}'.")

    def delete_collection(self, collection_name: str | None = None) -> None:
        target = collection_name or self._col_name
        self.mongo.db[target].drop()
        print(f"Collection '{target}' has been dropped.")


class MongoDBDataInsertion(MongoBase):

    def insert_profile_chunks(self, json_data: list[dict]) -> None:
        if not isinstance(json_data, list):
            raise TypeError(f"json_data must be a list of dicts, got {type(json_data).__name__}.")
        if not json_data:
            print("No data provided to insert_profile_chunks.")
            return

        query_helper = LLMForQuery(model_name="qwen2.5:1.5b-instruct-q4_K_M")

        for chunk in json_data:
            if chunk.get("chunk_type") != "profile_identity":
                continue

            text = chunk.get("text")
            if not text or not isinstance(text, str):
                print(f"Skipping invalid chunk: {chunk.get('chunk_id')}")
                continue

            raw_name = query_helper.extract_name(text)
            if not raw_name:
                print(f"Could not extract name from chunk: {chunk.get('chunk_id')}")
                continue

            clean_name = re.sub(r"[\[\]\"']", "", str(raw_name)).strip()
            normalized_name = clean_name.lower()

            patient_id = chunk["patient_id"]

            existing = self.collection.find_one({"patient_id": patient_id})
            if existing:
                print(f"Patient already exists, skipping: {patient_id}")
                continue

            document = {
                "patient_id": patient_id,
                "chunk_type": chunk["chunk_type"],
                "name_hash":  hash_text(normalized_name), # Hashing the clean, lowercase name
                "text_enc":   encrypt_patient_data(text, patient_id),
            }

            dump_to_json(
                {k: v for k, v in document.items() if k != "text_enc"},
                "datas/4_patients_personal_data_audit.json"
            )

            self.collection.insert_one(document)
            print(f"✅ Inserted: {clean_name} (ID: {patient_id})")


class MongoDBSearchAndRetrieval(MongoBase):

    def extract_and_clean_query(self, name_to_id_map: dict[str, str], query: str) -> str:
        if not name_to_id_map or not query:
            return query

        sorted_names = sorted(name_to_id_map.keys(), key=len, reverse=True)
        clean_query = query

        for name in sorted_names:
            p_id = name_to_id_map[name]
            pattern = re.compile(rf"\b{re.escape(name)}(['sS]*)\b", re.IGNORECASE)
            clean_query = pattern.sub(rf"{p_id}\1", clean_query)

        return re.sub(r"\s+", " ", clean_query).strip()

    def get_patient_id_by_name(
            self,
            query: str,
            query_helper,
    ) -> tuple[list[str], list[str], str]:
        if not query or not isinstance(query, str):
            return [], [], ""

        visit_id_pattern = r"\bV\d{5,10}\b"
        id_pattern = r"\b[pP]\d{3,}\b"

        visit_ids = list(set(v.upper() for v in re.findall(visit_id_pattern, query, re.IGNORECASE)))
        patient_ids_set = set(p.upper() for p in re.findall(id_pattern, query))

        # 1. Extract Names (Expecting Comma-Separated String)
        raw_response = query_helper.extract_name(query)
        # print(f"--- STEP 1: Raw LLM String ---\n{raw_response}")

        parsed_names = []
        if raw_response and isinstance(raw_response, str):
            # If prompt returns 'NONE', skip processing
            if raw_response.strip().upper() == "NONE":
                print("--- STEP 2: No names found by LLM ---")
            else:
                # 2. Aggressive Cleaning of artifacts (brackets, quotes)
                clean_str = re.sub(r"[\[\]\"']", "", raw_response).strip()
                # print(f"--- STEP 2: Artifacts Removed ---\n{clean_str}")

                # 3. Split by comma and strip each name
                parsed_names = [n.strip() for n in clean_str.split(",") if n.strip()]
                print(f"--- STEP 3: Split Result ---\n{parsed_names}")

        # Filter out anything that accidentally looks like an ID
        actual_names = [n for n in parsed_names if not re.fullmatch(id_pattern, n, re.IGNORECASE)]

        name_to_id_map: dict[str, str] = {}
        for name in actual_names:
            if not name or len(name) < 2:
                continue

            # 4. Resolve via Hash
            search_name = name.lower().strip()
            name_hash = hash_text(search_name)

            print(f"--- DB LOOKUP: '{search_name}' | Hash: {name_hash} ---")
            results = self.collection.find({"name_hash": name_hash})

            found_any = False
            for r in results:
                p_id = r["patient_id"]
                patient_ids_set.add(p_id)
                if not found_any:
                    name_to_id_map[name] = p_id
                    found_any = True

            if not found_any:
                print(f"--- WARNING: No ID found in DB for '{search_name}' ---")

        cleaned_query = self.extract_and_clean_query(name_to_id_map, query)
        final_patient_ids = list(patient_ids_set)

        print(f"--- FINALIDs: {final_patient_ids} ---")
        return final_patient_ids, visit_ids, cleaned_query