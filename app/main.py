from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastmcp.utilities.lifespan import combine_lifespans
from pydantic import ValidationError

from app.api import router as api_router
from app.auth import SWAGGER_CLIENT_ID, register_swagger_client, register_web_client
from app.mcp import error_message, mcp
from app.services import Forbidden, Invalid, NotFound
from app.web import router as web_router
from app.web_app import WebAuthRequired, render, router as web_app_router

# Serves /mcp plus the OAuth endpoints (/authorize, /token, /register, /auth/callback, /.well-known/*).
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)
STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await register_swagger_client()
    await register_web_client()
    yield


app = FastAPI(
    title="Quiz API",
    lifespan=combine_lifespans(mcp_app.lifespan, lifespan),
    swagger_ui_init_oauth={"clientId": SWAGGER_CLIENT_ID, "usePkceWithAuthorizationCodeGrant": True, "scopes": "read:user"},
)
app.include_router(api_router)
app.include_router(web_router)
app.include_router(web_app_router)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def _app_request(request: Request) -> bool:
    return request.url.path.startswith("/app")


@app.get("/up", include_in_schema=False)
def up() -> dict[str, str]:
    return {"status": "ok"}


@app.exception_handler(WebAuthRequired)
def web_auth_required(request: Request, _: WebAuthRequired) -> RedirectResponse:
    nxt = quote(request.url.path, safe="/")
    return RedirectResponse(f"/app/login?next={nxt}", status_code=303)


@app.exception_handler(NotFound)
def not_found(request: Request, exc: NotFound) -> HTMLResponse | JSONResponse:
    if _app_request(request):
        return render("app/error.html", request, status_code=404, title="Not found", message=str(exc))
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(Invalid)
def invalid(request: Request, exc: Invalid) -> HTMLResponse | JSONResponse:
    if _app_request(request):
        return render("app/error.html", request, status_code=422, title="Invalid request", message=exc.message)
    return JSONResponse({"detail": [{"loc": [exc.field], "msg": exc.message}]}, status_code=422)


@app.exception_handler(ValidationError)
def validation_error(request: Request, exc: ValidationError) -> HTMLResponse | JSONResponse:
    if _app_request(request):
        return render("app/error.html", request, status_code=422, title="Invalid request", message=error_message(exc))
    return JSONResponse({"detail": error_message(exc)}, status_code=422)


@app.exception_handler(Forbidden)
def forbidden(request: Request, exc: Forbidden) -> HTMLResponse | JSONResponse:
    if _app_request(request):
        return render("app/error.html", request, status_code=403, title="Forbidden", message=str(exc))
    return JSONResponse({"detail": str(exc)}, status_code=403)


app.mount("/", mcp_app)  # last, so the REST routes above win
