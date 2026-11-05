from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.pipeline import get_ml
from app.routers import admin, audit, auth, enroll, records, search


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Models load on first use otherwise, so a broken GPU would pass the health check.
    if get_settings().require_gpu:
        get_ml()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    production = settings.env == "production"
    app = FastAPI(
        title="Rourkela Police Face Match",
        lifespan=lifespan,
        version="1.0.0",
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
    )
    for module in (auth, search, records, enroll, audit, admin):
        app.include_router(module.router)
    if settings.dev_login and not production:
        app.include_router(auth.dev_router)

    @app.get("/healthz", include_in_schema=False)
    def healthz():
        return {"status": "ok"}

    @app.exception_handler(Exception)
    async def unhandled(_: Request, __: Exception):
        # Details stay in the server log. Paths and values don't go back to the browser.
        return JSONResponse({"detail": "Something broke on our side."}, status_code=500)

    return app


app = create_app()
