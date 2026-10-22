"""Дашборд + фонові цикли (скрейпер і Telegram) в одному процесі."""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .db import DB
from .models import STATUSES
from .scraper import run_once
from .settings import Settings, load
from .telegram import Telegram

log = logging.getLogger(__name__)
security = HTTPBasic()


class Patch(BaseModel):
    status: str | None = None
    note: str | None = None


def create_app(s: Settings | None = None, background: bool = True) -> FastAPI:
    s = s or load()
    db = DB(s.db_path)
    tg = Telegram(s, db)
    state = {"running": False}
    lock = asyncio.Lock()

    async def run_guarded():
        async with lock:
            state["running"] = True
            try:
                return await asyncio.to_thread(run_once, s, db, tg.send_lead)
            finally:
                state["running"] = False

    async def scrape_loop():
        await asyncio.sleep(3)
        while True:
            try:
                summary = await run_guarded()
                if summary["lists_ok"] == 0 and summary["lists_failed"]:
                    await asyncio.to_thread(tg.send_text, "⚠️ Lovec: жоден сайт не відповів. Перевір логи.")
            except Exception:  # noqa: BLE001
                log.exception("scrape loop")
            await asyncio.sleep(s.interval_minutes * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if background:
            tasks = [asyncio.create_task(scrape_loop()), asyncio.create_task(tg.poll_forever())]
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Lovec", lifespan=lifespan, docs_url=None, redoc_url=None)

    def auth(cred: HTTPBasicCredentials = Depends(security)):
        if not s.dashboard_password:
            raise HTTPException(503, "Задай DASHBOARD_PASSWORD у .env")
        ok = secrets.compare_digest(cred.username, s.dashboard_user) and \
            secrets.compare_digest(cred.password, s.dashboard_password)
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    @app.get("/health")
    def health():
        return {"ok": True, "last_run": db.get_kv("last_run")}

    @app.get("/", response_class=HTMLResponse, dependencies=[Depends(auth)])
    def index():
        page = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
        return page.replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))

    @app.get("/api/leads", dependencies=[Depends(auth)])
    def leads(category: str = "private,suspicious", status: str = "", deal: str = "", q: str = ""):
        rows = db.leads(category or None, status or None, deal or None, q or None)
        for r in rows:
            r["reasons"] = json.loads(r.get("reasons") or "[]")
        return rows

    @app.get("/api/stats", dependencies=[Depends(auth)])
    def stats():
        return {**db.stats(), "running": state["running"]}

    @app.patch("/api/leads/{lid}", dependencies=[Depends(auth)])
    def patch(lid: int, p: Patch):
        if p.status and p.status not in STATUSES:
            raise HTTPException(400, "bad status")
        row = db.set_status(lid, p.status, p.note)
        if not row:
            raise HTTPException(404)
        return {"ok": True}

    @app.post("/api/run", dependencies=[Depends(auth)])
    async def run_now():
        if lock.locked():
            return {"ok": False, "msg": "вже працює"}
        asyncio.create_task(run_guarded())
        return {"ok": True}

    return app
