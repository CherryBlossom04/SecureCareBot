import json
import re
from abc import ABC, abstractmethod
import ollama

from rag.patient_data_to_chunks import PatientDataToChunksForQwen2
from rag.prompt_templates import (
    EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE,
    EXTRACT_NAME_PROMPT_TEMPLATE,
    SPLIT_QUERY_PROMPT_TEMPLATE,
    SUMMARIZE_PROMPT_TEMPLATE,
    VALID_CHUNK_CATEGORIES, RAG_CHAT_PROMPT_TEMPLATE,
)


def ollama_chat(model: str, prompt: str, temperature: float = 0.1, num_predict: int = None, stream: bool = False):
    if not model or not isinstance(model, str):
        raise ValueError("model must be a non-empty string.")

    options = {"temperature": temperature}
    if num_predict:
        options["num_predict"] = num_predict

    response = ollama.chat(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        options=options,
        stream=stream,  # Enable streaming here
    )

    if stream:
        return response  # This is now an iterable

    return response["message"]["content"].strip()


class ILLMForVector(ABC):
    def __init__(self, model_name: str, patients: list[dict] = None):
        """
        patients is now optional to prevent NoneType errors during initialization.
        """
        self.model_name = model_name
        # Default to empty list if None is passed
        self.patients = patients if patients is not None else []

        if not isinstance(self.patients, list):
            raise TypeError(f"patients must be a list of dicts, got {type(self.patients).__name__}.")

    @abstractmethod
    def summarize_chunk(self, header: str, data: dict) -> str:
        pass

    @abstractmethod
    def summarize(self, structured_chunks: dict[str, dict]) -> list[dict]:
        """Signature updated to accept structured_chunks directly."""
        pass

    @staticmethod
    def _dump_to_json(data: list[dict], path: str) -> None:
        with open(path, "w") as f:
            json.dump(data, f, indent=4)
        print(f"Data saved to {path}")


class LLMforSummarization(ILLMForVector):
    def summarize(self, structured_chunks: dict[str, dict]) -> list[dict]:
        """
        Summarizes structured medical data chunks into natural language sentences.
        """
        model_label = self.model_name.split(":")[0].upper()
        print(f"\n=========== SUMMARISING {len(structured_chunks)} RECORDS USING {model_label} ===========")

        summarized_chunks: list[dict] = []

        # Iterate through the nested dictionary: { patient_id: { chunk_type: data } }
        for patient_id, categories in structured_chunks.items():
            for chunk_type, chunk_data in categories.items():
                # Create a clean header for the LLM context
                header = f"{patient_id} | {chunk_type}"

                # Generate the natural language summary
                paragraph = self.summarize_chunk(header, chunk_data)
                print(paragraph)

                # Consistent ID for database storage
                chunk_id = f"{patient_id}_{chunk_type}"

                print(f"--- Processed: {chunk_id} ---")

                summarized_chunks.append({
                    "chunk_id": chunk_id,
                    "patient_id": patient_id,
                    "chunk_type": chunk_type,
                    "text": paragraph,
                })

        self._dump_to_json(summarized_chunks, "datas/2_patients_data_chunks.json")
        return summarized_chunks


class LLMForVector:
    class Qwen25(LLMforSummarization):
        def __init__(self, model_name: str, patients: list[dict] = None):
            # Pass arguments to LLMforSummarization -> ILLMForVector
            super().__init__(model_name=model_name, patients=patients)

        def summarize_chunk(self, header: str, data: dict) -> str:
            # Uses the template to generate concise clinical sentences
            prompt = SUMMARIZE_PROMPT_TEMPLATE.format(
                header=header,
                data=json.dumps(data, indent=2),
            )
            return ollama_chat(self.model_name, prompt, temperature=0.5)


class LLMForQuery:
    def __init__(self, model_name: str):
        self.model_name = model_name

    def extract_name(self, query: str) -> str | None:
        prompt = EXTRACT_NAME_PROMPT_TEMPLATE.format(query=query)
        result = ollama_chat(self.model_name, prompt, temperature=0.1)
        # Handle potential multi-line responses from LLM
        name = result.split("\n")[0].strip()
        print(f"Extracted name: {name!r}")
        return None if name.lower() == "none" else name

    def extract_chunk_types(self, query: str) -> list[str]:
        prompt = EXTRACT_CHUNK_TYPE_PROMPT_TEMPLATE.format(query=query)
        result = ollama_chat(self.model_name, prompt, temperature=0.1)

        extracted = [
            cat.strip()
            for cat in result.lower().replace("\n", ",").split(",")
            if cat.strip() in VALID_CHUNK_CATEGORIES
        ]
        return extracted if extracted else ["general"]

    def split_query(self, query: str) -> str:
        prompt = SPLIT_QUERY_PROMPT_TEMPLATE.format(query=query)
        return ollama_chat(self.model_name, prompt, temperature=0.1)

    def generate_chat_response(self, search_results: list, query: str, length_limit: str = "3 to 8 sentences") -> str:
        # Handles Milvus result object vs simple list
        hits = search_results[0] if search_results and isinstance(search_results[0], list) else []

        context_fragments = []
        for item in hits:
            try:
                # Support for both dictionary and object-style entities
                if hasattr(item, 'entity'):
                    text = item.entity.get("text", "")
                else:
                    text = item.get("entity", {}).get("text", "")

                if text:
                    context_fragments.append(text)
            except Exception:
                continue

        context_text = "\n".join(context_fragments) if context_fragments else "No specific patient records found."

        prompt = RAG_CHAT_PROMPT_TEMPLATE.format(
            context=context_text,
            query=query,
            length_limit=length_limit
        )

        # Call with stream=True
        stream_gen = ollama_chat(
            model=self.model_name,
            prompt=prompt,
            temperature=0.2,
            num_predict=500,
            stream=True
        )

        full_response = ""
        print("\n" + "✨" * 30)
        print(" FINAL ASSISTANT RESPONSE ")
        print("✨" * 30)

        for chunk in stream_gen:
            content = chunk['message']['content']
            print(content, end='', flush=True)  # Print to terminal in real-time
            full_response += content

        print("\n" + "✨" * 30)
        return full_response