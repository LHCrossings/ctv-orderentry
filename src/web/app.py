"""
CTV Order Entry - FastAPI web application.
"""

import sys
from pathlib import Path

# Ensure src/ is on the path (mirrors how main.py runs)
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from fastapi import FastAPI, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from orchestration.config import ApplicationConfig
from web.routes.airchecks import build_airchecks_router
from web.routes.assets import build_assets_router
from web.routes.backwrite import build_backwrite_router
from web.routes.broadcast_health import build_broadcast_health_router
from web.routes.edi import build_edi_router
from web.routes.edi_billing import build_edi_billing_router
from web.routes.edi_export import build_edi_export_router
from web.routes.orders import build_router
from web.routes.reports import build_reports_router

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(config: ApplicationConfig | None = None) -> FastAPI:
    if config is None:
        config = ApplicationConfig.from_defaults()
    config.ensure_directories()

    app = FastAPI(title="CTV Order Entry", docs_url=None, redoc_url=None)

    static_dir = Path(__file__).parent / "static"
    templates_dir = Path(__file__).parent / "templates"

    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    templates = Jinja2Templates(directory=str(templates_dir))

    app.include_router(build_router(config, templates))
    app.include_router(build_backwrite_router(templates))
    app.include_router(build_reports_router(templates))
    app.include_router(build_edi_router(templates))
    app.include_router(build_edi_export_router(templates))
    app.include_router(build_edi_billing_router(templates))
    app.include_router(build_airchecks_router(templates))
    app.include_router(build_assets_router(templates))
    app.include_router(build_broadcast_health_router(templates))

    # Inject the global Broadcast Health indicator on every HTML page. Doing it
    # in one middleware avoids editing ~58 per-page headers (there is no shared
    # base template) and automatically covers future pages. Non-HTML responses
    # (JSON, static assets, SSE streams) are passed through untouched.
    _BH_TAG = b'<script src="/static/js/broadcast-health.js?v=20260721b"></script>'

    @app.middleware("http")
    async def inject_broadcast_health(request, call_next):
        response = await call_next(request)
        if not response.headers.get("content-type", "").startswith("text/html"):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk
        if b"</body>" in body:
            body = body.replace(b"</body>", _BH_TAG + b"</body>", 1)
        headers = dict(response.headers)
        headers.pop("content-length", None)  # body length changed; let Response recompute
        return Response(content=body, status_code=response.status_code,
                        headers=headers, media_type=response.media_type)

    return app


app = create_app()
