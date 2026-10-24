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


def check_search(search, s: Settings, n_details: int = 1) -> dict:
    """Діагностика одного пошуку без запису в БД: чи парситься список і деталки."""
    src = SOURCES.get(search.source)
    out: dict = {"ok": False, "links": 0, "samples": [], "error": ""}
    if not src:
        out["error"] = f"невідоме джерело {search.source}"
        return out
    f = Fetcher(s.request_delay_seconds)
    try:
        html = f.get(search.url)
        if not html:
            out["error"] = "сторінка не завантажилась (бан, 404 або сайт лежить)"
            return out
        links = src.extract_links(html, search.url)
        out["links"] = len(links)
        if not links:
            out["error"] = "0 посилань на оголошення — змінилась верстка або неправильний URL"
            return out
        for ext_id, url in links[:n_details]:
            d = f.get(url)
            if not d:
                out["samples"].append({"url": url, "error": "деталка не завантажилась"})
                continue
            l = classify(src.parse_detail(d, ext_id, url))
            weak = not l.title or len(l.description) < 50
            out["samples"].append({"url": url, "title": l.title, "price": l.price_text, "location": l.location,
                                   "seller": l.seller_name, "phones": l.phones, "desc_len": len(l.description),
                                   "category": l.category, "score": l.score, "reasons": l.reasons, "weak": weak})
        out["ok"] = not any(x.get("error") or x.get("weak") for x in out["samples"])
        return out
    finally:
        f.close()


def run_once(s: Settings, db: DB, notify: Notifier | None = None) -> dict:
    fetcher = Fetcher(s.request_delay_seconds)
    silent = s.first_run_silent and db.get_kv("first_run_done") != "1"
    summary = {"lists_ok": 0, "lists_failed": [], "new": 0, "private": 0, "suspicious": 0, "agency": 0,
               "notified": 0, "silent": silent}
    budget = s.max_new_per_run if not silent else 10_000
    ai_blocked = False

    try:
        # Оренда першою: там швидкість вирішує, і бюджет нових деталок іде спершу на неї
        for search in sorted(s.searches, key=lambda x: x.deal != "prenajom"):
            src = SOURCES.get(search.source)
            if not src:
                log.error("Невідоме джерело %s", search.source)
                continue
            html = fetcher.get(search.url)
            if not html:
                db.touch_search(search.url, -1)
                summary["lists_failed"].append(search.name)
                continue
            links = src.extract_links(html, search.url)
            db.touch_search(search.url, len(links))
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
                if db.exists(f"{src.name}:{ext_id}") or not db.unseen([f"deleted:{src.name}:{ext_id}"]):
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
                classify(l, db.phone_usage(l.match_keys, l.key), db.blacklisted(l.match_keys), **kw)

                # Bazoš: якщо схоже на приватника — перевіримо, скільки ще оголошень у цього юзера
                if isinstance(src, Bazos) and l.category != "agency" and l.extra.get("user_url"):
                    uhtml = fetcher.get(l.extra["user_url"])
                    if uhtml:
                        l.seller_listing_count = Bazos.count_user_listings(uhtml)
                        classify(l, db.phone_usage(l.match_keys, l.key), db.blacklisted(l.match_keys), **kw)

                from . import props
                from .districts import detect
                l.district = detect(l.location, l.title, l.description)
                props.enrich(l)
                lid = db.save(l, search.name, search.deal)
                db.mark_seen(url)
                summary["new"] += 1
                summary[l.category] += 1

                want = (l.category == "private" and s.notify_private) or \
                       (l.category == "suspicious" and s.notify_suspicious)
                if want and s.notify_districts:
                    want = (l.district in s.notify_districts) if l.district else s.notify_unknown_district
                if want and s.notify_filter:
                    want = props.passes(db.get(lid), s.notify_filter)
                if l.category != "agency" and s.ai_auto and s.ai_api_key and not ai_blocked:
                    try:
                        from .ai import summarize
                        db.set_ai(lid, summarize(db.get(lid), s.ai_provider, s.ai_api_key, s.ai_model, s.ai_base_url))
                    except Exception as e:  # noqa: BLE001 — AI не має ламати пуш
                        log.warning("AI summary %s: %s", lid, e)
                        if "ліміт" in str(e):
                            ai_blocked = True  # безкоштовний ліміт вичерпано — решта цього проходу без AI
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
