# SkillFair Skills Discovery API

Python 3.11+ is recommended. From this directory, create and activate a virtual environment, install `requirements.txt`, and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API listens at `http://127.0.0.1:8000`; health check: `GET /api/health`. The Vite development server proxies `/api` to this service. Upload a PDF or DOCX (up to 10 MB) from the Skills Discovery page. Documents are parsed in memory and are not saved by the application.

Use the existing `.env` in this directory if one is present; never replace it when updating configuration. If no `.env` exists, copy `.env.example` to create one. Start Uvicorn with `--env-file .env`; alternatively export the values in the shell. By default `SKILLFAIR_LLM_PROVIDER=none`: extraction works, and the API returns no skills while explicitly reporting development mode.

To opt into Gemini analysis, set `SKILLFAIR_LLM_PROVIDER=gemini`, `SKILLFAIR_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta`, `SKILLFAIR_LLM_API_KEY` to your private Google AI Studio key, and `SKILLFAIR_LLM_MODEL=gemini-3.8-flash` (or another supported model with structured-output support). The backend sends the key only in Google's `x-goog-api-key` header and calls the Gemini `generateContent` endpoint. It prompts the model to treat resume contents as untrusted and return schema-constrained JSON, then validates each skill against the Pydantic schema and a verbatim resume excerpt. Do not commit `.env` or real candidate documents.

For this project, add or update these lines in `backend/.env` (replace only the API key placeholder locally; do not share it in chat):

```dotenv
SKILLFAIR_LLM_PROVIDER=gemini
SKILLFAIR_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta
SKILLFAIR_LLM_API_KEY=PASTE_YOUR_GOOGLE_AI_STUDIO_KEY_HERE
SKILLFAIR_LLM_MODEL=gemini-3.8-flash
```

Run tests with `python -m pytest -q` after installing requirements.
