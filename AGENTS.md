# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: FastAPI app and AWS integration logic.
  - `app/main.py`: API entrypoint and router wiring.
  - `app/routers/`: service endpoints (`ec2.py`, `eks.py`, `iam.py`, etc.).
  - `app/core/`: shared config, AWS session helpers, STS policy mapping, DB/session utilities.
  - `app/db/`: SQLAlchemy models.
- `frontend/`: React app (JavaScript/JSX).
  - `src/pages/`: top-level routes/screens.
  - `src/components/`: service panels and shared UI blocks.
  - `src/api/client.js`: all HTTP calls.
- `docs/`: planning/design notes.
- Root: `docker-compose.yml`, `.env.example`, `README.md`.

## Build, Test, and Development Commands
- Full stack (recommended):
  - `docker compose up -d --build` — builds and starts frontend, backend, Postgres, Valkey.
- Backend local:
  - `cd backend && pip install -r requirements.txt`
  - `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
- Frontend local:
  - `cd frontend && npm install`
  - `npm start` — dev server on `:3000`.
  - `npm run build` — production build.
- Lightweight Python syntax check:
  - `python3 -m py_compile backend/app/**/*.py` (or targeted files).

## Coding Style & Naming Conventions
- Python: PEP 8, 4-space indentation, `snake_case` for functions/variables, explicit error handling.
- React/JSX: 2-space indentation, `camelCase` for variables/functions, `PascalCase` for components.
- Keep service keys consistent across backend STS policy keys and frontend tab/API keys (example: `databases`, `iam`).
- Prefer small, focused functions and keep router responses shape-stable for frontend consumers.

## Testing Guidelines
- There is currently no formal automated test suite in this repository.
- For changes, validate by:
  1. Running affected API endpoints.
  2. Exercising related frontend flow manually.
  3. Running `npm run build` (frontend) and `py_compile` (backend) before PR.

## Commit & Pull Request Guidelines
- Follow conventional-style prefixes seen in history: `feat:`, `fix:`, `docs:`, `chore:`.
- Keep commits scoped to one logical change.
- PRs should include:
  - clear summary and impacted areas (`backend`, `frontend`, or both),
  - any env/config changes,
  - screenshots/GIFs for UI changes,
  - verification steps (commands + flows tested).

## Security & Configuration Tips
- Never commit `.env`, AWS secrets, or generated credentials.
- Use `.env.example` as the source of required variables.
- IAM/STS permissions must be updated in both infrastructure role policy and `backend/app/core/sts_service.py` when adding new service capabilities.
