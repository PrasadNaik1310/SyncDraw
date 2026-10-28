
import json
import sys
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer, util

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DEFAULT_TOP_K = 3


def load_embedded_chunks(input_path: str | Path) -> list[dict]:
    input_file = Path(input_path)

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Embedded chunks not found: {input_file}"
        )

    records = []

    with input_file.open(encoding="utf-8") as file:
        for line in file:
            if line.strip():
                records.append(json.loads(line))

    if not records:
        raise ValueError("No embedded chunks found.")

    return records


def retrieve(
    question: str,
    embedded_path: str | Path,
    top_k: int = DEFAULT_TOP_K,
) -> list[dict]:
    if not question.strip():
        raise ValueError("Question cannot be empty.")

    if top_k < 1:
        raise ValueError("top_k must be at least 1.")

    records = load_embedded_chunks(embedded_path)

    model = SentenceTransformer(MODEL_NAME)

    question_vector = model.encode(
        question,
        convert_to_tensor=True,
    )

    chunk_vectors = torch.tensor(
    [record["embedding"] for record in records],
    dtype=question_vector.dtype,
    device=question_vector.device,
     )

    chunk_vectors = util.normalize_embeddings(chunk_vectors)

    question_vector = util.normalize_embeddings(
        question_vector.unsqueeze(0)
    )

    scores = util.cos_sim(
        question_vector,
        chunk_vectors,
    )[0]

    best_indices = scores.topk(
        k=min(top_k, len(records))
    ).indices.tolist()

    results = []

    for index in best_indices:
        result = records[index].copy()
        result["similarity"] = float(scores[index])
        results.append(result)

    return results


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            'Usage: python -m rag.retrieve '
            '"path/to/embedded.jsonl" "your question"'
        )
        sys.exit(1)

    embedded_path = sys.argv[1]
    question = sys.argv[2]

    for result in retrieve(question, embedded_path):
        print(f"\nSimilarity: {result['similarity']:.4f}")
        print(f"Source: {result['source']}")
        print(f"Page: {result['page']}")
        print(f"Text:\n{result['text']}")
