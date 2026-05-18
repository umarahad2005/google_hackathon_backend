# Zimma AI — Backend (FastAPI)

Agentic AI Service Orchestrator for the Informal Economy — Challenge 2.

Multi-agent backend (Gemini ADK) exposing a FastAPI API consumed by the Zimma AI
Flutter app (separate repo).

## Layout (FastAPI Cloud pattern)

```
main.py           # Root entrypoint — exposes `app` (re-exports app.main:app)
requirements.txt  # Project dependencies
.venv/            # Virtual environment (optional, local only — git-ignored)
app/              # Application package
  main.py         # FastAPI app instance: `app = FastAPI(...)` + routes
  agents/         # Orchestrator + Intent/NLU, Discovery, Ranking, Booking, Follow-up, Trace
  services/       # Supabase + Google Maps integrations
tests/            # pytest suite
scripts/          # Helper scripts
.env              # Secrets (NOT committed — git-ignored)
```

`main.py` only does `from app.main import app`, so the `fastapi` CLI finds the
`app` instance at the project root while all internal `from app.xxx` imports
keep working unchanged.

## Local development

```bash
python -m venv .venv
.venv\Scripts\activate         # Windows  (use: source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt

fastapi dev main.py            # hot-reload dev server
```

To let the Flutter app on a device/emulator reach it, bind all interfaces:

```bash
fastapi dev main.py --host 0.0.0.0 --port 8000
```

## Deploy on FastAPI Cloud

```bash
fastapi deploy
```

- **Entrypoint:** `main.py` (the `app` object) — detected automatically
- **Python:** 3.11+
- **Dependencies:** `requirements.txt` (FastAPI Cloud installs these; `.venv/` is local-only and git-ignored)
- **Environment variables:** set the keys from `.env` in the FastAPI Cloud
  dashboard (Gemini / Google API key, Supabase URL + key, Google Maps key, etc.).

After deploy, point the Flutter app's API base URL at the deployed HTTPS URL
(`lib/core/network/dio_client.dart` in the app repo).

## Authentication & multi-tenancy

- The app sends the Supabase access token as `Authorization: Bearer <jwt>`.
  `app/auth.py` validates it via Supabase and resolves the user id.
- Identity precedence in `POST /api/requests`: authenticated user >
  `body.user_id` > demo user. `GET /api/requests/{id}` (+ trace) only return
  a record to its owner when the caller is authenticated (no existence leak);
  unauthenticated calls are still allowed during rollout.
- No new env vars: token validation reuses the existing Supabase client
  (service key). No JWT secret needed.

### Row-Level Security (apply manually)

`migrations/0001_enable_rls.sql` enables RLS + owner-scoped policies. The
backend uses the **service-role key (bypasses RLS)**, so applying this does
**not** break it — RLS is defense-in-depth for direct client access. Review
the column assumptions noted at the top of the file, then apply via the
Supabase Dashboard SQL Editor (or `psql -f`). A rollback block is included.
