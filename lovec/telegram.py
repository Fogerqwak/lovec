"""Telegram: пуш нових лідів + кнопки статусів (long polling, без вебхуків)."""
from __future__ import annotations

import asyncio
import html
import json
import logging

import httpx

from .db import DB
from .models import STATUSES
from .settings import Settings

log = logging.getLogger(__name__)

SOURCE_NAMES = {"bazos": "Bazoš", "nehnutelnosti": "Nehnuteľnosti", "reality_sk": "Reality.sk",
                "topreality": "TopReality"}
QUICK = ["called", "no_answer", "meeting", "not_interesting", "agent"]


def fmt_phone(p: str) -> str:
    return f"+{p[:3]} {p[3:6]} {p[6:9]} {p[9:]}" if len(p) == 12 else p


def render(row: dict, public_url: str = "") -> str:
    e = html.escape
    head = {"private": "🟢 <b>Приватна особа</b>", "suspicious": "🟡 <b>Можливо RK</b> — глянь причини"}.get(
        row["category"], "🔴 RK")
    deal = "продаж" if row.get("deal") == "predaj" else "оренда"
    lines = [f"{head} · {deal} · {SOURCE_NAMES.get(row['source'], row['source'])}",
             f'<a href="{e(row["url"])}">{e(row["title"] or "без назви")}</a>']
    meta = [x for x in [row.get("price_text"), row.get("location")] if x]
    if meta:
        lines.append("💶 " + " · ".join(e(x) for x in meta))
    if row.get("phones"):
        lines.append("📱 " + ", ".join(f"<code>{fmt_phone(p)}</code>" for p in row["phones"].split(",") if p))
    if row.get("seller_name"):
        lines.append(f"👤 {e(row['seller_name'])}")
    if row.get("wants_no_rk"):
        lines.append("🤫 <i>Просить без ріелторів — заходь з цінністю, не з «продам вашу квартиру»</i>")
    if row.get("ai"):
        try:
            from .ai import one_liner
            lines.append(f"🤖 {e(one_liner(json.loads(row['ai'])))}")
        except (ValueError, TypeError):
            pass
    desc = (row.get("description") or "")[:280]
    if desc:
        lines.append(f"\n<i>{e(desc)}{'…' if len(row.get('description') or '') > 280 else ''}</i>")
    reasons = json.loads(row.get("reasons") or "[]")
    if reasons:
        r = e("; ".join(reasons[:6]))
        lines.append(f"\n⚠️ {r}" if row["category"] == "suspicious" else f"\n<span class=\"tg-spoiler\">score {row['score']}: {r}</span>")
    st = row.get("status", "new")
    if st != "new":
        lines.append(f"\n<b>Статус:</b> {STATUSES.get(st, st)}")
    return "\n".join(lines)


def keyboard(lid: int, current: str = "new", app_url: str = "") -> dict:
    btns = [{"text": ("• " if k == current else "") + STATUSES[k], "callback_data": f"s:{k}:{lid}"} for k in QUICK]
    rows = [btns[:3], btns[3:]]
    if app_url:  # web_app-кнопки Telegram дозволяє лише в особистому чаті з ботом
        rows.append([{"text": "📋 Картка в Lovec", "web_app": {"url": f"{app_url}/#lead-{lid}"}}])
    return {"inline_keyboard": rows}


class Telegram:
    def __init__(self, s: Settings, db: DB):
        self.s, self.db = s, db
        self.api = f"https://api.telegram.org/bot{s.telegram_token}"
        self.enabled = bool(s.telegram_token and s.telegram_chat_id)
        # Mini App потребує HTTPS; у групах web_app-кнопки заборонені
        self.app_url = s.public_url if s.public_url.startswith("https://") else ""
        self.private_chat = not str(s.telegram_chat_id).startswith("-")

    def _kb(self, lid: int, status: str = "new") -> dict:
        return keyboard(lid, status, self.app_url if self.private_chat else "")

    def setup_menu(self) -> None:
        """Кнопка «Lovec» зліва від поля вводу відкриває Mini App."""
        if not self.s.telegram_token:
            return
        try:
            httpx.post(f"{self.api}/setMyCommands", timeout=20, json={"commands": [
                {"command": "app", "description": "Відкрити Lovec"},
                {"command": "stats", "description": "Зведення"},
                {"command": "id", "description": "Мій chat_id"}]})
            if self.app_url:
                r = httpx.post(f"{self.api}/setChatMenuButton", timeout=20, json={
                    "menu_button": {"type": "web_app", "text": "Lovec", "web_app": {"url": self.app_url}}})
                log.info("Menu button: %s", r.json())
            else:
                log.warning("PUBLIC_URL не https — Mini App не підключено (Telegram вимагає HTTPS)")
        except httpx.HTTPError as e:
            log.warning("setup_menu: %s", e)

    # синхронно — викликається зі скрейпера (окремий потік)
    def send_lead(self, lid: int, row: dict) -> None:
        if not self.enabled:
            return
        r = httpx.post(f"{self.api}/sendMessage", timeout=20, json={
            "chat_id": self.s.telegram_chat_id, "text": render(row, self.s.public_url), "parse_mode": "HTML",
            "disable_web_page_preview": False, "reply_markup": self._kb(lid)})
        data = r.json()
        if not data.get("ok"):
            log.error("Telegram: %s", data)
            return
        self.db.mark_notified(lid, data["result"]["message_id"])

    def send_text(self, text: str) -> bool:
        if not self.enabled:
            return False
        r = httpx.post(f"{self.api}/sendMessage", timeout=20,
                       json={"chat_id": self.s.telegram_chat_id, "text": text, "parse_mode": "HTML"})
        return bool(r.json().get("ok"))

    # асинхронно — слухаємо натискання кнопок і команди
    async def poll_forever(self) -> None:
        if not self.enabled:
            log.info("Telegram вимкнено (нема TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)")
            return
        offset = 0
        async with httpx.AsyncClient(timeout=40) as c:
            while True:
                try:
                    r = await c.get(f"{self.api}/getUpdates",
                                    params={"offset": offset, "timeout": 30,
                                            "allowed_updates": json.dumps(["callback_query", "message"])})
                    data = r.json()
                    if not data.get("ok"):
                        # 401 = невірний токен, 409 = бот уже слухається деінде. Не долбимо API.
                        log.error("Telegram getUpdates: %s %s", r.status_code, data.get("description"))
                        await asyncio.sleep(300 if r.status_code == 401 else 30)
                        continue
                    for u in data.get("result", []):
                        offset = u["update_id"] + 1
                        await self._handle(c, u)
                except (httpx.HTTPError, ValueError) as e:
                    log.warning("Telegram poll: %s", e)
                    await asyncio.sleep(5)

    async def _handle(self, c: httpx.AsyncClient, u: dict) -> None:
        cq = u.get("callback_query")
        if cq:
            chat = str(cq.get("message", {}).get("chat", {}).get("id"))
            if chat != str(self.s.telegram_chat_id):
                return  # чужі натискання ігноруємо
            try:
                _, status, lid = cq["data"].split(":")
                lid = int(lid)
            except ValueError:
                return
            row = await asyncio.to_thread(self.db.set_status, lid, status)
            await c.post(f"{self.api}/answerCallbackQuery",
                         json={"callback_query_id": cq["id"], "text": STATUSES.get(status, status)})
            if row:
                await c.post(f"{self.api}/editMessageText", json={
                    "chat_id": chat, "message_id": cq["message"]["message_id"], "parse_mode": "HTML",
                    "text": render(row, self.s.public_url), "reply_markup": self._kb(lid, status)})
            return

        msg = u.get("message") or {}
        chat = str(msg.get("chat", {}).get("id"))
        text = (msg.get("text") or "").strip()
        if text.startswith("/id") or (text.startswith("/start") and not self.app_url):
            # Допомагає дізнатись свій chat_id при налаштуванні
            await c.post(f"{self.api}/sendMessage", json={"chat_id": chat, "text": f"chat_id: {chat}"})
        elif text.startswith("/start") or text.startswith("/app"):
            body = {"chat_id": chat, "text": f"Lovec 🎯\nchat_id: {chat}"}
            if self.app_url and not chat.startswith("-"):
                body["reply_markup"] = {"inline_keyboard": [[{"text": "Відкрити Lovec", "web_app": {"url": self.app_url}}]]}
            await c.post(f"{self.api}/sendMessage", json=body)
        elif text.startswith("/stats") and chat == str(self.s.telegram_chat_id):
            st = await asyncio.to_thread(self.db.stats)
            cats = st["by_category"]
            await c.post(f"{self.api}/sendMessage", json={"chat_id": chat, "text": (
                f"Сьогодні лідів: {st['leads_today']}\n"
                f"🟢 {cats.get('private', 0)}  🟡 {cats.get('suspicious', 0)}  🔴 {cats.get('agency', 0)}\n"
                f"Останній прохід: {st['last_run'] or '—'}")})
