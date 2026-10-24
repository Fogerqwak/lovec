"""CLI:
  python -m lovec serve          — Mini App/дашборд + скрейпер + Telegram (для VPS)
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
    # httpx на INFO пише повні URL — а в URL Telegram сидить токен бота
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(prog="lovec")
    ap.add_argument("cmd", choices=["serve", "check", "run-once", "test-telegram"])
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    ap.add_argument("--details", type=int, default=2, help="check: скільки деталок відкрити на пошук")
    a = ap.parse_args()

    from .db import DB
    from .settings import effective, load
    base = load()
    db = DB(base.db_path)
    db.seed_searches(base.searches)
    db.migrate_searches()
    db.migrate_rooms_pack()
    s = effective(base, db)

    if a.cmd == "serve":
        import uvicorn
        from .web import create_app
        uvicorn.run(create_app(base), host="0.0.0.0", port=a.port, log_level="info")

    elif a.cmd == "check":
        sys.exit(check(s, a.details))

    elif a.cmd == "run-once":
        from .scraper import run_once
        from .telegram import Telegram
        print(run_once(s, db, Telegram(s, db).send_lead))

    elif a.cmd == "test-telegram":
        from .telegram import Telegram
        ok = Telegram(s, db).send_text("✅ Lovec підключено. Нові приватні оголошення прилітатимуть сюди.")
        print("OK" if ok else "Не вдалось: перевір TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID (і що ти написав боту /start)")


def check(s, n_details: int) -> int:
    """Головний інструмент обслуговування: сайти міняють верстку — ця команда покаже де."""
    from .scraper import check_search
    bad = 0
    for search in s.searches:
        r = check_search(search, s, n_details)
        print(f"\n=== {search.name}\n    {search.url}\n    {'✅' if r['links'] else '❌'} посилань: {r['links']}"
              + (f"  — {r['error']}" if r["error"] else ""))
        for x in r["samples"]:
            if x.get("error"):
                print(f"    ❌ {x['url']}: {x['error']}")
                continue
            print(f"    • {x['title'][:70]!r}\n      ціна={x['price']!r} місце={x['location']!r} "
                  f"продавець={x['seller']!r} тел={x['phones']} опис={x['desc_len']} симв.\n"
                  f"      → {x['category']} (score {x['score']}): {'; '.join(x['reasons'][:5])}")
            if x["weak"]:
                print("      ⚠️ слабо розпарсилось — глянь селектори в lovec/sources/sites.py")
        bad += 0 if r["ok"] else 1
    print(f"\n{'Все ок' if not bad else f'Проблемних пошуків: {bad}'}")
    return 1 if bad else 0


if __name__ == "__main__":
    main()
