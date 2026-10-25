"""SQLite-сховище. Одне з'єднання на виклик + WAL — безпечно між потоками."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .models import Listing

_lock = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT UNIQUE NOT NULL,
    source TEXT NOT NULL,
    ext_id TEXT NOT NULL,
    url TEXT NOT NULL,
    search_name TEXT,
    deal TEXT,
    title TEXT,
    price_eur REAL,
    price_text TEXT,
    location TEXT,
    description TEXT,
    seller_name TEXT,
    phones TEXT,
    seller_listing_count INTEGER,
    score INTEGER,
    category TEXT,
    reasons TEXT,
    wants_no_rk INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    note TEXT DEFAULT '',
    notified INTEGER DEFAULT 0,
    tg_message_id INTEGER,
    first_seen TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_listings_cat ON listings(category, first_seen);
CREATE TABLE IF NOT EXISTS listing_phones (
    listing_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    PRIMARY KEY (listing_id, phone)
);
CREATE INDEX IF NOT EXISTS ix_phone ON listing_phones(phone);
CREATE TABLE IF NOT EXISTS phone_blacklist (
    phone TEXT PRIMARY KEY,
    reason TEXT,
    added_at TEXT
);
CREATE TABLE IF NOT EXISTS seen_urls (
    url TEXT PRIMARY KEY,
    first_seen TEXT
);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source TEXT NOT NULL,
    url TEXT NOT NULL,
    deal TEXT DEFAULT 'predaj',
    enabled INTEGER DEFAULT 1,
    last_check TEXT,
    last_links INTEGER
);
"""


SORTS = {  # невідома ціна/площа — завжди в кінці
    "new": "first_seen DESC",
    "price_asc": "price_eur IS NULL, price_eur ASC, first_seen DESC",
    "price_desc": "price_eur IS NULL, price_eur DESC, first_seen DESC",
    "area_desc": "area_m2 IS NULL, area_m2 DESC, first_seen DESC",
    "ppm_asc": "(price_eur IS NULL OR area_m2 IS NULL), price_eur / area_m2 ASC, first_seen DESC",
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class DB:
    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(listings)")}
            if "ai" not in cols:
                c.execute("ALTER TABLE listings ADD COLUMN ai TEXT")
            if "district" not in cols:
                c.execute("ALTER TABLE listings ADD COLUMN district TEXT")
            for col, typ in (("prop_type", "TEXT"), ("rooms", "REAL"), ("area_m2", "REAL"), ("okres", "TEXT")):
                if col not in cols:
                    c.execute(f"ALTER TABLE listings ADD COLUMN {col} {typ}")
        self._backfill_districts()
        self._backfill_props()
        self._backfill_okres()

    def _backfill_okres(self) -> None:
        if self.get_kv("okres_v1") == "1":
            return
        from .districts import detect_okres
        with self.conn() as c:
            for r in c.execute("SELECT id, district, location FROM listings").fetchall():
                c.execute("UPDATE listings SET okres=? WHERE id=?",
                          (detect_okres(r["district"], r["location"] or ""), r["id"]))
        self.set_kv("okres_v1", "1")

    def _backfill_props(self) -> None:
        if self.get_kv("props_v2") == "1":  # v2: кращe розпізнавання кімнат у квартирах
            return
        from . import props
        with self.conn() as c:
            for r in c.execute("SELECT id, url, title, description FROM listings").fetchall():
                l = Listing("x", "x", r["url"] or "", title=r["title"] or "", description=r["description"] or "")
                props.enrich(l)
                c.execute("UPDATE listings SET prop_type=?, rooms=?, area_m2=? WHERE id=?",
                          (l.prop_type, l.rooms, l.area_m2, r["id"]))
        self.set_kv("props_v2", "1")

    def _backfill_districts(self) -> None:
        if self.get_kv("districts_v1") == "1":
            return
        from .districts import detect
        with self.conn() as c:
            for r in c.execute("SELECT id, location, title, description FROM listings").fetchall():
                c.execute("UPDATE listings SET district=? WHERE id=?",
                          (detect(r["location"] or "", r["title"] or "", r["description"] or ""), r["id"]))
        self.set_kv("districts_v1", "1")

    @contextmanager
    def conn(self):
        with _lock:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA journal_mode=WAL")
            try:
                yield c
                c.commit()
            finally:
                c.close()

    # ---- kv ----
    def get_kv(self, k: str, default: str | None = None) -> str | None:
        with self.conn() as c:
            r = c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
            return r["v"] if r else default

    def set_kv(self, k: str, v: str) -> None:
        with self.conn() as c:
            c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # ---- searches (керуються з Mini App; при першому старті беруться з config.yaml) ----
    def seed_searches(self, searches) -> None:
        if self.get_kv("searches_seeded") == "1":
            return
        with self.conn() as c:
            for x in searches:
                c.execute("INSERT INTO searches(name,source,url,deal) VALUES(?,?,?,?)",
                          (x.name, x.source, x.url, x.deal))
        self.set_kv("searches_seeded", "1")
        self.set_kv("rent_pack_v1", "1")  # свіжа установка: пошуки вже з config.yaml
        self.set_kv("rent_pack_v2", "1")

    RENT_PACK = [  # одноразово додається при оновленні (фокус на оренду)
        ("Bazoš · domy prenájom BA", "bazos", "https://reality.bazos.sk/prenajmu/dom/?hlokalita=81101&humkreis=15", "prenajom"),
        ("Nehnuteľnosti · domy prenájom BA", "nehnutelnosti", "https://www.nehnutelnosti.sk/vysledky/domy/bratislava/prenajom?order=NEWEST", "prenajom"),
        ("Reality.sk · domy prenájom BA", "reality_sk", "https://www.reality.sk/domy/bratislava/prenajom/?order=created_date-newest", "prenajom"),
    ]

    def migrate_searches(self) -> None:
        if self.get_kv("rent_pack_v1") == "1":
            return
        have = {r["url"] for r in self.searches()}
        with self.conn() as c:
            for name, src, url, deal in self.RENT_PACK:
                if url not in have:
                    c.execute("INSERT INTO searches(name,source,url,deal) VALUES(?,?,?,?)", (name, src, url, deal))
            # битий шаблон TopReality з першої версії — вимикаємо, нормальний додається з Mini App
            c.execute("UPDATE searches SET enabled=0 WHERE source='topreality' AND url LIKE '%obec=d100%'")
        self.set_kv("rent_pack_v1", "1")

    def migrate_rooms_pack(self) -> None:
        if self.get_kv("rent_pack_v2") == "1":
            return
        url = "https://reality.bazos.sk/prenajmu/podnajom/?hlokalita=81101&humkreis=15"
        if url not in {r["url"] for r in self.searches()}:
            self.add_search("Bazoš · izby / podnájom BA", "bazos", url, "prenajom")
        self.set_kv("rent_pack_v2", "1")

    def searches(self) -> list[dict]:
        with self.conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM searches ORDER BY deal='predaj', source, id")]  # оренда зверху

    def add_search(self, name: str, source: str, url: str, deal: str) -> int:
        with self.conn() as c:
            return c.execute("INSERT INTO searches(name,source,url,deal) VALUES(?,?,?,?)",
                             (name, source, url, deal)).lastrowid

    def update_search(self, sid: int, **fields) -> None:
        allowed = {k: v for k, v in fields.items()
                   if k in ("name", "url", "deal", "enabled", "last_check", "last_links") and v is not None}
        if not allowed:
            return
        with self.conn() as c:
            c.execute(f"UPDATE searches SET {', '.join(f'{k}=?' for k in allowed)} WHERE id=?",
                      [*allowed.values(), sid])

    def delete_search(self, sid: int) -> None:
        with self.conn() as c:
            c.execute("DELETE FROM searches WHERE id=?", (sid,))

    def touch_search(self, url: str, links: int) -> None:
        with self.conn() as c:
            c.execute("UPDATE searches SET last_check=?, last_links=? WHERE url=?", (now(), links, url))

    # ---- seen ----
    def unseen(self, urls: list[str]) -> list[str]:
        if not urls:
            return []
        with self.conn() as c:
            q = ",".join("?" * len(urls))
            seen = {r["url"] for r in c.execute(f"SELECT url FROM seen_urls WHERE url IN ({q})", urls)}
        return [u for u in urls if u not in seen]

    def mark_seen(self, url: str) -> None:
        with self.conn() as c:
            c.execute("INSERT OR IGNORE INTO seen_urls(url, first_seen) VALUES(?,?)", (url, now()))

    # ---- phones ----
    def phone_usage(self, phones: list[str], exclude_key: str | None = None) -> int:
        """Скільки ІНШИХ оголошень мають хоч один з цих телефонів."""
        if not phones:
            return 0
        with self.conn() as c:
            q = ",".join("?" * len(phones))
            sql = f"""SELECT COUNT(DISTINCT lp.listing_id) n FROM listing_phones lp
                      JOIN listings l ON l.id = lp.listing_id
                      WHERE lp.phone IN ({q})"""
            args: list = list(phones)
            if exclude_key:
                sql += " AND l.key != ?"
                args.append(exclude_key)
            return c.execute(sql, args).fetchone()["n"]

    def blacklisted(self, phones: list[str]) -> bool:
        if not phones:
            return False
        with self.conn() as c:
            q = ",".join("?" * len(phones))
            return c.execute(f"SELECT 1 FROM phone_blacklist WHERE phone IN ({q})", phones).fetchone() is not None

    def blacklist_listing_phones(self, listing_id: int, reason: str = "marked agent") -> None:
        with self.conn() as c:
            for r in c.execute("SELECT phone FROM listing_phones WHERE listing_id=?", (listing_id,)).fetchall():
                c.execute("INSERT OR IGNORE INTO phone_blacklist VALUES(?,?,?)", (r["phone"], reason, now()))

    # ---- listings ----
    def save(self, l: Listing, search_name: str, deal: str) -> int:
        with self.conn() as c:
            c.execute(
                """INSERT INTO listings(key,source,ext_id,url,search_name,deal,title,price_eur,price_text,
                       location,description,seller_name,phones,seller_listing_count,score,category,reasons,
                       wants_no_rk,first_seen,updated_at,district,prop_type,rooms,area_m2,okres)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(key) DO NOTHING""",
                (l.key, l.source, l.ext_id, l.url, search_name, deal, l.title, l.price_eur, l.price_text,
                 l.location, l.description[:5000], l.seller_name, ",".join(l.phones), l.seller_listing_count,
                 l.score, l.category, json.dumps(l.reasons, ensure_ascii=False), int(l.wants_no_rk), now(), now(),
                 l.district, l.prop_type, l.rooms, l.area_m2, l.okres),
            )
            row = c.execute("SELECT id FROM listings WHERE key=?", (l.key,)).fetchone()
            lid = row["id"]
            for p in l.match_keys:
                c.execute("INSERT OR IGNORE INTO listing_phones VALUES(?,?)", (lid, p))
            return lid

    def exists(self, key: str) -> bool:
        with self.conn() as c:
            return c.execute("SELECT 1 FROM listings WHERE key=?", (key,)).fetchone() is not None

    def mark_notified(self, lid: int, msg_id: int | None) -> None:
        with self.conn() as c:
            c.execute("UPDATE listings SET notified=1, tg_message_id=? WHERE id=?", (msg_id, lid))

    def set_status(self, lid: int, status: str | None = None, note: str | None = None) -> dict | None:
        with self.conn() as c:
            if status is not None:
                c.execute("UPDATE listings SET status=?, updated_at=? WHERE id=?", (status, now(), lid))
                if status == "agent":
                    c.execute("UPDATE listings SET category='agency' WHERE id=?", (lid,))
            if note is not None:
                c.execute("UPDATE listings SET note=?, updated_at=? WHERE id=?", (note, now(), lid))
            r = c.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
        if status == "agent":
            self.blacklist_listing_phones(lid)
        return dict(r) if r else None

    def delete_listing(self, lid: int) -> dict | None:
        """Видаляє лід. URL лишається в seen_urls — тож скрейпер його більше не підтягне."""
        with self.conn() as c:
            r = c.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
            if not r:
                return None
            c.execute("INSERT OR IGNORE INTO seen_urls(url, first_seen) VALUES(?,?)", (r["url"], now()))
            # і по id оголошення — якщо сайт змінить slug в URL, воно однаково не повернеться
            c.execute("INSERT OR IGNORE INTO seen_urls(url, first_seen) VALUES(?,?)", (f"deleted:{r['key']}", now()))
            c.execute("DELETE FROM listing_phones WHERE listing_id=?", (lid,))
            c.execute("DELETE FROM listings WHERE id=?", (lid,))
            return dict(r)

    def set_ai(self, lid: int, ai: dict) -> None:
        with self.conn() as c:
            c.execute("UPDATE listings SET ai=? WHERE id=?", (json.dumps(ai, ensure_ascii=False), lid))

    def get(self, lid: int) -> dict | None:
        with self.conn() as c:
            r = c.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
            return dict(r) if r else None

    def leads(self, category: str | None = None, status: str | None = None, deal: str | None = None,
              q: str | None = None, limit: int = 300, districts: list[str] | None = None,
              okresy: list[str] | None = None, sort: str = "new") -> list[dict]:
        sql = "SELECT * FROM listings WHERE 1=1"
        args: list = []
        if okresy:
            named = [o for o in okresy if o != "?"]
            parts = []
            if named:
                parts.append(f"okres IN ({','.join('?' * len(named))})")
                args += named
            if "?" in okresy:
                parts.append("okres IS NULL")
            sql += " AND (" + " OR ".join(parts) + ")"
        if districts:
            named = [d for d in districts if d != "?"]
            parts = []
            if named:
                parts.append(f"district IN ({','.join('?' * len(named))})")
                args += named
            if "?" in districts:
                parts.append("district IS NULL")
            sql += " AND (" + " OR ".join(parts) + ")"
        if category:
            cats = category.split(",")
            sql += f" AND category IN ({','.join('?' * len(cats))})"
            args += cats
        if status:
            sql += " AND status=?"
            args.append(status)
        if deal:
            sql += " AND deal=?"
            args.append(deal)
        if q:
            sql += " AND (title LIKE ? OR location LIKE ? OR description LIKE ? OR phones LIKE ? OR note LIKE ?)"
            args += [f"%{q}%"] * 5
        sql += f" ORDER BY {SORTS.get(sort, SORTS['new'])} LIMIT ?"
        args.append(limit)
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, args)]

    def district_counts(self, category: str = "private,suspicious", deal: str = "", col: str = "district") -> dict:
        assert col in ("district", "okres")
        cats = category.split(",")
        sql = f"SELECT {col} AS district, COUNT(*) n FROM listings WHERE category IN ({','.join('?' * len(cats))})"
        args = list(cats)
        if deal:
            sql += " AND deal=?"
            args.append(deal)
        with self.conn() as c:
            return {(r["district"] or "?"): r["n"] for r in c.execute(sql + f" GROUP BY {col}", args)}

    def stats(self) -> dict:
        with self.conn() as c:
            by_cat = {r["category"]: r["n"] for r in c.execute("SELECT category, COUNT(*) n FROM listings GROUP BY category")}
            by_status = {r["status"]: r["n"] for r in c.execute(
                "SELECT status, COUNT(*) n FROM listings WHERE category!='agency' GROUP BY status")}
            today = c.execute(
                "SELECT COUNT(*) n FROM listings WHERE category!='agency' AND first_seen >= date('now')").fetchone()["n"]
        return {"by_category": by_cat, "by_status": by_status, "leads_today": today,
                "last_run": self.get_kv("last_run"), "last_run_summary": self.get_kv("last_run_summary")}


def mark_seekers(db: DB) -> int:
    """Одноразово: оголошення «шукаю житло», що вже лежать у базі, — в окрему категорію."""
    from .classify import is_seeker
    if db.get_kv("seeker_v1") == "1":
        return 0
    n = 0
    with db.conn() as c:
        for r in c.execute("SELECT id, title, description, reasons FROM listings WHERE status != 'agent'").fetchall():
            if is_seeker(r["title"] or "", r["description"] or ""):
                reasons = ["🔍 шукає житло, а не пропонує"] + json.loads(r["reasons"] or "[]")
                c.execute("UPDATE listings SET category='seeker', reasons=? WHERE id=?",
                          (json.dumps(reasons, ensure_ascii=False), r["id"]))
                n += 1
    db.set_kv("seeker_v1", "1")
    return n


def reclassify_all(db: DB, phone_reuse_limit: int = 3, private_max: int = 1) -> int:
    """Одноразово після зміни правил: перерахувати збережені ліди (крім позначених тобою вручну)."""
    from .classify import classify
    if db.get_kv("reclass_v2") == "1":
        return 0
    with db.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM listings WHERE status != 'agent'")]
    n = 0
    for r in rows:
        l = Listing(source=r["source"], ext_id=r["ext_id"], url=r["url"], title=r["title"] or "",
                    description=r["description"] or "", seller_name=r["seller_name"] or "",
                    phones=[p for p in (r["phones"] or "").split(",") if p],
                    seller_listing_count=r["seller_listing_count"])
        with db.conn() as c:
            keys = [x["phone"] for x in c.execute("SELECT phone FROM listing_phones WHERE listing_id=?", (r["id"],))]
        classify(l, db.phone_usage(keys, l.key), db.blacklisted(keys),
                 phone_reuse_limit=phone_reuse_limit, private_max=private_max)
        with db.conn() as c:
            c.execute("UPDATE listings SET category=?, score=?, reasons=? WHERE id=?",
                      (l.category, l.score, json.dumps(l.reasons, ensure_ascii=False), r["id"]))
        n += 1
    db.set_kv("reclass_v2", "1")
    return n
