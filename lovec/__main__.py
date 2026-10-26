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
    ap.add_argument("cmd", choices=["serve", "check", "run-once", "test-telegram", "photos", "photos-check"])
    ap.add_argument("arg", nargs="?", help="photos: ID ліда (L-0042 або 42)")
    ap.add_argument("--zip", action="store_true", help="photos: ще й зібрати zip")
    ap.add_argument("--per-source", type=int, default=2, help="photos-check: скільки оголошень на джерело")
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
    db.migrate_owner_portals()
    db.migrate_topreality()
    db.migrate_bezrealitky()
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

    elif a.cmd == "photos":
        from .models import parse_code
        from .photos import MESSAGES, PhotoError, get_photos, make_zip
        lid = parse_code(a.arg or "")
        if lid is None:
            sys.exit("Вкажи ID: python -m lovec photos L-0042 [--zip]")
        try:
            files = get_photos(db, lid)
        except PhotoError as e:
            sys.exit(f"❌ {MESSAGES.get(e.kind, str(e))}")
        for f in files:
            print(f"{f}  {f.stat().st_size // 1024} КБ")
        if a.zip:
            print("zip:", make_zip(db, lid, files))

    elif a.cmd == "photos-check":
        sys.exit(photos_check(db, s, a.per_source))

    elif a.cmd == "test-telegram":
        from .telegram import Telegram
        ok = Telegram(s, db).send_text("✅ Lovec підключено. Нові приватні оголошення прилітатимуть сюди.")
        print("OK" if ok else "Не вдалось: перевір TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID (і що ти написав боту /start)")


def photos_check(db, s, per_source: int) -> int:
    """Реальні оголошення з бази → свіжо відкрити сторінку → скільки фото знайдено і скачано, по джерелах."""
    import shutil
    import tempfile
    from pathlib import Path

    from .http import Fetcher
    from .photos import download, extract_image_urls
    from .sources.sites import SOURCES
    f = Fetcher(s.request_delay_seconds)
    tmp = Path(tempfile.mkdtemp(prefix="lovec-photos-"))
    bad = 0
    try:
        for src in SOURCES:
            with db.conn() as c:
                rows = [dict(r) for r in c.execute(
                    "SELECT id, url, source, ext_id FROM listings WHERE source=? ORDER BY id DESC LIMIT ?",
                    (src, per_source))]
            if not rows:
                print(f"\n=== {src}: у базі нема оголошень — пропускаю")
                continue
            print(f"\n=== {src}")
            for r in rows:
                html = f.get(r["url"], retries=1)
                if not html:
                    print(f"  L-{r['id']:04d} ❌ сторінка не відкрилась: {r['url']}")
                    bad += 1
                    continue
                urls = extract_image_urls(html, r["url"], r["source"], r["ext_id"])
                files = download(urls[:12], tmp / str(r["id"]), r["url"]) if urls else []
                mark = "✅" if files else "❌"
                bad += 0 if files else 1
                print(f"  L-{r['id']:04d} {mark} знайдено URL: {len(urls)}, скачано (з перших 12): {len(files)}")
                for u in urls[:3]:
                    print(f"      {u}")
    finally:
        f.close()
        shutil.rmtree(tmp, ignore_errors=True)
    print("\nВсе ок" if not bad else f"\nПроблем: {bad} — скинь цей вивід, підправлю селектори")
    return 1 if bad else 0


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
                  f"продавець={x['seller']!r} тел={x['phones']} опис={x['desc_len']} симв. фото={x.get('photos', 0)}\n"
                  f"      → {x['category']} (score {x['score']}): {'; '.join(x['reasons'][:5])}")
            if x["weak"]:
                print("      ⚠️ слабо розпарсилось — глянь селектори в lovec/sources/sites.py")
        bad += 0 if r["ok"] else 1
    print(f"\n{'Все ок' if not bad else f'Проблемних пошуків: {bad}'}")
    return 1 if bad else 0


if __name__ == "__main__":
    main()
