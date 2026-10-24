"""Налаштування: секрети з env, пошуки з config.yaml."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Search:
    name: str
    source: str
    url: str
    deal: str = "predaj"


@dataclass
class Settings:
    interval_minutes: int = 5
    request_delay_seconds: tuple[float, float] = (2.0, 5.0)
    max_new_per_run: int = 40
    first_run_silent: bool = True
    private_max: int = 1
    suspicious_max: int = 3
    phone_reuse_limit: int = 3
    notify_private: bool = True
    notify_suspicious: bool = True
    searches: list[Search] = field(default_factory=list)

    # env
    db_path: str = "data/lovec.db"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    dashboard_user: str = "admin"
    dashboard_password: str = ""
    public_url: str = ""  # напр. https://lovec.example.com — для посилань у Telegram
    allowed_users: list[int] = field(default_factory=list)  # Telegram user id з доступом до Mini App
    ai_provider: str = ""   # groq | gemini | anthropic
    ai_api_key: str = ""
    ai_model: str = ""      # порожньо = модель за замовчуванням провайдера
    ai_base_url: str = ""
    ai_auto: bool = False  # підсумок для кожного нового 🟢/🟡 одразу
    notify_districts: list[str] = field(default_factory=list)  # порожньо = всі райони
    notify_unknown_district: bool = True  # пушити, якщо район не вдалося визначити
    paused: bool = False

    # Що можна міняти з Mini App (зберігається в БД поверх config.yaml)
    TUNABLE = {"interval_minutes": int, "max_new_per_run": int, "private_max": int, "suspicious_max": int,
               "phone_reuse_limit": int, "notify_private": bool, "notify_suspicious": bool, "paused": bool, "ai_auto": bool,
               "notify_districts": list, "notify_unknown_district": bool}


def load(config_path: str | None = None) -> Settings:
    path = Path(config_path or os.getenv("LOVEC_CONFIG", "config.yaml"))
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    score = raw.get("score", {}) or {}
    notify = raw.get("notify", {}) or {}
    delay = raw.get("request_delay_seconds", [2, 5])

    s = Settings(
        interval_minutes=int(raw.get("interval_minutes", 5)),
        request_delay_seconds=(float(delay[0]), float(delay[1])),
        max_new_per_run=int(raw.get("max_new_per_run", 40)),
        first_run_silent=bool(raw.get("first_run_silent", True)),
        private_max=int(score.get("private_max", 1)),
        suspicious_max=int(score.get("suspicious_max", 3)),
        phone_reuse_limit=int(score.get("phone_reuse_limit", 3)),
        notify_private=bool(notify.get("private", True)),
        notify_suspicious=bool(notify.get("suspicious", True)),
        searches=[Search(**x) for x in raw.get("searches", [])],
        db_path=os.getenv("LOVEC_DB", "data/lovec.db"),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        dashboard_user=os.getenv("DASHBOARD_USER", "admin"),
        dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
        public_url=os.getenv("PUBLIC_URL", "").rstrip("/"),
        ai_model=os.getenv("AI_MODEL", ""),
        ai_base_url=os.getenv("AI_BASE_URL", ""),
        allowed_users=[int(x) for x in os.getenv("TELEGRAM_ALLOWED_USERS", "").replace(" ", "").split(",") if x],
    )
    # Особистий чат з ботом: chat_id == user_id, тож власник має доступ без додаткових налаштувань
    if not s.allowed_users and s.telegram_chat_id.isdigit():
        s.allowed_users = [int(s.telegram_chat_id)]
    # AI: перший знайдений ключ визначає провайдера (або явно AI_PROVIDER + AI_API_KEY)
    for prov, env in (("groq", "GROQ_API_KEY"), ("gemini", "GEMINI_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")):
        if os.getenv(env):
            s.ai_provider, s.ai_api_key = prov, os.getenv(env, "")
            break
    if os.getenv("AI_API_KEY"):
        s.ai_provider, s.ai_api_key = os.getenv("AI_PROVIDER", "groq"), os.getenv("AI_API_KEY", "")
    if s.interval_minutes < 3:
        s.interval_minutes = 3
    return s


def effective(base: Settings, db) -> Settings:
    """config.yaml + правки з Mini App (kv) + пошуки з БД."""
    import copy
    import json
    s = copy.copy(base)
    overrides = json.loads(db.get_kv("settings", "{}") or "{}")
    for k, typ in Settings.TUNABLE.items():
        if k in overrides:
            setattr(s, k, typ(overrides[k]))
    s.interval_minutes = max(3, s.interval_minutes)
    s.searches = [Search(name=r["name"], source=r["source"], url=r["url"], deal=r["deal"])
                  for r in db.searches() if r["enabled"]]
    return s
