import html
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans
from pydantic import ValidationError

from app import config
from app.api import router
from app.auth import SWAGGER_CLIENT_ID, WEB_CLIENT_ID, register_browser_clients
from app.mcp import error_message, mcp
from app.services import Forbidden, Invalid, NotFound

# Serves /mcp plus the OAuth endpoints (/authorize, /token, /register, /auth/callback, /.well-known/*).
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await register_browser_clients()
    yield


app = FastAPI(
    title="Quiz API",
    lifespan=combine_lifespans(mcp_app.lifespan, lifespan),
    swagger_ui_init_oauth={"clientId": SWAGGER_CLIENT_ID, "usePkceWithAuthorizationCodeGrant": True, "scopes": "read:user"},
)
app.include_router(router)


@app.get("/up", include_in_schema=False)
def up() -> dict[str, str]:
    return {"status": "ok"}


UI = (Path(__file__).parent / "ui.html").read_text()


@app.get("/", include_in_schema=False)
def home() -> HTMLResponse:
    """Sign-up page: GitHub sign-in, then connection instructions."""
    page = UI if config.PLUGIN_MARKETPLACE else re.sub(r"<!--plugin-->.*?<!--/plugin-->", "", UI, flags=re.S)
    for key, value in {
        "APP_URL": config.APP_URL,
        "CLIENT_ID": WEB_CLIENT_ID,
        "PLUGIN_MARKETPLACE": html.escape(config.PLUGIN_MARKETPLACE),
    }.items():
        page = page.replace("{{" + key + "}}", value)
    return HTMLResponse(page)


@app.exception_handler(NotFound)
def not_found(_: Request, exc: NotFound) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(Invalid)
def invalid(_: Request, exc: Invalid) -> JSONResponse:
    return JSONResponse({"detail": [{"loc": [exc.field], "msg": exc.message}]}, status_code=422)


@app.exception_handler(ValidationError)
def validation_error(_: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse({"detail": error_message(exc)}, status_code=422)


@app.exception_handler(Forbidden)
def forbidden(_: Request, exc: Forbidden) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=403)


app.mount("/", mcp_app)  # last, so the REST routes above win
