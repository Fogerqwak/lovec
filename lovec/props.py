"""Тип нерухомості, кількість кімнат і площа з тексту оголошення.

Невідоме = None: фільтри такі оголошення НЕ відкидають (краще зайвий лід, ніж втрачений).
"""
from __future__ import annotations

import re

from .classify import fold

TYPES = {"byt": "Квартира", "izba": "Кімната", "dom": "Будинок"}

# «v 4-izbovom byte», «v 3i byte», «v rodinnom dome» — кімната ВСЕРЕДИНІ квартири/будинку
IN_FLAT = r"v (\d[,.]?5? ?-? ?(izb\w*|i)|jedno\w*|dvoj\w*|troj\w*|styr\w*|pat\w*|velk\w*|zdielan\w*|spolocn\w*) ?(byte|bytu|dome)\b"
ROOM_FOR_RENT = re.compile(
    r"\b(prenaj\w* (si )?(jednej |jednu |1 )?izb[uyae]\b|izb[au] (na |do )?prenaj|podnaj\w*|spolubyv\w*|volna izba|volnu izbu|"
    r"izb[au] pre (1|jednu|jedneho|studenta|studentku|par|zenu|muza|slecnu)|izb[au] v |izbu v|lozk\w* (v izbe|pre)|"
    r"miesto v izbe|zdielan\w* izb\w*|" + IN_FLAT + r")")
# заголовок «Izba Ružinov», «Pekná izba, Nivy» — але не «3 izby» (це кімнати квартири)
ROOM_TITLE = re.compile(r"(?<![\d,.\-] )(?<![\d,.\-])\bizb[au]\b(?! ?(a|v)? ?kuchyn)")
HOUSE = re.compile(r"\b(rodinn\w* dom\w*|\bdom\b|domu\b|domy\b|chalup\w*|chat[aku]\b|vil[aku]\b|radov\w* dom\w*|bungalov)")
FLAT = re.compile(r"\b(byt\w*|garsonk\w*|garzonk\w*|garsoniera|apartman\w*|mezonet\w*|penthouse|loft|\d[,.]?5? ?-? ?izb\w*|\d ?kk\b)")
GARSONKA = re.compile(r"\b(garsonk\w*|garzonk\w*|garsoniera|1 ?kk\b|studio\b)")
ROOMS = re.compile(r"\b([1-6])(?:[,.](5))? ?-? ?(?:izb\w*|izie\w*|i\b|kk\b|izbak)")
ROOMS_WORD = {"jedno": 1, "dvoj": 2, "troj": 3, "styri": 4, "styr": 4, "pat": 5}
ROOMS_W = re.compile(r"\b(jedno|dvoj|troj|styr|pat)izb\w*")
AREA = re.compile(r"(\d{2,3}(?:[,.]\d{1,2})?)\s?(?:m2|m²|m\s?2\b|m\^2|metrov stvorcov\w*)")
AREA_LABEL = re.compile(r"(?:plocha|vymera|rozloha|uzitkov\w*)[^0-9]{0,25}(\d{2,3}(?:[,.]\d{1,2})?)")


def detect_type(title: str, desc: str, url: str = "") -> str | None:
    t, d, u = fold(title), fold(desc)[:800], (url or "").lower()
    if "/podnajom/" in u or ROOM_FOR_RENT.search(t) or ROOM_TITLE.search(t):
        return "izba"
    # заголовок про квартиру («4-izbový byt, Ružinov»), а в тексті одразу «prenajmem izbu v byte»
    if ROOM_FOR_RENT.search(d[:300]) and not re.search(r"\b(cely|celkovy|cely byt|kompletn\w* byt)\b", t + " " + d[:300]):
        return "izba"
    if "/dom/" in u or "/domy/" in u or re.search(r"/(rodinny-dom|dom)-", u):
        return "dom"
    if "/byt/" in u or "/byty/" in u:
        # у категорії «byty» буває «prenájom izby» — заголовок має пріоритет
        return "byt"
    if FLAT.search(t):
        return "byt"
    if HOUSE.search(t):
        return "dom"
    if ROOM_FOR_RENT.search(d):
        return "izba"
    if FLAT.search(d):
        return "byt"
    if HOUSE.search(d):
        return "dom"
    return None


def detect_rooms(title: str, desc: str) -> float | None:
    """Гарсоньєра = 1. «1,5-izbový» = 1.5. Для будинку теж рахуємо, якщо вказано."""
    for txt in (fold(title), fold(desc)[:1500]):
        if GARSONKA.search(txt):
            return 1.0
        m = ROOMS.search(txt)
        if m:
            return float(m.group(1)) + (0.5 if m.group(2) else 0)
        m = ROOMS_W.search(txt)
        if m:
            return float(ROOMS_WORD[m.group(1)])
    return None


def detect_area(title: str, desc: str, prop_type: str | None = None) -> float | None:
    lo, hi = (8, 120) if prop_type == "izba" else (15, 600)
    for txt in (fold(title), fold(desc)[:3000]):
        for rx in (AREA_LABEL, AREA):
            for m in rx.finditer(txt):
                v = float(m.group(1).replace(",", "."))
                if lo <= v <= hi:
                    return v
    return None


def enrich(l) -> None:
    l.prop_type = detect_type(l.title, l.description, l.url)
    l.rooms = detect_rooms(l.title, l.description) if l.prop_type != "izba" else None
    l.area_m2 = detect_area(l.title, l.description, l.prop_type)


def passes(row: dict, f: dict) -> bool:
    """Фільтр {types, rooms, area_min, area_max, price_min, price_max}. Невідоме проходить."""
    if not f:
        return True
    if f.get("types") and row.get("prop_type") and row["prop_type"] not in f["types"]:
        return False
    # кімната в 4-кімнатній квартирі ≠ 4-кімнатна квартира
    if f.get("rooms") and row.get("prop_type") == "izba" and "izba" not in (f.get("types") or []):
        return False
    if f.get("rooms") and row.get("rooms") is not None and row.get("prop_type") != "izba":
        r = row["rooms"]
        ok = any((x == "4+" and r >= 4) or (x != "4+" and int(float(x)) == int(r)) for x in f["rooms"])
        if not ok:
            return False
    a = row.get("area_m2")
    if a is not None:
        if f.get("area_min") and a < float(f["area_min"]):
            return False
        if f.get("area_max") and a > float(f["area_max"]):
            return False
    p = row.get("price_eur")
    # ціна «від–до» — лише для оренди (місячна)
    if p is not None and row.get("deal") == "prenajom":
        if f.get("price_min") and p < float(f["price_min"]):
            return False
        if f.get("price_max") and p > float(f["price_max"]):
            return False
    return True
