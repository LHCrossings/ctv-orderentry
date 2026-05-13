from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates


def build_airchecks_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/airchecks", response_class=HTMLResponse)
    async def airchecks(request: Request):
        return templates.TemplateResponse(request, "airchecks.html")

    return router
