"""
Smart Release Analytic System API.

    uvicorn app.main:app --reload --port 5000      (from packages/api)

Interactive docs at http://localhost:5000/docs
"""
import logging
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import risk
from .config import CORS_ORIGINS, DATABASE_URL, SEED_DEMO
from .db import pool
from .errors import psycopg_error_handler
from .realtime import hub
from .routers import audit, auth, governance, ml, notifications, releases, users

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        pool.open(wait=True, timeout=15)
    except Exception as e:  # noqa: BLE001
        raise SystemExit(
            f"\nCannot connect to the database ({DATABASE_URL.rsplit('@', 1)[-1]}).\n"
            "Make sure PostgreSQL is installed and running, then create the database once:\n"
            "  python packages/db/setup_db.py     (or: npm run db:setup)\n"
            "Docker users can instead run: docker compose up -d db\n") from e
    hub.start()
    risk.load_model()
    if SEED_DEMO and risk.state.engine is not None:
        from .demo_seed import seed
        try:
            if seed():
                logging.getLogger("seed").info("database was empty: created demo releases (SEED_DEMO=0 disables)")
        except Exception:  # noqa: BLE001 - demo data must never stop the API starting
            logging.getLogger("seed").exception("could not create demo data")
    yield
    hub.stop()
    pool.close()


app = FastAPI(title="Smart Release Analytic System", version="2.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.add_exception_handler(psycopg.Error, psycopg_error_handler)

for r in (auth.router, users.router, notifications.router, releases.router, governance.router,
          audit.router, ml.router):
    app.include_router(r)


@app.get("/api/health")
def health():
    return {"status": "ok", "model_loaded": risk.state.engine is not None,
            "model_error": risk.state.error, "realtime": hub.connected,
            "live_connections": hub.subscriber_count()}
