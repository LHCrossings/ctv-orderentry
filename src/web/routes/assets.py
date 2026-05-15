"""
Asset Browser — search, download, and delete files in S3.

Credentials read from credentials.env:
    AWS_ACCESS_KEY_ID     = ...
    AWS_SECRET_ACCESS_KEY = ...
    AWS_REGION            = us-west-2   (default)
    S3_BUCKET_NAME        = storageforct (default)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent


def _load_s3_config() -> dict[str, str]:
    cfg: dict[str, str] = {}
    for env_file in ("credentials.env", ".env"):
        p = _PROJECT_ROOT / env_file
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip()
    return cfg


_CFG = _load_s3_config()
_BUCKET = _CFG.get("S3_BUCKET_NAME", "storageforct")
_REGION = _CFG.get("AWS_REGION", "us-west-2")
_KEY_ID = _CFG.get("AWS_ACCESS_KEY_ID") or None
_SECRET = _CFG.get("AWS_SECRET_ACCESS_KEY") or None


def _client():
    import boto3
    return boto3.client(
        "s3",
        region_name=_REGION,
        aws_access_key_id=_KEY_ID,
        aws_secret_access_key=_SECRET,
    )


def _matches(key: str, q: str) -> bool:
    """Match a key against a query.

    Glob (*): matched against full key (case-insensitive). A trailing * is
    auto-appended so you never need to include the file extension.
      newstoday*23   → newstoday*23*  → matches Newstoday051123.mp4
      newstoday*23.mp4 → kept as-is  → also matches

    Multi-term AND (no *): all space-separated terms must be substrings.
      newstoday 23 → key must contain both "newstoday" and "23"
    """
    import fnmatch as _fnm
    q_lower = q.strip().lower()
    if '*' in q_lower:
        full    = key.lower()
        pattern = q_lower if q_lower.endswith('*') else q_lower + '*'
        return _fnm.fnmatch(full, pattern)
    return all(t in key.lower() for t in q_lower.split() if t)


def _fmt_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.0f} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"


def build_assets_router(templates: Jinja2Templates) -> APIRouter:
    router = APIRouter()

    @router.get("/assets", response_class=HTMLResponse)
    async def assets_page(request: Request):
        return templates.TemplateResponse(request, "assets.html")

    @router.get("/api/assets/list")
    async def list_assets(
        search: str = Query(default=""),
        continuation: str = Query(default=""),
    ):
        loop = asyncio.get_running_loop()

        def _list():
            s3 = _client()
            q = search.strip().lower()

            if q:
                # Search mode: page through all objects and filter
                files: list[dict] = []
                kwargs: dict = {"Bucket": _BUCKET}
                while True:
                    resp = s3.list_objects_v2(**kwargs)
                    for obj in resp.get("Contents", []):
                        if _matches(obj["Key"], q):
                            files.append({
                                "key": obj["Key"],
                                "size": _fmt_size(obj["Size"]),
                                "size_bytes": obj["Size"],
                                "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M"),
                            })
                    if not resp.get("IsTruncated"):
                        break
                    kwargs["ContinuationToken"] = resp["NextContinuationToken"]
                return {"files": files, "next_token": "", "search_mode": True}
            else:
                # Browse mode: one page at a time
                kwargs = {"Bucket": _BUCKET, "MaxKeys": 200}
                if continuation:
                    kwargs["ContinuationToken"] = continuation
                resp = s3.list_objects_v2(**kwargs)
                files = [
                    {
                        "key": obj["Key"],
                        "size": _fmt_size(obj["Size"]),
                        "size_bytes": obj["Size"],
                        "last_modified": obj["LastModified"].strftime("%Y-%m-%d %H:%M"),
                    }
                    for obj in resp.get("Contents", [])
                ]
                return {
                    "files": files,
                    "next_token": resp.get("NextContinuationToken", ""),
                    "search_mode": False,
                }

        try:
            result = await loop.run_in_executor(None, _list)
            return JSONResponse(result)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.get("/api/assets/download-url")
    async def download_url(key: str = Query(...)):
        loop = asyncio.get_running_loop()

        def _presign():
            s3 = _client()
            return s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": _BUCKET, "Key": key},
                ExpiresIn=300,
            )

        try:
            url = await loop.run_in_executor(None, _presign)
            return JSONResponse({"url": url})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.delete("/api/assets/file")
    async def delete_asset(key: str = Query(...)):
        loop = asyncio.get_running_loop()

        def _delete():
            _client().delete_object(Bucket=_BUCKET, Key=key)

        try:
            await loop.run_in_executor(None, _delete)
            return JSONResponse({"ok": True})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    @router.post("/api/assets/rename")
    async def rename_asset(key: str = Query(...), new_key: str = Query(...)):
        if not new_key or new_key == key:
            raise HTTPException(status_code=400, detail="New name must differ from current name.")
        loop = asyncio.get_running_loop()

        def _rename():
            s3 = _client()
            s3.copy_object(
                Bucket=_BUCKET,
                CopySource={"Bucket": _BUCKET, "Key": key},
                Key=new_key,
            )
            s3.delete_object(Bucket=_BUCKET, Key=key)

        try:
            await loop.run_in_executor(None, _rename)
            return JSONResponse({"ok": True, "new_key": new_key})
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))

    return router
