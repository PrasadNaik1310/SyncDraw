from pathlib import Path
from dotenv import load_dotenv
from google import genai
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

response = client.models.generate_content(
    model="gemini-3.5-flash-lite",
    contents="Explain cohesion and coupling in two sentences.",
)

print(response.text)
