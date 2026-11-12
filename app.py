"""
Ticketify AI Chatbot

A Flask app that answers questions about the Ticketify Django project using
Google Gemini. The answer is grounded in Ticketify's own README and source
files, which are placed in the prompt together with strict accuracy rules.
"""
import logging
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, render_template_string, request
from google import genai
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("ticketify-chatbot")

app = Flask(__name__)
if os.getenv("TRUST_PROXY", "").lower() in ("1", "true", "yes"):
    # Behind a hosting proxy (e.g. Render), use X-Forwarded-For for the client IP.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

BASE_DIR = Path(__file__).resolve().parent

# Files from the Ticketify project that are given to the model as context,
# as paths relative to the Ticketify Django project folder.
CONTEXT_FILES = {
    "readme": "README.md",
    "qr_service": "events/services/qr_service.py",
    "models": "events/models.py",
    "views": "events/views.py",
    "api_views": "events/api_views.py",
    "urls": "events/urls.py",
    "qr_template": "templates/events/qr_scan_result.html",
}


def find_project_root():
    """Where to read Ticketify's files from, in order of preference:

    1. TICKETIFY_PROJECT_PATH environment variable
    2. A sibling ../Ticketify folder (the original local development layout)
    3. The snapshot bundled in this repository's context/ folder
    """
    configured = os.getenv("TICKETIFY_PROJECT_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    sibling = BASE_DIR.parent / "Ticketify"
    if (sibling / "events" / "models.py").exists():
        return sibling
    return BASE_DIR / "context"


def load_context(root):
    context = {}
    for key, relative_path in CONTEXT_FILES.items():
        path = root / relative_path
        if path.exists():
            context[key] = path.read_text(encoding="utf-8-sig", errors="replace")
        else:
            context[key] = f"{Path(relative_path).name} was not found."
        logger.info("Context %-10s %s (%s)", key, "found" if path.exists() else "MISSING", path)
    return context


PROJECT_ROOT = find_project_root()
CONTEXT = load_context(PROJECT_ROOT)
README_FOUND = not CONTEXT["readme"].endswith("was not found.")

# Connect to Gemini
api_key = os.getenv("GEMINI_API_KEY")
client = genai.Client(api_key=api_key) if api_key else None
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")

MAX_QUESTION_LENGTH = int(os.getenv("MAX_QUESTION_LENGTH", "500"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))
_requests_by_ip = defaultdict(deque)
_rate_lock = threading.Lock()


def is_rate_limited(ip):
    """Simple in-memory limit: at most RATE_LIMIT_PER_MINUTE questions per IP per minute."""
    now = time.monotonic()
    with _rate_lock:
        recent = _requests_by_ip[ip]
        while recent and now - recent[0] > 60:
            recent.popleft()
        if len(recent) >= RATE_LIMIT_PER_MINUTE:
            return True
        recent.append(now)
        return False


def build_prompt(question):
    return f"""
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
{CONTEXT["readme"]}

PROJECT QR SERVICE.PY:
{CONTEXT["qr_service"]}

PROJECT MODELS.PY:
{CONTEXT["models"]}

PROJECT VIEWS.PY:
{CONTEXT["views"]}

PROJECT API VIEWS.PY:
{CONTEXT["api_views"]}

PROJECT URLS.PY:
{CONTEXT["urls"]}

PROJECT QR SCAN RESULT TEMPLATE:
{CONTEXT["qr_template"]}

USER QUESTION:
{question}
"""


# Webpage design
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Ticketify AI Chatbot</title>
    <style>
        :root { --brand: #6d5dfc; --brand2: #8b5cf6; --bg: #f6f7fb; --text: #1f2330; --muted: #646b7d; }
        * { box-sizing: border-box; }
        body { margin: 0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }
        header { background: linear-gradient(135deg, var(--brand), var(--brand2)); color: #fff; padding: 28px 16px; text-align: center; }
        header h1 { margin: 0 0 6px; font-size: 1.8rem; }
        header p { margin: 0; opacity: .9; }
        main { max-width: 820px; margin: 24px auto; padding: 0 16px; }
        .card { background: #fff; border-radius: 14px; box-shadow: 0 4px 18px rgba(30,30,60,.08); padding: 20px; margin-bottom: 18px; }
        form { display: flex; gap: 10px; }
        input[type=text] { flex: 1; padding: 12px 14px; border: 1px solid #d6d9e3; border-radius: 10px; font-size: 1rem; }
        button { padding: 12px 20px; border: 0; border-radius: 10px; background: var(--brand); color: #fff; font-size: 1rem; cursor: pointer; }
        button:hover { background: var(--brand2); }
        .examples { margin-top: 12px; color: var(--muted); font-size: .9rem; }
        .label { font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; color: var(--muted); margin-bottom: 6px; }
        .answer { white-space: pre-wrap; line-height: 1.55; }
        footer { text-align: center; color: var(--muted); font-size: .85rem; padding: 10px 16px 30px; }
        footer a { color: var(--brand); }
    </style>
</head>
<body>
    <header>
        <h1>🎟️ Ticketify AI Chatbot</h1>
        <p>Ask me anything about the Ticketify project!</p>
    </header>
    <main>
        <div class="card">
            <form method="POST">
                <input type="text" name="question" placeholder="Ask a question..." maxlength="{{ max_length }}" required autofocus>
                <button type="submit">Send</button>
            </form>
            <div class="examples">Try: “How does Ticketify stop a QR ticket from being used twice?” · “Which models store bookings?”</div>
        </div>

        {% if question %}
        <div class="card">
            <div class="label">Your question</div>
            <div>{{ question }}</div>
        </div>
        <div class="card">
            <div class="label">AI answer</div>
            <div class="answer">{{ answer }}</div>
        </div>
        {% endif %}
    </main>
    <footer>
        Answers are generated by Google Gemini from Ticketify's README and source files and may contain mistakes.<br>
        <a href="https://github.com/Aarthi23115/Ticketify-">Ticketify repository</a> ·
        <a href="https://github.com/Aarthi23115/Ticketify-AI-Chatbot">Chatbot repository</a>
    </footer>
</body>
</html>
"""


@app.route("/", methods=["GET", "POST"])
def home():
    question = ""
    answer = ""

    if request.method == "POST":
        question = request.form.get("question", "").strip()[:MAX_QUESTION_LENGTH]

        if not client:
            answer = "Gemini API key is missing. Check your .env file."

        elif not README_FOUND:
            answer = "README.md was not found. Please check the project folder."

        elif question and is_rate_limited(request.remote_addr or "unknown"):
            answer = "Too many questions in a short time. Please wait a minute and try again."

        elif question:
            try:
                response = client.models.generate_content(
                    model=MODEL_NAME,
                    contents=build_prompt(question),
                )
                answer = response.text or "No answer was returned."
            except Exception:
                logger.exception("Gemini request failed")
                answer = "Sorry, something went wrong. Please try again."

    return render_template_string(
        HTML,
        question=question,
        answer=answer,
        max_length=MAX_QUESTION_LENGTH,
    )


@app.route("/healthz")
def healthz():
    return {"status": "ok", "context_root": PROJECT_ROOT.name, "readme_found": README_FOUND}


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes"))
