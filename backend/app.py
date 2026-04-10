# PitCrew — main app
# Run with: uvicorn backend.app:app --reload

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from dotenv import load_dotenv

# Resolve .env relative to this file so it works regardless of cwd
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

from backend.db import init_db, close_db
from backend.routes.cars import router as cars_router
from backend.routes.journal import router as journal_router
from backend.routes.views import router as views_router
from backend.routes.parts import router as parts_router
from backend.routes.manuals import router as manuals_router

# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Directories ──────────────────────────────────────────────────────────────

STATIC_DIR = os.path.join(os.path.dirname(__file__), '..', 'static')
UPLOADS_DIR = os.path.join(STATIC_DIR, 'uploads')
os.makedirs(os.path.join(UPLOADS_DIR, 'manuals'), exist_ok=True)
os.makedirs(os.path.join(UPLOADS_DIR, 'views'), exist_ok=True)

# ── App ──────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("PitCrew started")
    yield
    await close_db()
    log.info("PitCrew stopped")

app = FastAPI(lifespan=lifespan)

# Mount routers
app.include_router(cars_router)
app.include_router(journal_router)
app.include_router(views_router)
app.include_router(parts_router)
app.include_router(manuals_router)

# Static frontend
app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')


@app.get('/')
async def index():
    return FileResponse(os.path.join(STATIC_DIR, 'index.html'))


# ── Chat stub ────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    car_id: Optional[int] = None


@app.post('/api/chat')
async def chat(req: ChatRequest):
    return {
        'response': f'(stub) you said: {req.message}',
        'citations': [],
    }
