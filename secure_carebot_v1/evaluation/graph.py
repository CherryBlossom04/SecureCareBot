import json
import os
import pandas as pd
import matplotlib.pyplot as plt

IMAGE_DIR = "evaluation/images"
os.makedirs(IMAGE_DIR, exist_ok=True)


def load_and_prepare_data(file_path):
    if not os.path.exists(file_path):
        return None
    with open(file_path, "r") as f:
        data = json.load(f)
    df = pd.DataFrame(data)
    # Ensure numeric for calculations
    df['answer_fact_score'] = pd.to_numeric(df['answer_fact_score'], errors='coerce').fillna(0)
    df['chunk_score'] = pd.to_numeric(df['chunk_score'], errors='coerce').fillna(0)
    df['time_taken_ms'] = pd.to_numeric(df['time_taken_ms'], errors='coerce').fillna(0)
    return df


# ---------------------------------------------------------
# 🔹 1. WORKED VS FAILED (Horizontal Labels, Vertical Y)
# ---------------------------------------------------------
def plot_worked_vs_failed(df):
    plt.figure(figsize=(7, 5))
    status_counts = df['worked'].apply(lambda x: 'Yes' if str(x).lower() == 'yes' else 'No').value_counts()
    status_counts.plot(kind='bar', color=['#2ecc71', '#e74c3c'])

    plt.title("System Success: Worked vs Failed")
    plt.xlabel("Execution Status", labelpad=10)  # Horizontal X label
    plt.ylabel("Number of Queries", rotation=90, labelpad=10)  # Vertical Y label
    plt.xticks(rotation=0)  # Ensure X-axis text is horizontal
    plt.tight_layout()
    plt.savefig(f"{IMAGE_DIR}/worked_vs_failed_bar.png")
    plt.close()


# ---------------------------------------------------------
# 🔹 2. STACKED BARS (Accuracy & Retrieval by Complexity)
# ---------------------------------------------------------
def plot_stacked_metrics(df):
    order = ['Low', 'Medium', 'High']

    # --- Answer Accuracy Stacked Bar ---
    pivot_ans = df.groupby(['complexity', 'answer_accuracy']).size().unstack(fill_value=0)
    pivot_ans = pivot_ans.reindex([o for o in order if o in pivot_ans.index])

    pivot_ans.plot(kind='bar', stacked=True, figsize=(9, 6), colormap='viridis')
    plt.title("Answer Accuracy by Complexity")
    plt.xlabel("Complexity Level", rotation=0)
    plt.ylabel("Number of Questions", rotation=90)
    plt.xticks(rotation=0)
    plt.legend(title="Accuracy", bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.savefig(f"{IMAGE_DIR}/accuracy_stacked_complexity.png")
    plt.close()

    # --- Chunk Retrieval Accuracy Stacked Bar ---
    pivot_chunk = df.groupby(['complexity', 'chunk_accuracy']).size().unstack(fill_value=0)
    pivot_chunk = pivot_chunk.reindex([o for o in order if o in pivot_chunk.index])

    pivot_chunk.plot(kind='bar', stacked=True, figsize=(9, 6), colormap='plasma')
    plt.title("Chunk Retrieval Accuracy by Complexity")
    plt.xlabel("Complexity Level", rotation=0)
    plt.ylabel("Number of Questions", rotation=90)
    plt.xticks(rotation=0)
    plt.legend(title="Retrieval Quality", bbox_to_anchor=(1.05, 1), loc='upper left')
    plt.tight_layout()
    plt.savefig(f"{IMAGE_DIR}/retrieval_stacked_complexity.png")
    plt.close()


# ---------------------------------------------------------
# 🔹 3. SEPARATED HISTOGRAMS
# ---------------------------------------------------------
def plot_histograms_separated(df):
    # Answer Fact Score Histogram
    plt.figure(figsize=(8, 5))
    plt.hist(df['answer_fact_score'], bins=10, color='skyblue', edgecolor='black')
    plt.title("Distribution of Answer Fact Scores")
    plt.xlabel("Score (0.0 - 1.0)", rotation=0)
    plt.ylabel("Frequency (Count)", rotation=90)
    plt.tight_layout()
    plt.savefig(f"{IMAGE_DIR}/hist_answer_fact_score.png")
    plt.close()

    # Chunk Retrieval Score Histogram
    plt.figure(figsize=(8, 5))
    plt.hist(df['chunk_score'], bins=10, color='salmon', edgecolor='black')
    plt.title("Distribution of Chunk Retrieval Scores")
    plt.xlabel("Score (0.0 - 1.0)", rotation=0)
    plt.ylabel("Frequency (Count)", rotation=90)
    plt.tight_layout()
    plt.savefig(f"{IMAGE_DIR}/hist_chunk_retrieval_score.png")
    plt.close()


# ---------------------------------------------------------
# 🔹 4. PIE CHART & CATEGORY LATENCY
# ---------------------------------------------------------
def plot_additional_charts(df):
    # Retrieval Pie
    plt.figure(figsize=(7, 7))
    counts = df['chunk_accuracy'].value_counts()
    plt.pie(counts, labels=counts.index, autopct='%1.1f%%', startangle=140)
    plt.title("Overall Retrieval Quality")
    plt.savefig(f"{IMAGE_DIR}/retrieval_pie.png")
    plt.close()

    # Category Time (Horizontal Bar)
    plt.figure(figsize=(10, 6))
    df.groupby('category')['time_taken_ms'].mean().sort_values().plot(kind='barh', color='teal')
    plt.title("Average Latency per Category")
    plt.xlabel("Time (ms)", rotation=0)
    plt.ylabel("Category", rotation=90)
    plt.tight_layout()
    plt.savefig(f"{IMAGE_DIR}/category_latency.png")
    plt.close()


# -----------------------------
# 🔹 MAIN EXECUTION
# -----------------------------
def main():
    FILE_PATH = "/home/sowmya/Documents/main-project/secure_carebot_v1/evaluation/retrieval_eval_results.json"
    df = load_and_prepare_data(FILE_PATH)

    if df is not None:
        print("📊 Generating charts with standard orientations...")
        plot_worked_vs_failed(df)
        plot_stacked_metrics(df)
        plot_histograms_separated(df)
        plot_additional_charts(df)
        print(f"✅ All {len(os.listdir(IMAGE_DIR))} charts successfully saved to {IMAGE_DIR}")
    else:
        print("❌ Error: Could not load data from path.")


if __name__ == "__main__":
    main()