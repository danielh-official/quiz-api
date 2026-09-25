from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans
from pydantic import ValidationError

from app.web import router as web_router
from app.api import router as api_router
from app.auth import SWAGGER_CLIENT_ID, register_browser_clients
from app.mcp import error_message, mcp
from app.services import Forbidden, Invalid, NotFound

# Serves /mcp plus the OAuth endpoints (/authorize, /token, /register, /auth/callback, /.well-known/*).
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
    await register_browser_clients()
    yield


app = FastAPI(
    title="Quiz API",
    lifespan=combine_lifespans(mcp_app.lifespan, lifespan),
    swagger_ui_init_oauth={"clientId": SWAGGER_CLIENT_ID, "usePkceWithAuthorizationCodeGrant": True, "scopes": "read:user"},
)
app.include_router(api_router)
app.include_router(web_router)


@app.get("/up", include_in_schema=False)
def up() -> dict[str, str]:
    return {"status": "ok"}


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
