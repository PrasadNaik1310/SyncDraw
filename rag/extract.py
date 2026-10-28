
import json
import sys
from pathlib import Path

import pymupdf


def extract_pdf(pdf_path: str, output_path: str) -> None:
    pdf_file = Path(pdf_path)
    output_file = Path(output_path)

    if not pdf_file.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_file}")

    # Create the output directory if it doesn't exist.
    output_file.parent.mkdir(parents=True, exist_ok=True)

    total_pages = 0
    low_text_pages = []

    with pymupdf.open(pdf_file) as document:
        total_pages = len(document)

        with output_file.open("w", encoding="utf-8") as output:
            for page_number, page in enumerate(document, start=1):
                text = page.get_text("text").strip()

                record = {
                    "source": pdf_file.name,
                    "page": page_number,
                    "text": text,
                }

                output.write(json.dumps(record, ensure_ascii=False) + "\n")

                if len(text) < 100:
                    low_text_pages.append(page_number)

    print(f"Source: {pdf_file.name}")
    print(f"Pages processed: {total_pages}")
    print(f"Pages with fewer than 100 characters: {len(low_text_pages)}")
    print(f"Low-text page numbers: {low_text_pages}")
    print(f"Output: {output_file.resolve()}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(
            "Usage: python -m rag.extract "
            "path/to/notes.pdf path/to/output.jsonl"
        )
        sys.exit(1)

    extract_pdf(sys.argv[1], sys.argv[2])
