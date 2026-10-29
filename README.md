# SkillFair — Skills-First Hiring Platform

**Hiring on What You Can Do, Not Where You've Been.**

SkillFair is a prototype of an inclusive, skills-first recruitment platform designed to help recruiters evaluate candidates using evidence of their skills rather than relying solely on traditional qualifications or resume keywords.

The platform currently includes resume-based Skills Discovery and evidence-led Job Matching. It emphasizes transparency, human oversight, and responsible interpretation of candidate information.

## 🎯 Problem Statement

Traditional recruitment often relies heavily on degrees, previous job titles, and resume keywords. This can make it difficult to recognize transferable skills and practical capabilities.

SkillFair explores an alternative approach: identify skills supported by resume evidence, compare them with role requirements, and help recruiters make more informed decisions.

## ✨ Key Features

### 1. Skills Discovery

* Upload a resume in PDF or DOCX format.
* Extract text from the uploaded document.
* Identify skills and associate them with supporting resume excerpts.
* Distinguish explicitly stated skills from inferred skills.
* Validate extracted evidence against the available resume text.
* Display supporting evidence so recruiters can review the results.

### 2. Evidence-Led Job Matching

* Enter a job title, description, and required skills.
* Compare job requirements against discovered candidate skills.
* Identify matched skills and display their supporting evidence.
* Clearly distinguish explicit evidence from inferred, unverified skills.
* List requirements that were not found in the discovered skills.
* Calculate evidence coverage based on the proportion of unique requirements with matching skill names.

**Important:** Evidence coverage indicates whether skill names match. It does not establish competence, proficiency, or candidate suitability.

### 3. Responsible Recruitment Principles

* Human review remains essential.
* Inferred skills are clearly identified as unverified.
* Missing resume evidence is not treated as proof that a candidate lacks a skill.
* The current matching process does not rank candidates.
* Resume information is handled separately from recruiter-entered role requirements.

## 🛠️ Technology Stack

| Component                    | Technology                                 |
| ---------------------------- | ------------------------------------------ |
| Frontend                     | React, TypeScript, Vite                    |
| Backend                      | Python, FastAPI                            |
| Resume Processing            | PDF and DOCX text extraction               |
| AI-Assisted Skills Discovery | Google Gemini API — optional configuration |
| Testing                      | Pytest                                     |
| Frontend Quality Checks      | ESLint, TypeScript, Vite production build  |

## 🏗️ Architecture

```text
Recruiter
    |
    v
React + TypeScript Frontend
    |
    | REST API requests
    v
FastAPI Backend
    |
    +---- Resume text extraction
    |
    +---- Skills Discovery
    |         |
    |         +---- Optional Gemini API
    |         |
    |         +---- Evidence validation
    |
    +---- Job Matching
              |
              +---- Normalized skill-name comparison
              |
              +---- Matched skills and evidence
              |
              +---- Missing requirements
```

The frontend communicates with the FastAPI backend through API endpoints. Skills Discovery can operate in development mode without an AI provider, while Gemini-assisted analysis requires a valid local API key and compatible model configuration.

The current Job Matching implementation uses deterministic, normalized exact skill-name matching rather than semantic AI matching.

## 📂 Project Structure

```text
SkillFair/
├── backend/
│   ├── app/
│   │   ├── analysis.py
│   │   ├── main.py
│   │   ├── matching.py
│   │   ├── models.py
│   │   └── resume.py
│   ├── tests/
│   │   ├── test_job_matching.py
│   │   └── test_skills_discovery.py
│   ├── .env.example
│   ├── README.md
│   └── requirements.txt
├── src/
│   ├── components/
│   │   ├── JobMatching.tsx
│   │   └── SkillsDiscovery.tsx
│   ├── data/
│   ├── styles/
│   ├── types/
│   ├── App.tsx
│   └── main.tsx
├── .gitignore
├── package.json
└── README.md
```

## 🚀 Getting Started

### Prerequisites

* Python 3.11 or newer
* Node.js and npm
* A modern web browser

### 1. Clone the Repository

```bash
git clone https://github.com/Sumanth14117/SkillFair.git
cd SkillFair
```

### 2. Set Up the Backend

Open a terminal in the project root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Start the backend:

```powershell
uvicorn app.main:app --reload --port 8000
```

The backend will be available at:

`http://127.0.0.1:8000`

Health check:

`http://127.0.0.1:8000/api/health`

### 3. Set Up the Frontend

Open a second terminal in the project root:

```powershell
cd C:\SkillFair
npm install
npm run dev
```

Open the local URL printed by Vite, typically:

`http://localhost:5173`

Keep both the frontend and backend running while testing the application.

## 🔑 Optional Gemini Configuration

Gemini-assisted Skills Discovery can be enabled using a Google AI Studio API key.

1. Open `backend/.env.example`.
2. Use it as a reference for the required environment variable names.
3. Create or update your private `backend/.env` file.
4. Configure the provider, base URL, API key, and supported model.
5. Restart the backend.

**Security precautions:**

* Never commit `backend/.env`.
* Never paste API keys into source code, GitHub issues, screenshots, or public repositories.
* If an API key is exposed, revoke it and create a replacement.
* Keep real candidate resumes and other personal information out of public repositories.

The application can also run without Gemini when configured for development-mode extraction.

## 🔌 API Endpoints

| Method | Endpoint               | Purpose                                                       |
| ------ | ---------------------- | ------------------------------------------------------------- |
| GET    | `/api/health`          | Check backend health                                          |
| POST   | `/api/skills/discover` | Extract and discover skills from an uploaded resume           |
| POST   | `/api/jobs/match`      | Compare discovered skills with recruiter-entered requirements |

Request and response details are defined by the backend implementation.

## 🧪 Testing

### Backend Tests

From the project root:

```powershell
py -m pytest -q backend\tests
```

### Frontend Lint

```powershell
npm run lint
```

### Production Build

```powershell
npm run build
```

These checks help validate backend behavior and frontend code quality.

## ⚖️ Responsible Use and Current Limitations

SkillFair is a prototype intended to demonstrate a skills-first recruitment workflow.

* Matching is based on normalized skill names, not semantic similarity.
* The job description is collected for recruiter context but is not currently used by the matching algorithm.
* Evidence coverage is not a measure of competence or hiring suitability.
* Inferred skills require verification.
* A missing skill in a resume does not prove that a candidate lacks it.
* Recruiters must independently review the supporting evidence.
* Database-backed persistence, external recruitment integrations, and SAP integration are not currently part of this workflow.
* The interface should not be interpreted as a validated hiring or fairness assessment system.

## 🛣️ Future Scope

Potential future improvements include:

* Semantic skill matching and transparent explanations.
* Candidate-controlled skill profiles and verified credentials.
* Learning pathways for skill gaps.
* Recruiter review workflows with audit trails.
* Fairness evaluation using appropriate, legally compliant methods.
* Secure persistence and authentication.
* Integration with recruitment platforms and enterprise systems.

These are proposed improvements and should not be considered implemented features.

## 👨‍💻 Author

**Sumanth N**

GitHub: [@Sumanth14117](https://github.com/Sumanth14117)

## 📄 License

No open-source license has been added yet. All rights are reserved unless a license is subsequently provided.
