
from rag.retrieve import retrieve
from rag.generate import generate_answer

question = "What is the difference between cohesion and coupling?"

chunks = retrieve(
    question,
    "data/embedded_chunks.jsonl",
)

answer = generate_answer(question, chunks)

print("\nQuestion:", question)
print("\nAnswer:\n", answer)
import traceback

try:
    answer = generate_answer(question,chunks)
    print("\nAnswer:\n", answer)
except Exception:
    traceback.print_exc()    