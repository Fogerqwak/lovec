"""Mini App / дашборд + фонові цикли (скрейпер і Telegram) в одному процесі.

Авторизація: у Telegram — підписаний initData (заголовок X-Tg-Init-Data),
у звичайному браузері — basic auth (DASHBOARD_USER / DASHBOARD_PASSWORD).
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

from .db import DB
from .models import STATUSES
from .scraper import check_search, run_once
from .settings import Search, Settings, effective, load
from .sources.sites import SOURCES
from .telegram import Telegram
from .tgauth import validate_init_data

log = logging.getLogger(__name__)
basic = HTTPBasic(auto_error=False)

SOURCE_HOSTS = {"bazos.sk": "bazos", "nehnutelnosti.sk": "nehnutelnosti", "reality.sk": "reality_sk",
                "topreality.sk": "topreality"}


def source_from_url(url: str) -> str | None:
    from urllib.parse import urlparse
    host = urlparse(url).hostname or ""
    for h, name in SOURCE_HOSTS.items():
        if host == h or host.endswith("." + h):
            return name
    return None


class LeadPatch(BaseModel):
    status: str | None = None
    note: str | None = None


class SearchIn(BaseModel):
    url: str
    name: str = ""
    deal: str = "predaj"


class SearchPatch(BaseModel):
    name: str | None = None
    url: str | None = None
    deal: str | None = None
    enabled: bool | None = None


def create_app(base: Settings | None = None, background: bool = True) -> FastAPI:
    base = base or load()
    db = DB(base.db_path)
    db.seed_searches(base.searches)
    db.migrate_searches()
    from .db import reclassify_all
    reclassify_all(db, base.phone_reuse_limit, base.private_max)
    state = {"running": False}
    lock = asyncio.Lock()

    def cur() -> Settings:
        return effective(base, db)

    async def run_guarded():
        async with lock:
            state["running"] = True
            try:
                s = cur()
                return await asyncio.to_thread(run_once, s, db, Telegram(s, db).send_lead)
            finally:
                state["running"] = False

    async def scrape_loop():
        await asyncio.sleep(3)
        while True:
            s = cur()
            if not s.paused:
                try:
                    summary = await run_guarded()
                    if summary["lists_ok"] == 0 and summary["lists_failed"]:
                        await asyncio.to_thread(Telegram(s, db).send_text,
                                                "⚠️ Lovec: жоден сайт не відповів. Відкрий Пошуки → Перевірити.")
                except Exception:  # noqa: BLE001
                    log.exception("scrape loop")
            await asyncio.sleep(s.interval_minutes * 60)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = []
        if background:
            tg = Telegram(base, db)
            tasks = [asyncio.create_task(scrape_loop()), asyncio.create_task(tg.poll_forever())]
            asyncio.create_task(asyncio.to_thread(tg.setup_menu))
        yield
        for t in tasks:
            t.cancel()

    app = FastAPI(title="Lovec", lifespan=lifespan, docs_url=None, redoc_url=None)

    def auth(x_tg_init_data: str = Header(default=""),
             cred: HTTPBasicCredentials | None = Depends(basic)) -> dict:
        if x_tg_init_data:
            user = validate_init_data(x_tg_init_data, base.telegram_token)
            if not user:
                raise HTTPException(401, "Невалідний підпис Telegram")
            if user.get("id") not in base.allowed_users:
                raise HTTPException(403, f"Нема доступу. Додай {user.get('id')} у TELEGRAM_ALLOWED_USERS")
            return {"via": "telegram", "id": user["id"], "name": user.get("first_name", "")}
        if cred and base.dashboard_password and \
                secrets.compare_digest(cred.username, base.dashboard_user) and \
                secrets.compare_digest(cred.password, base.dashboard_password):
            return {"via": "basic", "id": 0, "name": cred.username}
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    A = [Depends(auth)]

    # ---------- сторінка ----------
    @app.get("/health")
    def health():
        return {"ok": True, "last_run": db.get_kv("last_run")}

    @app.get("/", response_class=HTMLResponse)
    def index():
        # Сторінка сама даних не містить — усе через API з авторизацією
        page = (Path(__file__).parent / "app.html").read_text(encoding="utf-8")
        return page.replace("__STATUSES__", json.dumps(STATUSES, ensure_ascii=False))

    @app.get("/api/me")
    def me(user: dict = Depends(auth)):
        return user

    # ---------- ліди ----------
    @app.get("/api/leads", dependencies=A)
    def leads(category: str = "private,suspicious", status: str = "", deal: str = "", q: str = ""):
        rows = db.leads(category or None, None, deal or None, q or None)
        if status:
            wanted = set(status.split(","))
            rows = [r for r in rows if r["status"] in wanted]
        for r in rows:
            r["reasons"] = json.loads(r.get("reasons") or "[]")
            ai = json.loads(r["ai"]) if r.get("ai") else None
            r["ai"] = {"tldr": ai.get("tldr"), "verdict": (ai.get("seller") or {}).get("verdict")} if ai else None
            r["description"] = (r.get("description") or "")[:300]
        return rows

    def _lead(lid: int) -> dict:
        r = db.get(lid)
        if not r:
            raise HTTPException(404)
        r["reasons"] = json.loads(r.get("reasons") or "[]")
        r["ai"] = json.loads(r["ai"]) if r.get("ai") else None
        return r

    @app.get("/api/leads/{lid}", dependencies=A)
    def lead(lid: int):
        return _lead(lid)

    @app.post("/api/leads/{lid}/ai", dependencies=A)
    async def lead_ai(lid: int):
        from .ai import AIError, summarize
        row = db.get(lid)
        if not row:
            raise HTTPException(404)
        try:
            ai = await asyncio.to_thread(summarize, row, base.anthropic_api_key, base.ai_model)
        except AIError as e:
            raise HTTPException(400, str(e))
        db.set_ai(lid, ai)
        return _lead(lid)

    @app.patch("/api/leads/{lid}", dependencies=A)
    def patch_lead(lid: int, p: LeadPatch):
        if p.status and p.status not in STATUSES:
            raise HTTPException(400, "bad status")
        if not db.set_status(lid, p.status, p.note):
            raise HTTPException(404)
        return {"ok": True}

    @app.get("/api/stats", dependencies=A)
    def stats():
        return {**db.stats(), "running": state["running"], "paused": cur().paused}

    @app.post("/api/run", dependencies=A)
    async def run_now():
        if lock.locked():
            return {"ok": False, "msg": "Вже перевіряю"}
        asyncio.create_task(run_guarded())
        return {"ok": True}

    # ---------- пошуки ----------
    @app.get("/api/searches", dependencies=A)
    def searches():
        return db.searches()

    @app.post("/api/searches", dependencies=A)
    def add_search(p: SearchIn):
        src = source_from_url(p.url.strip())
        if not src:
            raise HTTPException(400, "Підтримуються лише bazos.sk, nehnutelnosti.sk, reality.sk, topreality.sk")
        if p.deal not in ("predaj", "prenajom"):
            raise HTTPException(400, "deal: predaj або prenajom")
        name = p.name.strip() or f"{src} · {'predaj' if p.deal == 'predaj' else 'prenájom'}"
        return {"id": db.add_search(name, src, p.url.strip(), p.deal)}

    @app.patch("/api/searches/{sid}", dependencies=A)
    def patch_search(sid: int, p: SearchPatch):
        if p.url and source_from_url(p.url) is None:
            raise HTTPException(400, "Непідтримуваний сайт")
        db.update_search(sid, name=p.name, url=p.url, deal=p.deal,
                         enabled=None if p.enabled is None else int(p.enabled))
        return {"ok": True}

    @app.delete("/api/searches/{sid}", dependencies=A)
    def del_search(sid: int):
        db.delete_search(sid)
        return {"ok": True}

    @app.post("/api/searches/{sid}/check", dependencies=A)
    async def check(sid: int):
        row = next((r for r in db.searches() if r["id"] == sid), None)
        if not row:
            raise HTTPException(404)
        res = await asyncio.to_thread(check_search, Search(row["name"], row["source"], row["url"], row["deal"]),
                                      cur(), 1)
        from .db import now
        db.update_search(sid, last_check=now(), last_links=-1 if res["error"].startswith("сторінка") else res["links"])
        return res

    # ---------- налаштування ----------
    @app.get("/api/settings", dependencies=A)
    def get_settings():
        s = cur()
        return {k: getattr(s, k) for k in Settings.TUNABLE} | {
            "sources": list(SOURCES), "telegram": bool(base.telegram_token and base.telegram_chat_id),
            "ai": bool(base.anthropic_api_key)}

    @app.patch("/api/settings", dependencies=A)
    def patch_settings(p: dict):
        overrides = json.loads(db.get_kv("settings", "{}") or "{}")
        for k, v in p.items():
            if k not in Settings.TUNABLE:
                raise HTTPException(400, f"невідомий параметр {k}")
            try:
                overrides[k] = Settings.TUNABLE[k](v)
            except (TypeError, ValueError):
                raise HTTPException(400, f"погане значення для {k}")
        if overrides.get("interval_minutes", 5) < 3:
            raise HTTPException(400, "Інтервал мінімум 3 хв, інакше забанять")
        db.set_kv("settings", json.dumps(overrides))
        return get_settings()

    return app
