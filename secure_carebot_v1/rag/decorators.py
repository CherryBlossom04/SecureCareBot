import json
from typing import Any
def singleton(cls):
    """Ensures only one instance of a class is created."""
    instances = {}

    def wrapper(*args, **kwargs):
        if cls not in instances:
            instances[cls] = cls(*args, **kwargs)
        return instances[cls]

    return wrapper


def dump_to_json(data: Any, path: str) -> None:
    """Serializes data to a JSON file at the given path."""
    if not path or not isinstance(path, str):
        raise ValueError("A valid file path string must be provided.")
    if data is None:
        raise ValueError("Data to serialize cannot be None.")

    with open(path, "w") as f:
        json.dump(data, f, indent=4)
    print(f"Data saved to {path}")


def print_result(results: list) -> None:
    """Pretty-prints Milvus search results."""
    if not results or not results[0]:
        print("No results to display.")
        return

    print(f"Found {len(results[0])} results..")
    for item in results[0]:
        text = item.get("entity", {}).get("text", "")
        print(f"Score: {item['distance']:.4f} | {text}")


def load_json_data(file: str) -> Any:
    """Loads and returns data from a JSON file."""
    if not file or not isinstance(file, str):
        raise ValueError("A valid file path string must be provided.")

    with open(file) as f:
        return json.load(f)
