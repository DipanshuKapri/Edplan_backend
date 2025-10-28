
# EdPlan College Guide – Python FastAPI Backend

A production‑ready FastAPI backend that powers your college guide frontend using the **U.S. Dept. of Education College Scorecard API**.

## ✨ What you get
- Clean, async FastAPI service
- Search, details, programs, and compare endpoints
- Built‑in pagination, sorting passthrough, and field selection
- CORS enabled for your frontend
- Env‑driven config + `.env.example`
- Simple in‑memory TTL cache for performance
- Dockerfile + `requirements.txt`
- Ready to deploy on any VM/container or run locally

> Data source: College Scorecard API (requires an API key). Apply for a key and see docs. 

## 🚀 Quickstart

```bash
# 1) Clone your frontend (already done in your repo)
# 2) Create a backend virtualenv
python -m venv .venv && source .venv/bin/activate  # (Windows: .venv\Scripts\activate)

# 3) Install deps
pip install -r requirements.txt

# 4) Configure environment
cp .env.example .env
# Edit .env and set COLLEGE_SCORECARD_API_KEY

# 5) Run
uvicorn main:app --reload --port 8000
```

Open: `http://127.0.0.1:8000/docs`

## 🔌 Environment

Copy `.env.example` and configure (or set system envs):

```
COLLEGE_SCORECARD_API_KEY=YOUR_KEY
ALLOWED_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
ALLOWED_HOSTS=localhost,127.0.0.1
SCORECARD_BASE_URL=https://api.data.gov/ed/collegescorecard/v1
CACHE_TTL_SECONDS=900
READY_CHECK_CACHE_SECONDS=60
LOG_LEVEL=INFO
CORS_ALLOW_CREDENTIALS=false
PUBLIC_CACHE_SECONDS=60
PROGRAMS_PUBLIC_CACHE_SECONDS=300
```

- Set `ALLOWED_ORIGINS` to the URLs serving your frontend. For production include the deployed hostname(s).
- Set `ALLOWED_HOSTS` to the hostnames your reverse proxy routes (include the platform’s internal host if required).
- Only enable `CORS_ALLOW_CREDENTIALS` when you have an explicit list of origins; browsers block `*` with credentials.
- `READY_CHECK_CACHE_SECONDS` controls how often the backend probes the Scorecard API for readiness checks.
- `CACHE_TTL_SECONDS=0` disables in-memory response caching.
 - `PUBLIC_CACHE_SECONDS` sets `Cache-Control: public, max-age=...` on GET responses and enables 304 with ETags.
 - `PROGRAMS_PUBLIC_CACHE_SECONDS` overrides the default public cache just for the programs endpoint.

## 🔗 Example calls

```http
# Search schools (name fuzzy, state, per_page up to 100)
GET /api/v1/search?q=harvard&state=MA&per_page=25&page=0

# Sort (Scorecard API syntax — e.g., by size desc)
GET /api/v1/search?q=ohio&sort=latest.student.size:desc

# Fields (comma‑separated Scorecard field names)
GET /api/v1/search?q=ohio&fields=id,school.name,school.city,latest.student.size

# Get details
GET /api/v1/schools/129020

# Get programs (CIP‑4 summaries; filter by CIP code prefix)
GET /api/v1/schools/129020/programs?cip_prefix=11

# Programs efficiency controls (optional)
# - min_share: only include programs with share >= value (0..1)
# - top_n: return only the top N programs by share (1..50)
GET /api/v1/schools/129020/programs?min_share=0.05&top_n=12

# Compare multiple schools
GET /api/v1/compare?ids=129020,153658,110635
```

## 🧠 Notes (Scorecard API)

- Use `latest.*` for most metrics (admissions, cost, earnings, etc.).
- `per_page` max is **100**; paginate with `page` (0‑indexed here).
- Fuzzy name match uses a regex filter: `school.name=~.*<q>.*`
- Many program variables are under `latest.programs.cip_4_digit.*` (code, title, credential level, share).

## 🧪 Testing (optional)

```bash
pytest -q
```

## 🐳 Docker

```bash
docker build -t edplan-backend .
docker run -it --rm -p 8000:8000 --env-file .env edplan-backend
```

- Use `WEB_CONCURRENCY` to control worker count in the container (defaults to 2).
- The image exposes a `/ready` endpoint and includes a container `HEALTHCHECK`.

## 🔄 Frontend Integration

The React frontend lives at [avitmr2345/EdPlan_Project/frontend](https://github.com/avitmr2345/EdPlan_Project/tree/main/frontend).

1. Start this backend (locally via `uvicorn` or `docker run`) with `ALLOWED_ORIGINS` containing the frontend URL.
2. In the frontend, set `VITE_API_BASE_URL` (or the equivalent config) to point at the backend origin, e.g. `http://localhost:8000`.
3. When deploying, expose the backend behind HTTPS and update both `ALLOWED_ORIGINS` here and the frontend `.env` to use the production domain.

## 📁 Project layout

```
main.py
scorecard_client.py
utils.py
requirements.txt
Dockerfile
.env.example
README.md
```

## 📜 License

MIT (yours). This scaffolding is provided as-is.
