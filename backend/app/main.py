import os

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .analysis import analyze_skills
from .matching import match_job_skills
from .models import JobMatchRequest, JobMatchResponse, SkillsDiscoverResponse
from .resume import validate_and_extract

app = FastAPI(title="SkillFair Skills Discovery API", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/skills/discover", response_model=SkillsDiscoverResponse)
def discover_skills(file: UploadFile = File(...)) -> SkillsDiscoverResponse:
    try:
        max_bytes = int(os.getenv("SKILLFAIR_MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))
        if not 1 <= max_bytes <= 50 * 1024 * 1024:
            raise ValueError("outside allowed bounds")
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="The upload limit is misconfigured.") from exc
    # Read one byte over the limit so a large multipart upload cannot consume unbounded memory.
    content = file.file.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail=f"The file exceeds the {max_bytes // (1024 * 1024)} MB upload limit.")
    text = validate_and_extract(file.filename, content, max_bytes)
    return analyze_skills(text)


@app.post("/api/jobs/match", response_model=JobMatchResponse)
def match_job(request: JobMatchRequest) -> JobMatchResponse:
    return match_job_skills(request)
