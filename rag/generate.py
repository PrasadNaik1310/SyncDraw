
import os

from google import genai

MODEL_NAME = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
)


def generate_answer(question: str, chunks: list[dict]) -> str:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    if not question.strip():
        raise ValueError("Question cannot be empty.")

    if not chunks:
        raise ValueError("No context chunks were provided.")

    context_parts = []

    for chunk in chunks:
        context_parts.append(
            f"Source: {chunk['source']}, "
            f"Page: {chunk['page']}\n"
            f"{chunk['text']}"
        )

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""
You are a helpful teaching assistant for SyncDraw.

Answer the student's question using only the supplied document context.
If the context does not contain enough information, say so clearly.
Do not invent facts or claim the document says something it does not.
Cite relevant page numbers in your answer.

DOCUMENT CONTEXT:
{context}

STUDENT QUESTION:
{question}
"""

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )

    answer = response.text

    if not answer:
        raise RuntimeError("Gemini returned an empty response.")

    return answer.strip()

import os

from google import genai

MODEL_NAME = os.environ.get(
    "GEMINI_MODEL",
    "gemini-3.8-flash",
)

#import traceback
from pathlib import Path
from dotenv import load_dotenv
def generate_answer(question: str, chunks: list[dict]) -> str:
    '''
    try:
        answer = generate_answer(question,chunks)
    except Exception:
        print("Gemini APi key error see below__________")
        traceback.print_exc()
        raise
        '''
    PROJECT_ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(PROJECT_ROOT / ".env")
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured.")

    if not question.strip():
        raise ValueError("Question cannot be empty.")

    if not chunks:
        raise ValueError("No context chunks were provided.")

    context_parts = []

    for chunk in chunks:
        context_parts.append(
            f"Source: {chunk['source']}, "
            f"Page: {chunk['page']}\n"
            f"{chunk['text']}"
        )

    context = "\n\n---\n\n".join(context_parts)

    prompt = f"""
You are a helpful teaching assistant for SyncDraw.

Answer the student's question using only the supplied document context.
If the context does not contain enough information, say so clearly.
Do not invent facts or claim the document says something it does not.
Cite relevant page numbers in your answer.

DOCUMENT CONTEXT:
{context}

STUDENT QUESTION:
{question}
"""

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )

    answer = response.text

    if not answer:
        raise RuntimeError("Gemini returned an empty response.")

    return answer.strip()
