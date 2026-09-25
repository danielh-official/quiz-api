from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api import router
from app.mcp import error_message, mcp
from app.services import Forbidden, Invalid, NotFound

# Serves /mcp plus the OAuth endpoints (/authorize, /token, /register, /auth/callback, /.well-known/*).
mcp_app = mcp.http_app(path="/mcp", stateless_http=True)

app = FastAPI(title="Quiz API", lifespan=mcp_app.lifespan)
app.include_router(router)


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
