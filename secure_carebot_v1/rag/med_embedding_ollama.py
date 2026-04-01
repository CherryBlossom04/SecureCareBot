import os
from abc import ABC, abstractmethod
import ollama

os.environ["CUDA_VISIBLE_DEVICES"] = ""

class IMedEmbedding(ABC):

    def __init__(self, model_name: str, device: str):
        if not model_name or not isinstance(model_name, str):
            raise ValueError("A non-empty model_name string must be provided.")
        if not device or not isinstance(device, str):
            raise ValueError("A non-empty device string must be provided.")

        self.model_name = model_name
        self.device = device

    @abstractmethod
    def get_dims(self) -> int:
        pass

    @abstractmethod
    def encode(self, chunks: list[str]) -> list[list[float]]:
        pass

    @abstractmethod
    def get_med_embedding_model(self, model_name: str, device: str):
        pass


class MedEmbedderOllama(IMedEmbedding):

    EMBEDDING_DIM = 1024

    def __init__(self, model_name: str = "qwen3-embedding:0.6b", device: str = "cpu"):
        super().__init__(model_name, device)
        self._med_model: str | None = None
        self._med_model = self.get_med_embedding_model(model_name, device)

    def get_med_embedding_model(self, model_name: str = "qwen3-embedding:0.6b", device: str = "cpu") -> str:
        if self._med_model is None:
            self._med_model = model_name
        return self._med_model

    def get_dims(self) -> int:
        return self.EMBEDDING_DIM

    def encode(self, chunks: list[str]) -> list[list[float]]:
        if not isinstance(chunks, list):
            raise TypeError(f"Expected a list of strings, got {type(chunks).__name__}.")
        if not chunks:
            return []
        if not all(isinstance(c, str) for c in chunks):
            raise TypeError("All items in chunks must be strings.")

        embeddings: list[list[float]] = []
        for text in chunks:
            if not text.strip():
                raise ValueError("Cannot embed an empty or whitespace-only string.")
            response = ollama.embeddings(model=self._med_model, prompt=text)
            embeddings.append(response["embedding"])

        return embeddings
