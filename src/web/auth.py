"""
Token auth dependency for Control Room external API endpoints.

Usage:
    from src.web.auth import require_export_token
    ...
    async def my_endpoint(..., _auth: None = Depends(require_export_token)):

Server reads the expected token from env var CONTROLROOM_EXPORT_TOKEN.
Clients send it as the X-ControlRoom-Token request header.
"""

import os

from fastapi import Header, HTTPException


async def require_export_token(
    x_controlroom_token: str = Header(..., alias="X-ControlRoom-Token"),
) -> None:
    expected = os.environ.get("CONTROLROOM_EXPORT_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Export token not configured on server")
    if x_controlroom_token != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing token")
