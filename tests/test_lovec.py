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


def test_frank_zicher_without_name_is_yellow_not_hidden():
    # ім'я фірми лише в тексті, без «RK ponúka» — не 100%, тож 🟡, але не ховаємо
    l = classify(L("Predávame 3-izbový byt pri Hviezdoslavovom námestí",
                   "Ponúkame na predaj 3-izbový byt. Vďaka vzdušnému interiéru... Viac info v RK. "
                   "© Text a fotografie sú autorským dielom. FRANK ZICHER s.r.o."))
    assert l.category == "suspicious", l.reasons


def test_private_owner_no_rk():
    l = classify(L("Predám 2-izbový byt Petržalka",
                   "Predám náš byt po rekonštrukcii, sťahujeme sa. Priamo od majiteľa, RK prosím nevolať. 0905 123 456"))
    assert l.category == "private", l.reasons
    assert l.wants_no_rk


def test_private_rent_no_commission():
    l = classify(L("GARSONKA V NOVOSTAVBE- NETREBA PLATIT PROVIZIU PRE REALITKU",
                   "PRENAJMEM KOMPLET ZARIADENÚ GARSÓNKU V NOVOSTAVBE NA SLNEČNICIACH."))
    assert l.category == "private", l.reasons


def test_developer_warning_to_agencies_not_counted_as_agency():
    l = classify(L("5-izbový dom, dvojgaráž, 814 m² – Dunajská Lužná",
                   "REALITNÉ KANCELÁRIE PROSÍM O KONTAKTOVANIE VÝLUČNE V PRÍPADE, ŽE MAJÚ KONKRÉTNEHO KUPUJÚCEHO. "
                   "Na Azalkovej ulici ponúkame samostatne stojacu novostavbu."))
    assert l.wants_no_rk
    assert l.category in ("private", "suspicious"), l.reasons


def test_fake_private_flag_but_agency_text():
    l = classify(L("Na predaj 2i byt", "RK ponúka na predaj byt, provízia v cene.", site_private_flag=True))
    assert l.category != "private", l.reasons


def test_phone_reuse_is_yellow_not_hidden():
    # власник може здавати 3 квартири — це лід, не ріелтор
    l = classify(L("Predám byt", "Predám byt v Ružinove."), phone_reuse=3)
    assert l.category == "suspicious"


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
    assert l.extra["seller_key"] == "bz:1"
    from lovec.districts import detect
    assert l.location == "Bratislava 821 09"
    assert detect(l.location, l.title, l.description) == "Ružinov"
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



# ---------- нова логіка: ховаємо лише 100% ----------

def test_hard_seller_names():
    from lovec.classify import is_company_name
    for n in ["Realitná kancelária REALADR s.r.o.", "Direct Real, spol. s r.o.", "Mgr. Ján Badinka / Maklér",
              "HOME PROPERTY", "XY Reality", "Byty Invest a.s."]:
        assert is_company_name(n), n
    for n in ["Andrej", "Tomas Samuhel", "Nela Antalcová", "PhDr. Viktor Vlasák MBA", "Rafael", "Jana Realová"[:4]]:
        assert not is_company_name(n), n


def test_seller_name_hides():
    l = classify(L("Prenájom 2i bytu", "Prenajmem byt.", seller_name="Realitná kancelária REALADR s.r.o."))
    assert l.category == "agency" and l.reasons[0].startswith("🔴")


def test_brand_in_title_hides():
    l = classify(L("DIRECTREAL|Zariadený 1,5 izbový byt s balkónom", "Ponúkame na prenájom byt."))
    assert l.category == "agency", l.reasons


def test_rk_ponuka_hides():
    l = classify(L("Na prenájom 2i", "Realitná kancelária HERRYS ponúka na prenájom byt."))
    assert l.category == "agency"


def test_bez_provizie_agency_not_private():
    # агенти пишуть «BEZ PROVÍZIE» — це не має робити оголошення приватним
    l = classify(L("BEZ PROVÍZIE︱2-izbový apartmán | OLIVIA Residence",
                   "Ponúkame Vám na prenájom apartmán. Viac informácií poskytne realitná kancelária. Rezervačný poplatok 300 €."))
    assert l.category == "suspicious", l.reasons
    assert not l.wants_no_rk


def test_fake_bez_rk_with_agency_signs():
    l = classify(L("Prenájom bytu, RK nevolať", "Byt v zastúpení majiteľa. Rezervačná zmluva, provízia 1 nájom."))
    assert l.category == "suspicious", l.reasons
    assert any("не віримо" in r for r in l.reasons)


def test_private_rent_bez_provizie_still_green():
    l = classify(L("Prenajmem 2i byt Petržalka – bez provízie", "Prenajmem svoj byt, som majiteľ. Volajte."))
    assert l.category == "private", l.reasons


def test_nehnutelnosti_agency_link():
    html = """<html><body><h1>Zariadený 1,5 izbový byt</h1><div>730 €/mes.</div>
    <a href="/realitny-makler/mgr-michaela-mosna-d0de/inzeraty/b3f">Mgr. Michaela Mosná</a>
    <a href="/realitna-kancelaria/direct-real-spol-s-ro/centrala/o-firme/92b">Direct Real, spol. s r.o.</a>
    <p>Ponúkame na prenájom zariadený byt na Račianskej ulici, Bratislava-Nové Mesto. Pekný byt s balkónom a parkovaním.</p>
    </body></html>"""
    l = SOURCES["nehnutelnosti"].parse_detail(html, "x", "https://www.nehnutelnosti.sk/detail/x/y")
    assert l.site_private_flag is False
    assert "Michaela" in l.seller_name
    assert l.price_eur == 730
    assert l.location == "Bratislava-Nové Mesto"
    assert classify(l).category == "agency"


def test_rentals_first_and_rent_pack(tmp_path):
    db = DB(str(tmp_path / "r.db"))
    db.seed_searches([Search("Bazoš byty predaj", "bazos", "https://reality.bazos.sk/predam/byt/", "predaj")])
    db.set_kv("rent_pack_v1", "")  # імітуємо старий сервер
    db.migrate_searches()
    rows = db.searches()
    assert rows[0]["deal"] == "prenajom"
    assert sum(r["deal"] == "prenajom" for r in rows) == 3
    db.migrate_searches()
    assert len(db.searches()) == len(rows)  # ідемпотентно


# ---------- AI-підсумок ----------

def test_ai_summary_endpoint(tmp_path, monkeypatch):
    import httpx as _httpx
    from lovec import ai as ai_mod
    calls = {}

    class R:
        status_code = 200
        def json(self):
            return {"content": [{"type": "text", "text": 'Ось: {"tldr": "Гарсоньєра в Петржалці", '
                     '"seller": {"verdict": "owner", "why": "пише від себе"}, "facts": {"price": "650 €/міс"}, '
                     '"opener_sk": "Dobrý deň, volám kvôli inzerátu."}'}], "usage": {"input_tokens": 900}}

    def fake_post(url, **kw):
        calls["url"], calls["kw"] = url, kw
        return R()
    monkeypatch.setattr(_httpx, "post", fake_post)

    s = Settings(db_path=str(tmp_path / "ai.db"), telegram_token=TOKEN, allowed_users=[42],
                 ai_provider="anthropic", ai_api_key="sk-test", searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    lid = db.save(classify(L("Prenajmem garsónku", "Prenajmem svoju garsónku v Petržalke za 650 €.")), "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    assert c.get("/api/settings", headers=h).json()["ai"] is True
    r = c.post(f"/api/leads/{lid}/ai", headers=h).json()
    assert r["ai"]["seller"]["verdict"] == "owner"
    assert calls["kw"]["headers"]["x-api-key"] == "sk-test"
    assert calls["kw"]["json"]["model"] == "claude-haiku-5-5"
    assert "Prenajmem svoju garsónku" in calls["kw"]["json"]["messages"][0]["content"]
    assert c.get("/api/leads", headers=h).json()[0]["ai"]["tldr"] == "Гарсоньєра в Петржалці"
    from lovec.telegram import render
    assert "🤖 Гарсоньєра в Петржалці · схоже на власника" in render(db.get(lid))


def test_ai_without_key(tmp_path):
    s = Settings(db_path=str(tmp_path / "n.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    lid = DB(s.db_path).save(classify(L("Predám", "Predám byt.")), "s", "predaj")
    r = c.post(f"/api/leads/{lid}/ai", headers={"X-Tg-Init-Data": _init()})
    assert r.status_code == 400 and "GROQ_API_KEY" in r.json()["detail"]



def test_ai_groq_openai_compatible(monkeypatch):
    import httpx as _httpx
    from lovec.ai import summarize
    seen = []

    class R:
        def __init__(self, code, body):
            self.status_code, self._b, self.text = code, body, _json.dumps(body)
        def json(self):
            return self._b

    def fake_post(url, **kw):
        seen.append((url, kw))
        if "response_format" in kw["json"]:
            return R(400, {"error": {"message": "response_format json_object not supported"}})
        return R(200, {"choices": [{"message": {"content": '{"tldr": "ok", "seller": {"verdict": "unclear"}}'}}]})
    monkeypatch.setattr(_httpx, "post", fake_post)
    out = summarize({"title": "x", "description": "y"}, "groq", "gsk_test")
    assert out["tldr"] == "ok" and out["_model"] == "openai/gpt-oss-120b"
    assert seen[0][0] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen[0][1]["headers"]["Authorization"] == "Bearer gsk_test"
    assert len(seen) == 2  # повторив без JSON-режиму


def test_ai_provider_from_env(monkeypatch, tmp_path):
    from lovec.settings import load
    cfg = tmp_path / "c.yaml"; cfg.write_text("searches: []\n")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    s = load(str(cfg))
    assert (s.ai_provider, s.ai_api_key) == ("gemini", "g-key")


# ---------- райони ----------

def test_district_detection():
    from lovec.districts import detect
    assert detect("Račianska, Bratislava-Nové Mesto, okres Bratislava III") == "Nové Mesto"
    assert detect("Bratislava 821 07") == "Vrakuňa"
    assert detect("Bratislava 841 07") == "Devínska Nová Ves"
    assert detect("Bratislava 851 07") == "Petržalka"
    assert detect("", "Prenajmem 2i byt Nivy") == "Ružinov"
    assert detect("Senec 900 42", "5-izbový dom – Dunajská Lužná") == "Mimo BA"
    assert detect("Bratislava", "Byt", "Karlova Ves, krásny výhľad na hrad Devín") == "Karlova Ves"
    assert detect("Bratislava", "Ahoj, prenajmem byt") is None


def test_district_filter_api_and_push(tmp_path, monkeypatch):
    s = Settings(db_path=str(tmp_path / "d.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    for i, (loc, d) in enumerate([("Bratislava-Petržalka", "Petržalka"), ("Bratislava-Ružinov", "Ružinov"), ("", None)]):
        l = classify(Listing("bazos", str(i), f"u{i}", title="Prenajmem byt", description="Prenajmem byt.", location=loc))
        l.district = d
        db.save(l, "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    assert len(c.get("/api/leads?districts=Petržalka", headers=h).json()) == 1
    assert len(c.get("/api/leads?districts=Petržalka,?", headers=h).json()) == 2
    d = c.get("/api/districts", headers=h).json()
    assert d["counts"]["Petržalka"] == 1 and d["counts"]["?"] == 1 and "Čunovo" in d["all"]
    assert c.patch("/api/settings", headers=h, json={"notify_districts": ["Marsa"]}).status_code == 400
    r = c.patch("/api/settings", headers=h, json={"notify_districts": ["Ružinov"], "notify_unknown_district": False}).json()
    assert r["notify_districts"] == ["Ružinov"]

    # пуш лише з вибраного району
    from lovec import http as lhttp, scraper
    from lovec.settings import effective
    det = (BAZOS_DETAIL.replace("Mlynských Nivách", "Slnečniciach").replace("821 09", "851 07")
           .replace("byt, Nivy", "byt, Slnečnice"))
    def fake_get(self, url, retries=2):
        if "/predam/" in url:
            return '<a href="/inzerat/77/a.php">a</a>'
        return det if "/inzerat/77/" in url else None
    monkeypatch.setattr(lhttp.Fetcher, "get", fake_get)
    db.set_kv("first_run_done", "1")
    db.add_search("x", "bazos", "https://reality.bazos.sk/predam/byt/", "predaj")
    sent = []
    eff = effective(s, db); eff.request_delay_seconds = (0, 0)
    scraper.run_once(eff, db, lambda lid, row: sent.append(row))
    assert sent == []                                        # Петржалка, а пушимо лише Ружинов
    assert db.leads(districts=["Petržalka"])[-1]["ext_id"] in ("0", "77")


# ---------- тип / кімнати / площа / ціна ----------

def test_props_detection():
    from lovec.props import detect_area, detect_rooms, detect_type
    assert detect_type("GARSONKA V NOVOSTAVBE", "", "https://reality.bazos.sk/inzerat/1/x.php") == "byt"
    assert detect_rooms("GARSONKA V NOVOSTAVBE", "") == 1
    assert detect_type("Prenajmem izbu v 3i byte, Ružinov", "") == "izba"
    assert detect_type("Rodinný dom Rača na prenájom", "") == "dom"
    assert detect_rooms("1,5-izbový byt Kramáre", "") == 1.5
    assert detect_rooms("Dvojizbový byt na prenájom", "") == 2
    assert detect_rooms("Rodinný dom", "5 izieb, záhrada") == 5
    assert detect_area("Prenájom 2-izbového bytu", "Byt 54,5 m², loggia.") == 54.5
    assert detect_area("Dom", "Úžitková plocha: 180 m2, pozemok 600 m2", "dom") == 180
    assert detect_area("Byt", "500 m od zastávky") is None


def test_props_filter_and_push(tmp_path):
    from lovec.props import passes
    row = {"deal": "prenajom", "prop_type": "byt", "rooms": 2.0, "area_m2": 55, "price_eur": 750}
    assert passes(row, {"types": ["byt"], "rooms": ["2"], "area_min": 40, "price_max": 800})
    assert not passes(row, {"price_max": 700})
    assert not passes(row, {"rooms": ["1", "3"]})
    assert not passes(row, {"types": ["izba"]})
    assert passes({**row, "area_m2": None}, {"area_min": 60})            # невідома площа проходить
    assert passes({**row, "deal": "predaj", "price_eur": 250000}, {"price_max": 800})  # ціна — лише оренда
    assert passes({**row, "rooms": 4.5}, {"rooms": ["4+"]})

    s = Settings(db_path=str(tmp_path / "p.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    for i, (t, p) in enumerate([("Prenajmem 2-izbový byt, 55 m2", 750), ("Prenajmem 3-izbový byt 80 m2", 1100),
                                ("Prenajmem izbu pre študenta", 350)]):
        l = classify(Listing("bazos", str(i), f"u{i}", title=t, description=t, price_eur=p))
        from lovec import props
        props.enrich(l)
        db.save(l, "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    got = c.get("/api/leads?deal=prenajom&types=byt&price_max=900", headers=h).json()
    assert [r["ext_id"] for r in got] == ["0"]
    assert len(c.get("/api/leads?types=izba", headers=h).json()) == 1
    assert c.patch("/api/settings", headers=h, json={"notify_filter": {"hack": 1}}).status_code == 400
    assert c.patch("/api/settings", headers=h, json={"notify_filter": {"types": ["byt"], "price_max": 900}}).json()["notify_filter"]["price_max"] == 900
    from lovec.telegram import render
    assert "2-кімн. 55 м²" in render(db.get(1))


def test_delete_lead(tmp_path):
    s = Settings(db_path=str(tmp_path / "del.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    lid = db.save(classify(Listing("bazos", "9", "https://reality.bazos.sk/inzerat/9/x.php", title="Prenajmem byt",
                                   description="Prenajmem byt.", phones=["421900000009"])), "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    assert c.delete(f"/api/leads/{lid}", headers=h).json()["ok"]
    assert db.get(lid) is None
    assert db.unseen(["https://reality.bazos.sk/inzerat/9/x.php"]) == []   # більше не підтягнеться
    assert db.phone_usage(["421900000009"]) == 0
    assert c.delete(f"/api/leads/{lid}", headers=h).status_code == 404
    from lovec.telegram import keyboard
    assert any(b.get("callback_data") == f"x:ask:{lid}" for row in keyboard(lid)["inline_keyboard"] for b in row)


def test_okres_filter(tmp_path):
    from lovec.districts import detect_okres
    assert detect_okres("Ružinov") == "Bratislava II"
    assert detect_okres("Devínska Nová Ves") == "Bratislava IV"
    assert detect_okres(None, "Račianska, okres Bratislava III") == "Bratislava III"
    assert detect_okres(None, "Bratislava 851 01") == "Bratislava V"
    assert detect_okres("Mimo BA", "Senec") == "Mimo BA"
    assert detect_okres(None, "Bratislava") is None

    s = Settings(db_path=str(tmp_path / "o.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    for i, (d, o) in enumerate([("Ružinov", "Bratislava II"), ("Vrakuňa", "Bratislava II"), ("Petržalka", "Bratislava V"), (None, None)]):
        l = classify(Listing("bazos", str(i), f"u{i}", title="Prenajmem byt", description="Prenajmem byt."))
        l.district, l.okres = d, o
        db.save(l, "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    got = c.get("/api/leads?okresy=Bratislava II", headers=h).json()
    assert sorted(r["district"] for r in got) == ["Ružinov", "Vrakuňa"]     # шильдик району лишається
    assert len(c.get("/api/leads?okresy=Bratislava V,?", headers=h).json()) == 2
    o = c.get("/api/okresy?deal=prenajom", headers=h).json()
    assert o["counts"]["Bratislava II"] == 2 and o["all"][0]["districts"] == ["Staré Mesto"]


# ---------- «шукаю житло» ----------

def test_seeker_detection():
    from lovec.classify import is_seeker
    yes = [("Hľadám 2-izbový byt na prenájom v Ružinove", ""),
           ("HĽADÁME BYT NA DLHODOBÝ PRENÁJOM", ""),
           ("Kúpim garsónku v Bratislave", ""),
           ("Mladý pár hľadá podnájom", ""),
           ("Študentka hľadá izbu od septembra", ""),
           ("Dopyt - 3i byt Petržalka", ""),
           ("2i byt Bratislava", "Dobrý deň, hľadáme 2-izbový byt na dlhodobý prenájom, sme slušná rodina.")]
    no = [("Prenajmem 2-izbový byt, Ružinov", "Hľadám slušného nájomcu, ideálne pár."),
          ("Predám byt", "Hľadáte bývanie blízko centra? Ponúkam na predaj byt."),
          ("Garsónka v novostavbe", "Prenajmem garsónku. Hľadám nájomníka od 1.11."),
          ("Byt na prenájom, Nivy", "Ponúkam na prenájom byt. Pre rýchle jednanie hľadáme nájomcu.")]
    for t, d in yes:
        assert is_seeker(t, d), t
    for t, d in no:
        assert not is_seeker(t, d), t


def test_seeker_not_pushed_and_tab(tmp_path):
    l = classify(L("Hľadám byt na prenájom", "Hľadám 1-izbový byt, slušný, pracujúci."))
    assert l.category == "seeker"
    s = Settings(db_path=str(tmp_path / "sk.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    db.save(l, "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    assert c.get("/api/leads", headers=h).json() == []                       # не серед лідів
    assert len(c.get("/api/leads?category=seeker", headers=h).json()) == 1   # але є в «Шукають»


def test_room_in_big_flat_not_in_4plus(tmp_path):
    from lovec.props import detect_type, passes
    assert detect_type("Prenájom izby v 4-izbovom byte, Ružinov", "") == "izba"
    assert detect_type("4-izbový byt, Ružinov", "Prenajmem izbu v 4-izbovom byte pre študentku.") == "izba"
    assert detect_type("Prenajmem 4-izbový byt Ružinov", "Byt má 4 izby, kuchyňu.") == "byt"
    assert detect_type("3 izby + kuchyňa, Dúbravka", "") == "byt"
    room = {"deal": "prenajom", "prop_type": "izba", "rooms": None, "price_eur": 380}
    assert not passes(room, {"rooms": ["4+"]})                        # не в розділі «4+ квартири»
    assert passes(room, {"rooms": ["4+"], "types": ["izba", "byt"]})  # але якщо кімнати вибрані явно — так


def test_sort_by_price(tmp_path):
    s = Settings(db_path=str(tmp_path / "s.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    for i, p in enumerate([700, None, 450, 1200]):
        db.save(classify(Listing("bazos", str(i), f"u{i}", title="Prenajmem byt", description="Prenajmem byt.",
                                 price_eur=p, area_m2=None)), "s", "prenajom")
    h = {"X-Tg-Init-Data": _init()}
    prices = lambda q: [r["price_eur"] for r in c.get(f"/api/leads?{q}", headers=h).json()]
    assert prices("sort=price_asc") == [450, 700, 1200, None]
    assert prices("sort=price_desc") == [1200, 700, 450, None]
    assert prices("sort=bogus")[0] in (450, 700, 1200, None)        # невідоме — не падає


def test_lead_code_search(tmp_path):
    from lovec.models import lead_code, parse_code
    assert lead_code(42) == "L-0042"
    for s in ("L-0042", "l42", "#42", "L 42", " L-42 ", "/l 42"):
        assert parse_code(s) == 42, s
    assert parse_code("Ružinov 42") is None and parse_code("") is None
    s = Settings(db_path=str(tmp_path / "c.db"), telegram_token=TOKEN, allowed_users=[42], searches=[])
    c = TestClient(create_app(s, background=False))
    db = DB(s.db_path)
    lid = db.save(classify(Listing("bazos", "1", "u1", title="Prenajmem byt", description="Prenajmem byt.")), "s", "predaj")
    db.set_status(lid, "called")
    h = {"X-Tg-Init-Data": _init()}
    # знаходить навіть з вкладки «Нові» і з фільтром «Оренда»
    got = c.get(f"/api/leads?status=new&deal=prenajom&q=L-{lid:04d}", headers=h).json()
    assert [r["id"] for r in got] == [lid]
    from lovec.telegram import render
    assert f"<code>L-{lid:04d}</code>" in render(db.get(lid))


# ---------- догрузка старих ----------

def test_backfill_collect_and_process(tmp_path, monkeypatch):
    from datetime import date, timedelta
    from lovec import backfill, http as lhttp
    today = date.today()
    d = lambda n: (today - timedelta(days=n))
    fmt = lambda x: f"[{x.day}.{x.month}. {x.year}]"
    pages = {  # Bazoš: 3 сторінки, третя вся старша за 14 днів
        "": [(101, d(0)), (102, d(2))],
        "20/": [(103, d(5)), (104, d(9))],
        "40/": [(105, d(20)), (106, d(25))],
        "60/": [(107, d(30))],
    }
    detail = {101: d(0), 102: d(2), 103: d(5), 104: d(9), 105: d(20), 106: d(25)}
    hits = []

    def fake_get(self, url, retries=2):
        hits.append(url)
        if "/inzerat/" in url:
            i = int(url.split("/inzerat/")[1].split("/")[0])
            return (f"<html><h1 class='nadpisdetail'>Prenajmem 2-izbový byt {i}</h1><div class='popisdetail'>"
                    f"Prenajmem svoj byt v Petržalke, 55 m2. {fmt(detail[i])}</div>"
                    f"<p>{fmt(detail[i])}</p></html>")
        for suffix, items in pages.items():
            if url.startswith("https://reality.bazos.sk/prenajmu/byt/" + suffix + "?"):
                return "".join(f'<a href="/inzerat/{i}/x.php">x</a> {fmt(dt)}' for i, dt in items)
        return None
    monkeypatch.setattr(lhttp.Fetcher, "get", fake_get)

    s = Settings(db_path=str(tmp_path / "b.db"), request_delay_seconds=(0, 0),
                 searches=[Search("Bazoš byty", "bazos", "https://reality.bazos.sk/prenajmu/byt/?hlokalita=81101", "prenajom")])
    db = DB(s.db_path)
    # 101 вже є в базі (прийшов як свіжий) — повторно не додаємо
    db.save(classify(Listing("bazos", "101", "https://reality.bazos.sk/inzerat/101/x.php", title="t", description="d")), "s", "prenajom")
    r = backfill.collect(s, db, days=14)
    assert r["pages"] == 3                                          # на 3-й сторінці все старе — далі не гортає
    assert not any("/60/" in h for h in hits)
    assert db.queue_size() == 5                                     # 102..106, без 101
    sent = []
    while db.queue_size():
        backfill.process_chunk(s, db, 2)
    rows = db.leads(backlog=1)
    assert sorted(r["ext_id"] for r in rows) == ["102", "103", "104"]  # 105/106 старші 14 днів — відкинуті
    assert all(r["backlog"] == 1 and r["posted_at"] for r in rows)
    assert [r["ext_id"] for r in db.leads(backlog=0)] == ["101"]
    assert sent == []                                               # без пушів


# ---------- портали «від власника» ----------

def test_owner_portals_links_and_parse():
    br = SOURCES["bezrealitky"]
    html = '''<a href="/nehnutelnosti-byty-domy/912345-ponuka-prenajom-bytu-ruzinov">a</a>
              <a href="https://www.bezrealitky.sk/nehnutelnosti-byty-domy/912345-ponuka-prenajom-bytu-ruzinov?x=1">dup</a>
              <a href="/vypis/ponuka-prenajom/byt/bratislava">cat</a>'''
    assert [i for i, _ in br.extract_links(html, "https://www.bezrealitky.sk/vypis/x")] == ["912345"]
    detail = '''<html><head><meta property="og:title" content="Prenájom 2-izbového bytu 54 m²">
      <script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"advert":{"id":"912345",
      "price":780,"surface":54,"disposition":"DISP_2_1","address":"Bajkalská, Bratislava - Ružinov"}}}}</script></head>
      <body><h1>Prenájom 2-izbového bytu 54 m²</h1><p>Prenajmem svoj zrekonštruovaný byt blízko Trnavského mýta, voľný od 1.12.</p></body></html>'''
    l = br.parse_detail(detail, "912345", "https://www.bezrealitky.sk/nehnutelnosti-byty-domy/912345-x")
    assert l.price_eur == 780 and l.site_private_flag is True
    assert "Ružinov" in l.location
    from lovec.districts import detect
    assert detect(l.location, l.title, l.description) == "Ružinov"
    assert classify(l).category == "private"

    rf = SOURCES["realfree"]
    assert [i for i, _ in rf.extract_links('<a href="/inzerat/prenajom-3-izboveho-bytu-na-ulici-jana-holleho-v-trencine">x</a>',
                                            "https://realfree.sk/prenajom-bytu")] == ["prenajom-3-izboveho-bytu-na-ulici-jana-holleho-v-trencine"]
    bm = SOURCES["bezmaklerov"]
    assert [i for i, _ in bm.extract_links('<a href="/sk/inzerat/cg3K7idGqc6QVpqELegg7r">x</a>',
                                            "https://www.bezmaklerov.sk/sk/nehnutelnosti")] == ["cg3K7idGqc6QVpqELegg7r"]
    assert source_from_url("https://www.bezrealitky.sk/vypis/ponuka-prenajom/byt/bratislava") == "bezrealitky"
    assert source_from_url("https://realfree.sk/prenajom-bytu-bratislava") == "realfree"
    assert source_from_url("https://www.bezmaklerov.sk/sk/nehnutelnosti") == "bezmaklerov"
    # агентство на «безріелторському» порталі однаково ховається
    l2 = classify(Listing("realfree", "x", "u", title="Prenájom bytu", description="Realitná kancelária XY ponúka na prenájom byt."))
    assert l2.category == "agency"
