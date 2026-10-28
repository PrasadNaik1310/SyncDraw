
import json
import sys
from pathlib import Path

from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
BATCH_SIZE = 16


def embed_chunks(
    input_path: str | Path,
    output_path: str | Path,
) -> None:
    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Chunk file not found: {input_file}"
        )

    output_file.parent.mkdir(
        parents=True, exist_ok=True
    )

    model = SentenceTransformer(MODEL_NAME)
    total_chunks = 0

    with input_file.open(encoding="utf-8") as source, \
         output_file.open("w", encoding="utf-8") as output:

        batch = []

        def write_batch(records):
            nonlocal total_chunks

            if not records:
                return

            texts = [record["text"] for record in records]
            vectors = model.encode(
                texts,
                batch_size=BATCH_SIZE,
                show_progress_bar=False,
                convert_to_numpy=True,
            )

            for record, vector in zip(records, vectors):
                embedded_record = {
                    **record,
                    "embedding": vector.tolist(),
                }

                output.write(
                    json.dumps(
                        embedded_record,
                        ensure_ascii=False,
                    ) + "\n"
                )

            total_chunks += len(records)
            print(f"Embedded chunks: {total_chunks}", end="\r")

        for line in source:
            if not line.strip():
                continue

            batch.append(json.loads(line))

            if len(batch) >= BATCH_SIZE:
                write_batch(batch)
                batch = []

        write_batch(batch)

    print(f"\nTotal chunks embedded: {total_chunks}")
    print(f"Output: {output_file.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python -m rag.embed "
            "path/to/chunks.jsonl path/to/embedded.jsonl"
        )
        sys.exit(1)

    embed_chunks(sys.argv[1], sys.argv[2])
