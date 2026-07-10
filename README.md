# Agentic HRMS Foundation

Enterprise-grade foundation for a true multi-agent HRMS platform.

## Backend

Requires Python 3.12 and a running PostgreSQL instance. Copy `backend/.env.example` to `backend/.env` and adjust `DATABASE_URL` to point at a database that already exists (e.g. `createdb hrms`).

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python -m scripts.seed_auth
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Note: run `python -m scripts.seed_auth`, not `python scripts/seed_auth.py` — the latter fails with `ModuleNotFoundError: No module named 'app'` because the backend root isn't on `sys.path`.

Health: `http://127.0.0.1:8000/api/v1/health`

## Frontend

Copy `Frontend/.env.example` to `Frontend/.env` and set `VITE_API_BASE_URL` to match the backend's actual host/port (e.g. `http://127.0.0.1:8000/api/v1` if you followed the Backend steps above — the example file defaults to port 8001).

```powershell
cd Frontend
npm install
npm run dev -- --host 127.0.0.1
```

App: `http://127.0.0.1:5173`

## Docker

```powershell
docker compose -f infrastructure/docker-compose.yml up --build
```
