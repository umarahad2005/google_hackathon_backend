"""
Zimma AI — FastAPI Cloud entrypoint.

FastAPI Cloud's `fastapi dev main.py` / `fastapi deploy` looks for an ASGI
app named `app` in this root module. The real application lives in the
`app/` package (app/main.py + app/agents, app/services, ...), so we simply
re-export it here. This keeps the package structure and all internal
`from app.xxx import ...` imports working unchanged.

Run locally:   fastapi dev main.py
Deploy:        fastapi deploy
"""

from app.main import app

__all__ = ["app"]
