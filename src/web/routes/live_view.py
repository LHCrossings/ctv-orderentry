from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


def build_live_view_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/live-view", response_class=HTMLResponse)
    async def live_view(request: Request):
        return templates.TemplateResponse(request, "live_view.html")

    return router
