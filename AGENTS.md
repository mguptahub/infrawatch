# Repository Guidelines

## Project Structure & Module Organization
- `backend/`: FastAPI app and AWS integration logic.
  - `app/main.py`: API entrypoint and router wiring.
  - `app/routers/`: service endpoints (`ec2.py`, `eks.py`, `iam.py`, etc.).
  - `app/core/`: shared config, AWS session helpers, STS policy mapping, DB/session utilities.
  - `app/db/`: SQLAlchemy models.
  - Celery worker runs in the same image for background metric/resource collection.
- `frontend/`: React app (JavaScript/JSX).
  - `src/pages/`: top-level routes/screens.
  - `src/components/`: service panels and shared UI blocks.
  - `src/api/client.js`: all HTTP calls.
- `docs/`: GitHub Pages site (landing page + documentation).
  - `index.html`: product landing page.
  - `docs/`: documentation sub-site (5 pages: Overview, Docker, K8s, Configuration, IAM).
  - `assets/docs.css`: shared docs styles.
  - `assets/docs.js`: shared docs behaviour (navbar, sidebar, theme toggle, copy buttons). Sidebar nav and active link are injected by JS — add new pages by editing the `links` array in this file.
- `.plans/`: planning/design notes (not published).
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

## Development Workflow
- New features follow: brainstorm → design doc (`.plans/YYYY-MM-DD-<topic>-design.md`) → implementation plan (`.plans/YYYY-MM-DD-<topic>.md`) → implementation.
- Design docs capture the agreed approach and edge cases before any code is written.
- Implementation plans list exact files, code snippets, and commit commands per task.
- During implementation, review each task for spec compliance and code quality before moving on.

## Database Migrations
- There is no Alembic. Schema is created via `Base.metadata.create_all()` in `init_db()`.
- For changes to existing tables (column type changes, new columns), add a one-time `ALTER TABLE` migration inside `init_db()` in `backend/app/core/database.py` — before the `create_all` call.
- Wrap migration SQL in `try/except ProgrammingError: pass` so it is silently skipped on subsequent startups.
- Log unexpected exceptions (`except Exception as e: print(...)`) so real failures are visible in container logs.

## Push Policy
- **Never push to any branch without explicit confirmation from the user.** Always present the push as an option and wait for approval before running `git push`.

## Commit & Pull Request Guidelines
- Follow conventional-style prefixes seen in history: `feat:`, `fix:`, `docs:`, `chore:`.
- Squash a feature down to **one logical commit** before pushing — use `git reset --soft origin/main` then re-commit. Fix commits applied during testing come after as separate commits.
- PRs should include:
  - clear summary and impacted areas (`backend`, `frontend`, or both),
  - any env/config changes,
  - screenshots/GIFs for UI changes,
  - verification steps (commands + flows tested).

## License

- The project is licensed under **AGPLv3** (GNU Affero General Public License v3.0).
- All contributions must be compatible with AGPLv3.

## Docs Site (GitHub Pages)

- Deployed automatically via GitHub Actions workflow on push to `main`.
- Landing page: `docs/index.html` — fetches latest release version from GitHub API.
- Documentation pages: `docs/docs/*.html` — each page is a slim HTML shell; navbar, sidebar, and theme toggle are injected by `docs/assets/docs.js`.
- To add a new docs page: create the HTML file in `docs/docs/`, add an entry to the `links` array in `docs/assets/docs.js`.
- Tables must be wrapped in `<div class="table-wrap">` for mobile horizontal scrolling.

## Security & Configuration Tips

- Never commit `.env`, AWS secrets, or generated credentials.
- Use `.env.example` as the source of required variables.
- IAM/STS permissions must be updated in both infrastructure role policy and `backend/app/core/sts_service.py` when adding new service capabilities.
