

from abc import ABC, abstractmethod
import ollama
import numpy as np

class IReranker(ABC):

    @abstractmethod
    def rerank(self, query: str, results: list, top_k: int) -> list:
        pass


class BGEReranker(IReranker):
    def __init__(
        self,
        model_name: str = "qllama/bge-reranker-large:q4_k_m",
        score_threshold: float = 0.0,
    ):
        if not model_name or not isinstance(model_name, str):
            raise ValueError("model_name must be a non-empty string.")
        if not (0.0 <= score_threshold <= 1.0):
            raise ValueError("score_threshold must be between 0.0 and 1.0.")

        self.model_name      = model_name
        self.score_threshold = score_threshold


    def rerank(self, query: str, results: list, top_k: int = 5) -> list:
        if not query or not isinstance(query, str):
            raise ValueError("query must be a non-empty string.")
        if not isinstance(results, list) or not results:
            print("Reranker received empty results; returning as-is.")
            return results
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer.")

        # Flatten: hybrid search returns [[hit, hit, ...]]
        hits = results[0] if isinstance(results[0], list) else results

        if not hits:
            print("No hits to rerank.")
            return results

        print(f"\n Reranking {len(hits)} chunks with BGE reranker...")

        scored_hits = []
        for hit in hits:
            text = self._extract_text(hit)
            if not text:
                continue

            score = self._score(query, text)

            # Attach rerank score to entity for downstream inspection
            if hasattr(hit, "entity"):
                hit.entity["rerank_score"] = score
            elif isinstance(hit, dict):
                hit.setdefault("entity", {})["rerank_score"] = score

            scored_hits.append((score, hit))

        # Filter by threshold, sort descending by score
        filtered = [(s, h) for s, h in scored_hits if s >= self.score_threshold]
        filtered.sort(key=lambda x: x[0], reverse=True)

        top_hits = [h for _, h in filtered[:top_k]]

        print(f"✅ Reranking complete. Returning top {len(top_hits)} chunks.")
        for i, (score, hit) in enumerate(filtered[:top_k], 1):
            preview = self._extract_text(hit)[:80].replace("\n", " ")
            print(f"  [{i}] score={score:.4f} | {preview}...")
        return [top_hits]



    def _score(self, query: str, document: str) -> float:
        try:
            response = ollama.embed(
                model=self.model_name,
                input=[query, document],
            )

            query_vec = np.array(response["embeddings"][0])
            doc_vec = np.array(response["embeddings"][1])

            # cosine similarity
            score = np.dot(query_vec, doc_vec) / (
                    np.linalg.norm(query_vec) * np.linalg.norm(doc_vec)
            )

            return float(score)

        except Exception as e:
            print(f" BGE reranker scoring failed: {e}")
            return 0.0

    @staticmethod
    def _extract_text(hit) -> str:
        """Extracts the text field from a Milvus hit object or dict."""
        try:
            if hasattr(hit, "entity"):
                return hit.entity.get("text", "")
            return hit.get("entity", {}).get("text", "")
        except Exception:
            return ""