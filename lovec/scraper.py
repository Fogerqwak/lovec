"""Один прохід: списки -> нові посилання -> деталки -> класифікація -> БД -> пуш."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable

from .classify import classify
from .db import DB
from .http import Fetcher
from .settings import Settings
from .sources.sites import SOURCES, Bazos

log = logging.getLogger(__name__)

Notifier = Callable[[int, dict], None]  # (listing_id, row) -> None


def run_once(s: Settings, db: DB, notify: Notifier | None = None) -> dict:
    fetcher = Fetcher(s.request_delay_seconds)
    silent = s.first_run_silent and db.get_kv("first_run_done") != "1"
    summary = {"lists_ok": 0, "lists_failed": [], "new": 0, "private": 0, "suspicious": 0, "agency": 0,
               "notified": 0, "silent": silent}
    budget = s.max_new_per_run if not silent else 10_000

    try:
        for search in s.searches:
            src = SOURCES.get(search.source)
            if not src:
                log.error("Невідоме джерело %s", search.source)
                continue
            html = fetcher.get(search.url)
            if not html:
                summary["lists_failed"].append(search.name)
                continue
            links = src.extract_links(html, search.url)
            if not links:
                log.warning("%s: 0 посилань — змінилась верстка або URL пошуку", search.name)
                summary["lists_failed"].append(f"{search.name} (0 посилань)")
                continue
            summary["lists_ok"] += 1

            fresh = db.unseen([u for _, u in links])
            log.info("%s: %d на сторінці, %d нових", search.name, len(links), len(fresh))
            by_url = {u: i for i, u in links}

            for url in fresh:
                if budget <= 0:
                    break  # решта піде наступним проходом (url ще не позначений seen)
                ext_id = by_url[url]
                if db.exists(f"{src.name}:{ext_id}"):
                    db.mark_seen(url)
                    continue

                if silent:
                    # Перший запуск: просто запам'ятовуємо, що вже висить на сайті
                    db.mark_seen(url)
                    continue

                budget -= 1
                detail = fetcher.get(url)
                if not detail:
                    continue  # спробуємо наступного разу
                try:
                    l = src.parse_detail(detail, ext_id, url)
                except Exception:  # noqa: BLE001 — одне криве оголошення не має валити прохід
                    log.exception("parse fail %s", url)
                    db.mark_seen(url)
                    continue

                kw = dict(phone_reuse_limit=s.phone_reuse_limit, private_max=s.private_max,
                          suspicious_max=s.suspicious_max)
                classify(l, db.phone_usage(l.phones, l.key), db.blacklisted(l.phones), **kw)

                # Bazoš: якщо схоже на приватника — перевіримо, скільки ще оголошень у цього юзера
                if isinstance(src, Bazos) and l.category != "agency" and l.extra.get("user_url"):
                    uhtml = fetcher.get(l.extra["user_url"])
                    if uhtml:
                        l.seller_listing_count = Bazos.count_user_listings(uhtml)
                        classify(l, db.phone_usage(l.phones, l.key), db.blacklisted(l.phones), **kw)

                lid = db.save(l, search.name, search.deal)
                db.mark_seen(url)
                summary["new"] += 1
                summary[l.category] += 1

                want = (l.category == "private" and s.notify_private) or \
                       (l.category == "suspicious" and s.notify_suspicious)
                if want and notify:
                    try:
                        notify(lid, db.get(lid))
                        summary["notified"] += 1
                    except Exception:  # noqa: BLE001
                        log.exception("notify fail")
    finally:
        fetcher.close()

    if silent and summary["lists_ok"] > 0:
        db.set_kv("first_run_done", "1")
    db.set_kv("last_run", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    db.set_kv("last_run_summary", str(summary))
    log.info("Прохід завершено: %s", summary)
    return summary
