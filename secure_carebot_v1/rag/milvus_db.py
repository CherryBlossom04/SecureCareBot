from abc import ABC, abstractmethod

from pymilvus import (
    AnnSearchRequest,
    DataType,
    Function,
    FunctionType,
    MilvusClient,
    RRFRanker,
)

from rag.decorators import dump_to_json, singleton
from rag.med_embedding_ollama import IMedEmbedding, MedEmbedderOllama


class IMilvusCollection(ABC):

    @abstractmethod
    def list_collections(self) -> list[str]:
        pass

    @abstractmethod
    def create_milvus_collection(self) -> None:
        pass


class IMilvusSearch(ABC):

    @abstractmethod
    def search_sparse(self, query, patient_id, limit, attribute=None, param=None): pass

    @abstractmethod
    def search_dense(self, query, patient_id, limit, attribute=None, param=None): pass

    @abstractmethod
    def search_hybrid(self, query, patient_id, limit, attribute=None, param=None): pass


@singleton
class MilvusDB(IMilvusCollection):

    def __init__(
        self,
        server_url: str = "http://localhost:19530",
        db_name: str = "securecarebot_db",
        collection_name: str | None = None,
        dense_embedder: IMedEmbedding | None = None,
    ):
        if not server_url or not isinstance(server_url, str):
            raise ValueError("server_url must be a non-empty string.")
        if not db_name or not isinstance(db_name, str):
            raise ValueError("db_name must be a non-empty string.")
        if collection_name is not None and not isinstance(collection_name, str):
            raise TypeError("collection_name must be a string or None.")
        if dense_embedder is not None and not isinstance(dense_embedder, IMedEmbedding):
            raise TypeError("dense_embedder must implement IMedEmbedding.")

        self.server_url     = server_url
        self.db_name        = db_name
        self.collection_name = collection_name
        self.dense_embedder = dense_embedder
        self.milvus_client: MilvusClient | None = None
        self.milvus_client  = self._init_client()
        self.create_milvus_collection()

    def _init_client(self) -> MilvusClient:
        if self.milvus_client is None:
            self.milvus_client = MilvusClient(uri=self.server_url, db_name=self.db_name)
        print("Milvus client initialized.")
        return self.milvus_client

    def list_collections(self) -> list[str]:
        self._require_client()
        return self.milvus_client.list_collections()

    def create_milvus_collection(self) -> None:
        if not self.collection_name:
            print("Collection name is not set; skipping collection creation.")
            return
        if self.collection_name in self.list_collections():
            print(f"Collection '{self.collection_name}' already exists.")
            return
        if self.collection_name == "meddataollama":
            print("Creating Ollama schema...")
            self.create_meddataollama_schema()
        else:
            print(f"Warning: No specific schema defined for '{self.collection_name}'.")

    def create_meddataollama_schema(self) -> None:
        self._require_client()
        if self.dense_embedder is None:
            raise ValueError("dense_embedder is required to create the collection.")

        dense_dim = self.dense_embedder.get_dims()
        schema = self.milvus_client.create_schema(auto_id=False, enable_dynamic_field=True)

        schema.add_field("chunk_id",    DataType.VARCHAR, max_length=64,   is_primary=True)
        schema.add_field("patient_id",  DataType.VARCHAR, max_length=64,   enable_analyzer=True)
        schema.add_field("chunk_type",  DataType.VARCHAR, max_length=64,   enable_analyzer=True)
        schema.add_field("text",        DataType.VARCHAR, max_length=8192, enable_analyzer=True)
        schema.add_field("dense_vector",  DataType.FLOAT_VECTOR,       dim=dense_dim)
        schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

        schema.add_function(Function(
            name="text_bm25_emb",
            input_field_names=["text"],
            output_field_names=["sparse_vector"],
            function_type=FunctionType.BM25,
        ))

        index_params = self.milvus_client.prepare_index_params()
        index_params.add_index(field_name="dense_vector",  index_type="FLAT",                 metric_type="COSINE")
        index_params.add_index(field_name="sparse_vector", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")

        self.milvus_client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )
        print("Collection created.")

    def _require_client(self) -> None:
        if self.milvus_client is None:
            raise RuntimeError("Milvus client is not initialised.")


class MilvusDataManipulation:
    def __init__(self):
        self.db = MilvusDB()

    def list_collections(self) -> list[str]:
        return self.db.list_collections()

    def delete_collection(self, collection_name: str | None = None) -> None:
        target = collection_name or self.db.collection_name
        if not target:
            raise ValueError("No collection name provided.")
        self.db._require_client()
        self.db.milvus_client.drop_collection(target)
        print(f"Collection '{target}' deleted.")

    def clear_collection(self) -> None:
        if not self.db.collection_name:
            raise ValueError("collection_name is not set.")
        self.db._require_client()
        self.db.milvus_client.delete(
            collection_name=self.db.collection_name,
            filter="chunk_id != ''",
        )
        print(f"All data removed from '{self.db.collection_name}'.")



class MilvusDataInsertion:
    def __init__(self, chunks: list[dict]):
        if not isinstance(chunks, list):
            raise TypeError(f"chunks must be a list of dicts, got {type(chunks).__name__}.")
        self.db = MilvusDB()
        self.insert_data_chunks(chunks)

    def insert_data_chunks(self, chunks: list[dict]) -> None:
        medical_chunks = [c for c in chunks if c.get("chunk_type") != "profile_identity"]

        if not medical_chunks:
            print("No medical data to insert.")
            return

        self.db._require_client()
        if self.db.dense_embedder is None:
            raise ValueError("dense_embedder is required for insertion.")

        text_list = [item["text"] for item in medical_chunks]
        if any(not t or not isinstance(t, str) for t in text_list):
            raise ValueError("Every chunk must have a non-empty 'text' field.")

        print("Computing embeddings...")
        embeddings = self.db.dense_embedder.encode(text_list)

        data = []
        for i, item in enumerate(medical_chunks):
            data.append({
                "chunk_id":     item["chunk_id"],
                "patient_id":   item["patient_id"],
                "chunk_type":   item["chunk_type"],
                "text":         item["text"],
                "dense_vector": embeddings[i],
            })

        dump_to_json(data, "datas/3_patients_summarized_text_for_vectordb.txt")

        self.db.milvus_client.insert(
            collection_name=self.db.collection_name,
            data=data,
        )
        print(f" Inserted {len(data)} chunks.")



class MilvusSearchAndRetrieval(IMilvusSearch):
    def __init__(self):
        self.db = MilvusDB()

    def build_filter(
        self,
        patient_ids: list[str] | str,
        visit_ids: list[str] | str | None = None,
        attribute: list[str] | None = None,
    ) -> str:
        p_ids = [patient_ids] if isinstance(patient_ids, str) else patient_ids
        v_ids = [visit_ids]   if isinstance(visit_ids, str)   else (visit_ids or [])

        if not p_ids:
            raise ValueError("At least one patient_id is required.")

        p_expr     = " || ".join([f'patient_id == "{pid.strip()}"' for pid in p_ids])
        filter_expr = f"({p_expr})"

        if v_ids:
            v_expr = " || ".join([f'chunk_id like "%{vid.strip()}%"' for vid in v_ids])
            filter_expr += f" && ({v_expr})"

        if attribute and "general" not in attribute:
            logic_keys  = {"difference", "compare", "between", "vs"}
            clean_attrs = [str(a).strip() for a in attribute
                           if a and str(a).strip().lower() not in logic_keys]
            if clean_attrs:
                attr_expr    = " || ".join([f'chunk_type like "%{cat}%"' for cat in clean_attrs])
                filter_expr += f" && ({attr_expr})"

        return filter_expr

    def search_sparse(self, query, patient_id, limit,
                      visit_id=None, attribute=None, param=None):
        self.validate_search_args(query, patient_id, limit)
        filter_expr = self.build_filter(patient_id, visit_id, attribute)
        return self.db.milvus_client.search(
            collection_name=self.db.collection_name,
            data=[query],
            anns_field="sparse_vector",
            limit=limit,
            filter=filter_expr,
            output_fields=["text", "patient_id"],
            **(param or {}),
        )

    def search_dense(self, query, patient_id, limit,
                     visit_id=None, attribute=None, param=None):
        self.validate_search_args(query, patient_id, limit)
        if self.db.dense_embedder is None:
            raise ValueError("dense_embedder is required for dense search.")
        filter_expr = self.build_filter(patient_id, visit_id, attribute)
        embeddings  = self.db.dense_embedder.encode([query])
        return self.db.milvus_client.search(
            collection_name=self.db.collection_name,
            data=embeddings,
            anns_field="dense_vector",
            limit=limit,
            filter=filter_expr,
            output_fields=["text", "patient_id"],
            **(param or {}),
        )

    def search_hybrid(self, query, patient_id, limit,
                      visit_id=None, attribute=None, param=None):
        self.validate_search_args(query, patient_id, limit)
        if self.db.dense_embedder is None:
            raise ValueError("dense_embedder is required for hybrid search.")

        filter_expr = self.build_filter(patient_id, visit_id, attribute)
        embeddings  = self.db.dense_embedder.encode([query])

        dense_req = AnnSearchRequest(
            data=embeddings,
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {"ef": 64}},
            limit=limit,
            expr=filter_expr,
        )
        sparse_req = AnnSearchRequest(
            data=[query],
            anns_field="sparse_vector",
            param={"metric_type": "BM25", "params": {"drop_ratio_search": 0.1}},
            limit=limit,
            expr=filter_expr,
        )

        return self.db.milvus_client.hybrid_search(
            collection_name=self.db.collection_name,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=60),
            limit=limit,
            output_fields=["text", "patient_id"],
        )

    @staticmethod
    def validate_search_args(query: str, patient_id: list[str] | str, limit: int) -> None:
        if not query or not isinstance(query, str):
            raise ValueError("query must be a non-empty string.")
        if isinstance(patient_id, str):
            if not patient_id.strip():
                raise ValueError("patient_id string cannot be empty.")
        elif isinstance(patient_id, list):
            if not patient_id:
                raise ValueError("patient_id list cannot be empty.")
            if not all(isinstance(pid, str) and pid.strip() for pid in patient_id):
                raise ValueError("All patient_ids must be non-empty strings.")
        else:
            raise TypeError("patient_id must be a string or list of strings.")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer.")