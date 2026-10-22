"""CLI:
  python -m lovec serve          — дашборд + скрейпер + Telegram (для VPS)
  python -m lovec check          — перевірити, що кожен пошук парситься (без запису в БД)
  python -m lovec run-once       — один прохід зі збереженням і пушами
  python -m lovec test-telegram  — тестове повідомлення в Telegram
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="lovec")
    ap.add_argument("cmd", choices=["serve", "check", "run-once", "test-telegram"])
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--details", type=int, default=2, help="check: скільки деталок відкрити на пошук")
    a = ap.parse_args()

    from .settings import load
    s = load()

    if a.cmd == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(s), host="0.0.0.0", port=a.port, log_level="info")

    elif a.cmd == "check":
        sys.exit(check(s, a.details))

    elif a.cmd == "run-once":
        from .db import DB
        from .scraper import run_once
        from .telegram import Telegram
        db = DB(s.db_path)
        print(run_once(s, db, Telegram(s, db).send_lead))

    elif a.cmd == "test-telegram":
        from .db import DB
        from .telegram import Telegram
        ok = Telegram(s, DB(s.db_path)).send_text("✅ Lovec підключено. Нові приватні оголошення прилітатимуть сюди.")
        print("OK" if ok else "Не вдалось: перевір TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID (і що ти написав боту /start)")


def check(s, n_details: int) -> int:
    """Головний інструмент обслуговування: сайти міняють верстку — ця команда покаже де."""
    from .classify import classify
    from .http import Fetcher
    from .sources.sites import SOURCES

    f = Fetcher(s.request_delay_seconds)
    bad = 0
    for search in s.searches:
        src = SOURCES[search.source]
        print(f"\n=== {search.name}\n    {search.url}")
        html = f.get(search.url)
        if not html:
            print("    ❌ сторінка не завантажилась"); bad += 1; continue
        links = src.extract_links(html, search.url)
        print(f"    {'✅' if links else '❌'} посилань на оголошення: {len(links)}")
        if not links:
            bad += 1; continue
        for ext_id, url in links[:n_details]:
            d = f.get(url)
            if not d:
                print(f"    ❌ деталка {url}"); continue
            l = classify(src.parse_detail(d, ext_id, url))
            print(f"    • {l.title[:70]!r}\n      ціна={l.price_text!r} місце={l.location!r} продавець={l.seller_name!r} "
                  f"тел={l.phones} опис={len(l.description)} симв.\n"
                  f"      → {l.category} (score {l.score}): {'; '.join(l.reasons[:5])}")
            if not l.title or len(l.description) < 50:
                print("      ⚠️ слабо розпарсилось — глянь селектори в lovec/sources/sites.py")
    f.close()
    print(f"\n{'Все ок' if not bad else f'Проблемних пошуків: {bad}'}")
    return 1 if bad else 0


if __name__ == "__main__":
    main()
