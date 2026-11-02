import asyncio
import contextlib
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import clock, db
from .config import get_settings
from .routers import account, admin, auctions, listings, orders, rooms
from .scheduler import run_forever
from .services.bids import BidError
from .services.escrow import EscrowError


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    db.init_engine(s.database_url)
    await db.create_all()
    Path(s.upload_dir).mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(run_forever()) if s.run_scheduler else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Hammerly API", version="1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in s.cors_origins.split(",") if o.strip()], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(BidError)
    @app.exception_handler(EscrowError)
    async def domain_error(_: Request, e):
        return JSONResponse(status_code=e.status, content={
            "code": e.code, "message": e.message, **getattr(e, "extra", {})})

    for r in (account.router, listings.router, auctions.router, orders.router, rooms.router, admin.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health")
    async def health():
        return {"ok": True, "server_time": clock.now()}

    Path(s.upload_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/media", StaticFiles(directory=s.upload_dir), name="media")

    dist = Path(os.environ.get("FRONTEND_DIST", "/app/frontend_dist"))
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = dist / path
            return FileResponse(f if path and f.is_file() else dist / "index.html")
    return app


app = create_app()
