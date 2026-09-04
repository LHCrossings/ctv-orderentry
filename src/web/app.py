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
from web.routes.finish import build_finish_router
from web.routes.monitor_wall import build_monitor_wall_router
from web.routes.orders import build_router
from web.routes.programming import build_programming_router
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
    app.include_router(build_programming_router(templates))
    app.include_router(build_finish_router(templates))
    app.include_router(build_monitor_wall_router(templates))

    # Inject the global Broadcast Health indicator on every HTML page. Doing it
    # in one middleware avoids editing ~58 per-page headers (there is no shared
    # base template) and automatically covers future pages. Non-HTML responses
    # (JSON, static assets, SSE streams) are passed through untouched.
    _BH_TAG = b'<script src="/static/js/broadcast-health.js?v=20260904c"></script>'

    # Shared date/time entry helpers (formatDateInput / parseDateInput /
    # fmtAirtime), previously copy-pasted into a dozen templates. Injected into
    # <head> rather than before </body> so they are defined before any page's
    # own inline script runs, not just before its on* handlers fire.
    _DATE_TAG = b'<script src="/static/js/date-input.js?v=20260806"></script>'

    @app.middleware("http")
    async def inject_broadcast_health(request, call_next):
        response = await call_next(request)
        if not response.headers.get("content-type", "").startswith("text/html"):
            return response
        body = b""
        async for chunk in response.body_iterator:
            body += chunk

        # Inject before the LAST </body>, not the first. make_goods.html builds
        # its PDF export as a JS template literal that contains a whole
        # "</body></html>" — injecting at the first match put a literal
        # </script> inside that string, which ends the inline script block at
        # the HTML-parser level and killed every bit of JS on the page. The
        # real </body> is always the last one. (</head> is safe as a first
        # match: the document head precedes any body script content.)
        def _inject_last(html: bytes, needle: bytes, tag: bytes) -> bytes:
            i = html.rfind(needle)
            return html if i == -1 else html[:i] + tag + html[i:]

        if b"</head>" in body:
            body = body.replace(b"</head>", _DATE_TAG + b"</head>", 1)
        else:
            body = _inject_last(body, b"</body>", _DATE_TAG)
        body = _inject_last(body, b"</body>", _BH_TAG)
        headers = dict(response.headers)
        headers.pop("content-length", None)  # body length changed; let Response recompute
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )

    return app


app = create_app()
