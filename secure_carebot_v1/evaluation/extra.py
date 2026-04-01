import json

def reindex_test_cases(data):
    # Iterate through the list and update the ID to be 1-based index
    for index, item in enumerate(data, start=1):
        item["test_case_id"] = str(index)
    return data

# --- EXECUTION ---
# Assuming your data is stored in a file named 'test_cases.json'
file_path = "/home/sowmya/Documents/main-project/secure_carebot_v1/datas/queries.json"

try:
    with open(file_path, "r") as f:
        test_data = json.load(f)

    # Re-index
    updated_data = reindex_test_cases(test_data)

    # Save the updated data back to a file
    output_path = "/home/sowmya/Documents/main-project/secure_carebot_v1/datas/queries.json"
    with open(output_path, "w") as f:
        json.dump(updated_data, f, indent=4)

    print(f"✅ Re-indexing complete! {len(updated_data)} cases processed.")
    print(f"📂 Saved to: {output_path}")

except FileNotFoundError:
    print("❌ Error: The input file was not found.")
except Exception as e:
    print(f"❌ An error occurred: {e}")