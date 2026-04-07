"""
CTV Order Entry - FastAPI web application.
"""

import sys
from pathlib import Path

# Ensure src/ is on the path (mirrors how main.py runs)
_src_path = Path(__file__).parent.parent
if str(_src_path) not in sys.path:
    sys.path.insert(0, str(_src_path))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from orchestration.config import ApplicationConfig
from web.routes.orders import build_router

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

    return app


app = create_app()
