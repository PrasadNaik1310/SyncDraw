
import json
import sys
from pathlib import Path

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150


def split_text(text: str) -> list[str]:
    """Split text into overlapping chunks."""
    chunks = []
    start = 0

    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunk = text[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end == len(text):
            break

        start = end - CHUNK_OVERLAP

    return chunks


def create_chunks(
    input_path: str | Path,
    output_path: str | Path,
) -> None:
    """Create chunks from extracted page records."""
    input_file = Path(input_path)
    output_file = Path(output_path)

    if not input_file.is_file():
        raise FileNotFoundError(
            f"Input file not found: {input_file}"
        )

    output_file.parent.mkdir(
        parents=True, exist_ok=True
    )

    total_chunks = 0
    total_pages = 0

    with input_file.open(encoding="utf-8") as source, \
         output_file.open("w", encoding="utf-8") as output:

        for line in source:
            page = json.loads(line)
            total_pages += 1

            for index, text in enumerate(
                split_text(page["text"])
            ):
                record = {
                    "source": page["source"],
                    "page": page["page"],
                    "chunk_index": index,
                    "text": text,
                }

                output.write(
                    json.dumps(record, ensure_ascii=False)
                    + "\n"
                )
                total_chunks += 1

    print(f"Pages processed: {total_pages}")
    print(f"Chunks created: {total_chunks}")
    print(f"Output: {output_file.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python -m rag.chunk "
            "path/to/input.jsonl path/to/output.jsonl"
        )
        sys.exit(1)

    create_chunks(sys.argv[1], sys.argv[2])
