"""Догрузка старих оголошень (за останні N днів) — тихо, без пушів, у вкладку «Старі».

Два етапи, щоб не блокувати пошук свіжих:
  1) collect — гортаємо сторінки списків (2, 3, …) і складаємо посилання в чергу. Дешево: ~1 запит на сторінку.
  2) process_chunk — після кожного звичайного проходу відкриваємо частину черги (деталки), поки вона не спорожніє.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone

from .db import DB
from .http import Fetcher
from .settings import Settings
from .sources.sites import SOURCES

log = logging.getLogger(__name__)

MAX_PAGES = 15        # на один пошук: Bazoš 20/стор., інші ~20-30/стор.
CHUNK = 25            # деталок за один цикл (≈2 хв при паузах 2-5 с)


def _state(db: DB, **kw) -> None:
    st = json.loads(db.get_kv("backfill_state", "{}") or "{}")
    st.update(kw)
    db.set_kv("backfill_state", json.dumps(st, ensure_ascii=False))


def collect(s: Settings, db: DB, days: int = 14, max_pages: int = MAX_PAGES) -> dict:
    cutoff = date.today() - timedelta(days=days)
    _state(db, phase="collect", days=days, cutoff=cutoff.isoformat(), started=datetime.now(timezone.utc).isoformat(),
           pages=0, queued=0, error="")
    f = Fetcher(s.request_delay_seconds)
    total_pages = queued = 0
    try:
        for search in sorted(s.searches, key=lambda x: x.deal != "prenajom"):
            src = SOURCES.get(search.source)
            if not src:
                continue
            url, prev_ids = search.url, set()
            for page_no in range(1, max_pages + 1):
                html = f.get(url)
                if not html:
                    break
                links = src.extract_links(html, url)
                ids = {i for i, _ in links}
                if not links or ids <= prev_ids:
                    break  # сторінки скінчились або сайт повертає ту саму
                prev_ids |= ids
                queued += db.queue_add([(src.name, i, u, search.name, search.deal) for i, u in links])
                total_pages += 1
                _state(db, pages=total_pages, queued=db.queue_size(), current=search.name)

                # зупинка за датою, де сайт її показує (Bazoš): уся сторінка старша за cutoff
                dates = src.list_dates(html)
                if dates and max(dates) < cutoff:
                    break
                nxt = src.next_page(html, url, page_no)
                if not nxt or nxt == url:
                    break
                url = nxt
    except Exception as e:  # noqa: BLE001
        log.exception("backfill collect")
        _state(db, error=str(e)[:200])
    finally:
        f.close()
    _state(db, phase="process" if db.queue_size() else "done", pages=total_pages, queued=db.queue_size())
    log.info("Backfill: %d сторінок, %d у черзі", total_pages, db.queue_size())
    return {"pages": total_pages, "queued": db.queue_size()}


def process_chunk(s: Settings, db: DB, n: int = CHUNK) -> int:
    """Відкрити до n оголошень з черги. Повертає скільки збережено."""
    from .scraper import process_listing
    items = db.queue_take(n)
    if not items:
        return 0
    st = json.loads(db.get_kv("backfill_state", "{}") or "{}")
    cutoff = st.get("cutoff")
    f = Fetcher(s.request_delay_seconds)
    saved = 0
    try:
        for it in items:
            src = SOURCES.get(it["source"])
            if src is None or db.exists(it["key"]):
                db.queue_done(it["key"])
                continue
            res = process_listing(src, f, db, s, it["ext_id"], it["url"], it["search_name"], it["deal"], backlog=True)
            db.queue_done(it["key"])  # навіть якщо деталка не відкрилась — не крутимо вічно
            if res is None:
                continue
            lid, l = res
            # точна дата старша за межу — не потрібне (Bazoš «топує» старі оголошення нагору)
            if cutoff and l.posted_at and l.posted_at < cutoff:
                db.delete_listing(lid, remember=False)  # URL лишається «баченим», тож пушем не прийде
                continue
            saved += 1
    finally:
        f.close()
    left = db.queue_size()
    _state(db, phase="process" if left else "done", queued=left,
           saved=int(st.get("saved", 0)) + saved)
    return saved
