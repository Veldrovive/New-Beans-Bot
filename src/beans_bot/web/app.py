"""
create_app(ctx) builds the website: the API routers, the error handlers, the cross-site check and the
built frontend.

- Every non-GET request must carry `X-Beans-Admin: 1` or it gets a 403. A browser can't send that
  header cross-site without a CORS preflight, and CORS is off, so other sites can't use the admin's
  Cloudflare cookie to press buttons here.
- The frontend is served from `web.static_dir`. Any path outside /api that isn't a file gets
  index.html, so the browser's own routes survive a refresh.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from beans_bot.web import errors
from beans_bot.web.context import WebContext
from beans_bot.web.routers import ROUTERS

logger = logging.getLogger(__name__)

ADMIN_HEADER = "X-Beans-Admin"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def create_app(ctx: WebContext) -> FastAPI:
    app = FastAPI(title="Beans Bot admin", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.ctx = ctx
    app.state.retrain_tasks = set()  # Retrains started from the website, kept so they aren't garbage collected
    errors.install(app)

    @app.middleware("http")
    async def require_admin_header(request: Request, call_next):
        if request.method not in SAFE_METHODS and request.headers.get(ADMIN_HEADER) != "1":
            return JSONResponse({"detail": f"Missing the {ADMIN_HEADER} header."}, status_code=403)
        return await call_next(request)

    for router in ROUTERS:
        app.include_router(router)

    static_dir = ctx.config.web.static_dir
    if static_dir.is_dir():
        _serve_frontend(app, static_dir.resolve())
    else:
        logger.warning(f"No built frontend at {static_dir}; serving only the API. Run `npm run build` in frontend/.")
    return app


def _serve_frontend(app: FastAPI, root: Path) -> None:
    index = root / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> Response:
        if path == "api" or path.startswith("api/"):
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        file = (root / path).resolve()
        if path and file.is_file() and file.is_relative_to(root):
            # Vite puts a content hash in every asset's name, so they never change.
            cache = "public, max-age=31536000, immutable" if path.startswith("assets/") else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
