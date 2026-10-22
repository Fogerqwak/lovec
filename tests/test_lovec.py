from lovec.classify import classify
from lovec.db import DB
from lovec.models import Listing
from lovec.sources.base import normalize_phones, parse_price
from lovec.sources.sites import SOURCES, Bazos


def L(title, desc, **kw):
    return Listing(source="bazos", ext_id="1", url="u", title=title, description=desc, **kw)


# ---------- класифікатор (тексти з реальних оголошень, скорочені) ----------

def test_agency_herrys():
    l = classify(L("HERRYS - Na predaj 3i byt v pôvodnom stave, v blízkosti centra",
                   "Realitná kancelária HERRYS ponúka exkluzívne na predaj priestranný byt. "
                   "CENA 269.900 € vrátane odborného servisu a provízie RK. "
                   "© Text a fotografie sú autorským dielom a majetkom realitnej kancelárie HERRYS. HERRYS s.r.o."))
    assert l.category == "agency", l.reasons


def test_agency_frank_zicher_without_word_rk():
    l = classify(L("Predávame 3-izbový byt pri Hviezdoslavovom námestí",
                   "Ponúkame na predaj 3-izbový byt. Vďaka vzdušnému interiéru... Viac info v RK. "
                   "© Text a fotografie sú autorským dielom. FRANK ZICHER s.r.o."))
    assert l.category == "agency", l.reasons


def test_private_owner_no_rk():
    l = classify(L("Predám 2-izbový byt Petržalka",
                   "Predám náš byt po rekonštrukcii, sťahujeme sa. Priamo od majiteľa, RK prosím nevolať. 0905 123 456"))
    assert l.category == "private", l.reasons
    assert l.wants_no_rk


def test_private_rent_no_commission():
    l = classify(L("GARSONKA V NOVOSTAVBE- NETREBA PLATIT PROVIZIU PRE REALITKU",
                   "PRENAJMEM KOMPLET ZARIADENÚ GARSÓNKU V NOVOSTAVBE NA SLNEČNICIACH."))
    assert l.category == "private", l.reasons
    assert l.wants_no_rk


def test_developer_warning_to_agencies_not_counted_as_agency():
    l = classify(L("5-izbový dom, dvojgaráž, 814 m² – Dunajská Lužná",
                   "REALITNÉ KANCELÁRIE PROSÍM O KONTAKTOVANIE VÝLUČNE V PRÍPADE, ŽE MAJÚ KONKRÉTNEHO KUPUJÚCEHO. "
                   "Na Azalkovej ulici ponúkame samostatne stojacu novostavbu."))
    assert l.wants_no_rk
    assert l.category in ("private", "suspicious"), l.reasons


def test_fake_private_flag_but_agency_text():
    l = classify(L("Na predaj 2i byt", "RK ponúka na predaj byt, provízia v cene.", site_private_flag=True))
    assert l.category != "private", l.reasons


def test_phone_reuse_makes_agency():
    l = classify(L("Predám byt", "Predám byt v Ružinove."), phone_reuse=3)
    assert l.category == "agency"


def test_blacklist():
    l = classify(L("Predám byt", "Predám byt."), blacklisted=True)
    assert l.category == "agency"


def test_seller_many_listings():
    l = classify(L("Predám byt", "Predám byt.", seller_listing_count=14))
    assert l.category != "private"


# ---------- утиліти ----------

def test_phones():
    assert normalize_phones("tel 0905 123 456, +421 911-222-333, 00421903399155") == \
        ["421905123456", "421911222333", "421903399155"]
    assert normalize_phones("rok 2008, 1234567890") == []


def test_price():
    assert parse_price("Cena: 249 900 €")[0] == 249900
    assert parse_price("1 350 000 €")[0] == 1350000
    assert parse_price("499 €/mes")[0] == 499
    assert parse_price("270.000 €")[0] == 270000


# ---------- посилання зі сторінок-списків ----------

def test_bazos_links():
    html = """<div class="inzeraty"><h2 class="nadpis">
      <a href="/inzerat/196719480/5-izbovy-dom-dvojgaraz-814-m-dunajska-luzna.php">X</a></h2>
      <a href="https://reality.bazos.sk/inzerat/196719480/5-izbovy-dom-dvojgaraz-814-m-dunajska-luzna.php"><img></a>
      <a href="https://reality.bazos.sk/inzerat/196684062/garsonka.php">Y</a>
      <a href="https://reality.bazos.sk/20/">2</a></div>"""
    links = SOURCES["bazos"].extract_links(html, "https://reality.bazos.sk/predam/byt/")
    assert [i for i, _ in links] == ["196719480", "196684062"]


def test_nehnutelnosti_links():
    html = """<a href="https://www.nehnutelnosti.sk/detail/JuY-l_mO9Et/predaj-2-izb-bytu">a</a>
      <a href="/detail/JuY-l_mO9Et/predaj-2-izb-bytu">dup</a>
      <a href="/detail/JunY3ISTVwq/priestranny-2-izbovy-byt">b</a>
      <a href="/vysledky/2-izbove-byty/slovensko/predaj">cat</a>"""
    links = SOURCES["nehnutelnosti"].extract_links(html, "https://www.nehnutelnosti.sk/vysledky/byty/bratislava/predaj")
    assert [i for i, _ in links] == ["JuY-l_mO9Et", "JunY3ISTVwq"]


def test_topreality_links():
    html = '<a href="/predaj-2-izbovy-byt-ruzinov-r9876543.html">a</a><a href="/kontakt.html">k</a>'
    links = SOURCES["topreality"].extract_links(html, "https://www.topreality.sk/vyhladavanie.html")
    assert links == [("9876543", "https://www.topreality.sk/predaj-2-izbovy-byt-ruzinov-r9876543.html")]


# ---------- деталка Bazoš ----------

BAZOS_DETAIL = """<html><head><title>Predám 2i byt | Bazoš</title>
<meta property="og:description" content="Predám byt"></head><body>
<header><a href="tel:0800123456">podpora</a></header>
<h1 class="nadpisdetail">Predám 2-izbový byt, Nivy</h1>
<div class="popisdetail">Predám svoj 2-izbový byt po rekonštrukcii na Mlynských Nivách. Priamo od majiteľa,
bez RK. Volajte 0915 222 333.</div>
<table><tr><td>Meno:</td><td><b>Ján</b> <a href="/hodnotenie.php?idmeno=123&amp;idphone=1">Všetky inzeráty</a></td></tr>
<tr><td>Lokalita:</td><td>Bratislava 821 09</td></tr>
<tr><td>Cena:</td><td><b>219 000 €</b></td></tr></table></body></html>"""


def test_bazos_detail():
    l = SOURCES["bazos"].parse_detail(BAZOS_DETAIL, "1", "https://reality.bazos.sk/inzerat/1/x.php")
    assert l.title == "Predám 2-izbový byt, Nivy"
    assert l.price_eur == 219000
    assert l.location.startswith("Bratislava")
    assert l.phones == ["421915222333"]          # телефон сайту з header не підхопили
    assert l.seller_name == "Ján"
    assert "idmeno=123" in l.extra["user_url"]
    assert classify(l).category == "private"


def test_bazos_user_count():
    assert Bazos.count_user_listings("Zobrazených 1-20 inzerátov z 37") == 37


# ---------- БД ----------

def test_db_phone_usage_and_status(tmp_path):
    db = DB(str(tmp_path / "t.db"))
    a = classify(Listing("bazos", "1", "u1", title="Predám", description="Predám", phones=["421900000001"]))
    b = classify(Listing("bazos", "2", "u2", title="Predám", description="Predám", phones=["421900000001"]))
    ida = db.save(a, "s", "predaj")
    db.save(b, "s", "predaj")
    assert db.phone_usage(["421900000001"], exclude_key="bazos:2") == 1
    db.set_status(ida, "agent")
    assert db.blacklisted(["421900000001"])
    assert db.get(ida)["category"] == "agency"
    assert db.unseen(["u1", "u9"]) == ["u1", "u9"]
    db.mark_seen("u1")
    assert db.unseen(["u1", "u9"]) == ["u9"]


# ---------- Mini App: авторизація і API ----------

import json as _json
import time as _time

from fastapi.testclient import TestClient

from lovec.settings import Search, Settings
from lovec.tgauth import sign_init_data, validate_init_data
from lovec.web import create_app, source_from_url

TOKEN = "123456:TEST-token"


def _init(uid=42, token=TOKEN, age=0):
    return sign_init_data({"auth_date": str(int(_time.time()) - age), "query_id": "AA",
                           "user": _json.dumps({"id": uid, "first_name": "Bohdan"})}, token)


def test_init_data_validation():
    assert validate_init_data(_init(), TOKEN)["id"] == 42
    assert validate_init_data(_init(), "other:token") is None          # чужий бот
    assert validate_init_data(_init(age=8 * 86400), TOKEN) is None     # протух
    assert validate_init_data(_init().replace("Bohdan", "Hacker"), TOKEN) is None  # підмінили дані


def _app(tmp_path):
    s = Settings(db_path=str(tmp_path / "a.db"), telegram_token=TOKEN, allowed_users=[42],
                 dashboard_password="pw",
                 searches=[Search("Bazoš byty", "bazos", "https://reality.bazos.sk/predam/byt/", "predaj")])
    return TestClient(create_app(s, background=False))


def test_api_auth(tmp_path):
    c = _app(tmp_path)
    assert c.get("/").status_code == 200                                 # сторінка публічна, даних у ній нема
    assert c.get("/api/leads").status_code == 401
    assert c.get("/api/leads", headers={"X-Tg-Init-Data": _init()}).status_code == 200
    assert c.get("/api/leads", headers={"X-Tg-Init-Data": _init(uid=7)}).status_code == 403
    assert c.get("/api/leads", auth=("admin", "pw")).status_code == 200
    assert c.get("/api/me", headers={"X-Tg-Init-Data": _init()}).json()["via"] == "telegram"


def test_api_searches_and_settings(tmp_path):
    c = _app(tmp_path)
    h = {"X-Tg-Init-Data": _init()}
    rows = c.get("/api/searches", headers=h).json()
    assert [r["name"] for r in rows] == ["Bazoš byty"]                    # засіялось з конфігу
    sid = c.post("/api/searches", headers=h, json={"url": "https://www.topreality.sk/x.html?a=1", "deal": "prenajom"}).json()["id"]
    assert c.post("/api/searches", headers=h, json={"url": "https://www.idnes.cz/x"}).status_code == 400
    c.patch(f"/api/searches/{sid}", headers=h, json={"enabled": False})
    rows = {r["id"]: r for r in c.get("/api/searches", headers=h).json()}
    assert rows[sid]["source"] == "topreality" and rows[sid]["enabled"] == 0
    c.delete(f"/api/searches/{sid}", headers=h)
    assert len(c.get("/api/searches", headers=h).json()) == 1

    assert c.patch("/api/settings", headers=h, json={"interval_minutes": 1}).status_code == 400
    r = c.patch("/api/settings", headers=h, json={"interval_minutes": 10, "paused": True}).json()
    assert r["interval_minutes"] == 10 and r["paused"] is True
    assert c.patch("/api/settings", headers=h, json={"hack": 1}).status_code == 400


def test_source_from_url():
    assert source_from_url("https://reality.bazos.sk/predam/byt/") == "bazos"
    assert source_from_url("https://www.nehnutelnosti.sk/vysledky/byty") == "nehnutelnosti"
    assert source_from_url("https://evilnehnutelnosti.sk/") is None
