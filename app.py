
import os
from pathlib import Path

from flask import Flask, render_template_string, request
from dotenv import load_dotenv
from google import genai

load_dotenv()

app = Flask(__name__)


# Read the README.md from the main Ticketify folder
PROJECT_ROOT = Path(__file__).resolve().parent.parent
README_PATH = PROJECT_ROOT / "Ticketify" / "README.md"
MODELS_PATH = PROJECT_ROOT / "Ticketify" / "events" / "models.py"
QR_SERVICE_PATH = PROJECT_ROOT / "Ticketify" / "events" / "services" / "qr_service.py"
VIEWS_PATH = PROJECT_ROOT / "Ticketify" / "events" / "views.py"
URLS_PATH = PROJECT_ROOT / "Ticketify" / "events" / "urls.py"
QR_TEMPLATE_PATH = PROJECT_ROOT / "Ticketify" / "templates" / "events" / "qr_scan_result.html"
API_VIEWS_PATH = PROJECT_ROOT / "Ticketify" / "events" / "api_views.py"

if MODELS_PATH.exists():
    with open(MODELS_PATH, "r", encoding="utf-8") as file:
        models_context = file.read()
else:
    models_context = "models.py was not found."

print("MODELS path:", MODELS_PATH)
print("MODELS found:", MODELS_PATH.exists())

if QR_SERVICE_PATH.exists():
    with open(QR_SERVICE_PATH, "r", encoding="utf-8") as file:
        qr_service_context = file.read()
else:
    qr_service_context = "qr_service.py was not found."

if VIEWS_PATH.exists():
    with open(VIEWS_PATH, "r", encoding="utf-8") as file:
        views_context = file.read()
else:
    views_context = "views.py was not found."

if API_VIEWS_PATH.exists():
    with open(API_VIEWS_PATH, "r", encoding="utf-8") as file:
        api_views_context = file.read()
else:
    api_views_context = "api_views.py was not found."

print("API views path:", API_VIEWS_PATH)
print("API views found:", API_VIEWS_PATH.exists())

if URLS_PATH.exists():
    with open(URLS_PATH, "r", encoding="utf-8") as file:
        urls_context = file.read()
else:
    urls_context = "urls.py was not found."

if QR_TEMPLATE_PATH.exists():
    with open(QR_TEMPLATE_PATH, "r", encoding="utf-8") as file:
        qr_template_context = file.read()
else:
    qr_template_context = "qr_scan_result.html was not found."


print("QR template path:", QR_TEMPLATE_PATH)
print("QR template found:", QR_TEMPLATE_PATH.exists())

if README_PATH.exists():
    with open(README_PATH, "r", encoding="utf-8-sig", errors="replace") as file:
        project_context = file.read()
else:
    project_context = "README.md was not found."

# Debugging information
print("README path:", README_PATH)
print("README found:", README_PATH.exists())
print("README preview:", project_context[:500])


# Connect to Gemini
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None

# Webpage design
HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Ticketify AI Chatbot</title>
</head>
<body>
    <h1>Ticketify AI Chatbot</h1>
    <p>Ask me anything about the Ticketify project!</p>

    <form method="POST">
        <input
            type="text"
            name="question"
            placeholder="Ask a question..."
            required
        >
        <button type="submit">Send</button>
    </form>

    {% if question %}
        <p><strong>Your question:</strong> {{ question }}</p>
        <p><strong>AI Answer:</strong> {{ answer }}</p>
    {% endif %}
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    question = ""
    answer = ""

    if request.method == "POST":
        question = request.form.get("question", "").strip()

        if not client:
            answer = "Gemini API key is missing. Check your .env file."

        elif not project_context or project_context == "README.md was not found.":
            answer = "README.md was not found. Please check the project folder."

        elif question:
            try:
                print("README characters:", len(project_context))
                print("Models characters:", len(models_context))
                response = client.models.generate_content(
                    model="gemini-flash-lite-latest",
                    contents=f"""
You are an assistant for my Ticketify Django project.

Answer using only the project information below.
Do not invent technologies, features, or implementation details.
If the information is not present in any of the provided project files, say:
"This information is not documented in the provided project files."

SOURCE CODE ACCURACY RULES:

1. Use only the project files provided in the prompt.
2. Never invent code, filenames, functions, or behavior.
3. Distinguish between:
   - What the code explicitly implements.
   - What comments claim or intend.
   - What cannot be verified from the provided files.
4. Do not claim a security feature works just because
   a comment says it should work.
5. If code stores data in a cache, do not claim that
   the cache enforces replay protection unless the
   provided code shows how the entry is checked.
6. When quoting code, preserve the original logic.
   If the relevant code is missing, say so.
7. If multiple files are relevant, explain which file
   supports each claim.

PROJECT README:
{project_context}

PROJECT QR SERVICE.PY:
{qr_service_context}

PROJECT MODELS.PY:
{models_context}

PROJECT VIEWS.PY:
{views_context}

PROJECT API VIEWS.PY:
{api_views_context}

PROJECT URLS.PY:
{urls_context}

PROJECT QR SCAN RESULT TEMPLATE:
{qr_template_context}

USER QUESTION:
{question}
"""
                )



                answer = response.text or "No answer was returned."

            except Exception as error:
                print("Gemini error:", error)
                answer = "Sorry, something went wrong. Check the VS Code terminal."

    return render_template_string(
        HTML,
        question=question,
        answer=answer
    )


if __name__ == "__main__":
    app.run(debug=True)
