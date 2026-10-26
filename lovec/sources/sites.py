"""Адаптери конкретних сайтів."""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Listing
from .base import Source, clean, parse_price


PSC_RE = re.compile(r"\b(\d{3})\s?(\d{2})\b")


def bazos_location(val: str) -> str:
    """«Mapa | 851 07 Bratislava» -> «Bratislava 851 07» (район рахує lovec.districts)."""
    val = re.sub(r"^\s*Mapa\s*\|?\s*", "", val or "").strip()
    m = PSC_RE.search(val)
    if not m:
        return val[:60]
    city = clean(PSC_RE.sub("", val)).strip(" |,")
    return f"{city} {m.group(1)} {m.group(2)}".strip()


class Bazos(Source):
    """reality.bazos.sk — найбільше приватних оголошень.
    Деталка: https://reality.bazos.sk/inzerat/196719480/slug.php
    Телефон прихований («zobraz číslo»), але в сторінці є idphone/idmeno продавця —
    це стабільний ключ: по ньому видно, що одна людина веде 10 оголошень."""
    name = "bazos"
    detail_re = re.compile(r"https?://reality\.bazos\.sk/inzerat/(?P<id>\d+)/[^/?#]+\.php")
    description_selectors = ["div.popisdetail", "div.popis"]
    title_selectors = ["h1.nadpisdetail", "h1"]

    def enrich(self, soup: BeautifulSoup, l: Listing, page_text: str) -> None:
        # 1) таблиця "Meno: / Lokalita: / Cena:"
        for td in soup.find_all("td"):
            label = clean(td.get_text()).lower().rstrip(":")
            if label not in ("meno", "lokalita", "cena"):
                continue
            nxt = td.find_next_sibling("td")
            if not nxt:
                continue
            val = clean(nxt.get_text(" "))
            if label == "meno" and not l.seller_name:
                l.seller_name = re.split(r"\s+(Všetky|Ohodno)", val)[0][:80]
            elif label == "lokalita" and not l.location:
                l.location = bazos_location(val)
            elif label == "cena":
                p, txt = parse_price(val)
                if p is not None or not l.price_text:
                    l.price_eur, l.price_text = p, (txt or val[:40])

        # 2) фолбек по тексту сторінки — якщо верстка таблиці зміниться
        if not l.seller_name:
            m = re.search(r"Meno:\s*(.+?)\s+(?:Telef[oó]n|Lokalita|Všetky)", page_text)
            if m:
                l.seller_name = m.group(1)[:80]
        if not l.location:
            m = re.search(r"Lokalita:\s*(.{3,80}?)\s+Videlo", page_text)
            if m:
                l.location = bazos_location(m.group(1))
        m = re.search(r"Cena:\s*(.{1,40}?)\s+(?:Užívateľ|Uzivatel|Obľúbené|$)", page_text)
        if m:
            p, txt = parse_price(m.group(1))
            if p is not None:
                l.price_eur, l.price_text = p, txt

        # дата публікації «[22.10. 2026]»
        m = self.DATE_RE.search(page_text)
        if m:
            l.posted_at = f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

        # 3) прихований id продавця і посилання на всі його оголошення (сидять в onclick/href)
        raw = html.unescape(str(soup))
        m = re.search(r"idphone=(\d+)", raw) or re.search(r"idmeno=(\d+)", raw)
        if m:
            l.extra["seller_key"] = f"bz:{m.group(1)}"
        u = re.search(r"((?:https?://[\w.]*bazos\.sk)?/?[\w./-]*hodnotenie\.php\?[^\"' <>]*id(?:meno|phone)=[^\"' <>]+)", raw)
        if u:
            l.extra["user_url"] = urljoin("https://www.bazos.sk/", u.group(1))

    DATE_RE = re.compile(r"\[\s*(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})\s*\]")

    def page_url(self, url: str, page: int) -> str | None:
        """https://reality.bazos.sk/prenajmu/byt/?q → …/prenajmu/byt/20/?q (по 20 на сторінку)."""
        from urllib.parse import urlsplit, urlunsplit
        p = urlsplit(url)
        path = re.sub(r"/\d+/?$", "/", p.path.rstrip("/") + "/")
        return urlunsplit((p.scheme, p.netloc, f"{path}{(page - 1) * 20}/", p.query, ""))

    def next_page(self, html: str, url: str, page_no: int) -> str | None:
        return self.page_url(url, page_no + 1)  # у Bazoš формат стабільний — не гадаємо по посиланнях

    def list_dates(self, html: str) -> list:
        from datetime import date
        out = []
        for d, m, y in self.DATE_RE.findall(html):
            try:
                out.append(date(int(y), int(m), int(d)))
            except ValueError:
                pass
        return out

    @staticmethod
    def count_user_listings(html_text: str) -> int:
        m = re.search(r"Zobrazen\w*\s+\d+\s*-\s*\d+\s+inzer\w+\s+z\s+([\d\s]+)", html_text)
        if m:
            return int(re.sub(r"\s", "", m.group(1)))
        return len(set(re.findall(r"/inzerat/(\d+)/", html_text)))


AGENCY_LINK = re.compile(r"/(realitna-kancelaria|realitne-kancelarie|realitny-makler|realitni-makleri)/", re.I)


class Nehnutelnosti(Source):
    """nehnutelnosti.sk — Next.js. Деталка: /detail/JuY-l_mO9Et/slug
    Агентство видно на 100%: у контактах посилання на /realitna-kancelaria/… або /realitny-makler/…"""
    name = "nehnutelnosti"
    detail_re = re.compile(r"https?://(?:www\.)?nehnutelnosti\.sk/detail/(?P<id>[A-Za-z0-9_\-]{6,})/[^/?#]*")
    title_selectors = ["h1"]

    def enrich(self, soup: BeautifulSoup, l: Listing, page_text: str) -> None:
        names, hrefs = [], set()
        for a in soup.find_all("a", href=True):
            if AGENCY_LINK.search(a["href"]):
                hrefs.add(a["href"].split("?")[0])
                n = clean(a.get_text(" "))
                if 2 < len(n) <= 60 and n not in names and not n.lower().startswith(("zobraz", "všetky", "viac")):
                    names.append(n)
        # Контакт оголошення = 1-2 профілі (маклер + фірма). Якщо їх багато — це блок
        # «схожі оголошення»/реклама, а не продавець, тож не робимо висновків.
        if 1 <= len(hrefs) <= 3:
            l.site_private_flag = False
            l.seller_name = " / ".join(names[:2]) or l.seller_name
        if "sukromna_osoba" in l.page_markers and l.site_private_flag is None:
            l.site_private_flag = True
        if not l.location:
            m = re.search(r"Bratislava\s?-\s?[A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ][\wáäčďéíľĺňóôŕšťúýž]+(?:\s[A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ][\wáäčďéíľĺňóôŕšťúýž]+)?",
                          l.title + " " + page_text)
            m = m or re.search(r"okres [^,]{3,40}", page_text)
            if m:
                l.location = m.group(0)


class RealitySk(Source):
    """reality.sk (та ж група, що й nehnutelnosti). Детальні URL мають числовий/хеш id в кінці."""
    name = "reality_sk"
    detail_re = re.compile(
        r"https?://(?:www\.)?reality\.sk/(?:detail/(?P<id>[A-Za-z0-9_\-]{6,})"
        r"|(?:byty|domy|[a-z\-]+)/[a-z0-9\-]+/(?P<id2>[A-Za-z0-9_\-]{6,})/?$)"
    )

    def extract_links(self, html: str, base_url: str):
        soup = BeautifulSoup(html, "lxml")
        seen: dict[str, str] = {}
        for a in soup.find_all("a", href=True):
            url = urljoin(base_url, a["href"]).split("#")[0].split("?")[0]
            m = self.detail_re.search(url)
            if not m:
                continue
            ext = m.group("id") or m.group("id2")
            # відсікаємо сторінки-фільтри типу /byty/bratislava/predaj/
            if ext and not ext.isalpha() and ext not in seen:
                seen[ext] = url
        return list(seen.items())

    def enrich(self, soup, l, page_text):
        Nehnutelnosti.enrich(self, soup, l, page_text)  # однакова платформа


class TopReality(Source):
    """topreality.sk. Деталка: https://www.topreality.sk/<slug>-r12345678.html"""
    name = "topreality"
    detail_re = re.compile(r"https?://(?:www\.)?topreality\.sk/[^\s\"'?#]*?-r(?P<id>\d{5,})\.html")
    title_selectors = ["h1"]

    def enrich(self, soup, l, page_text):
        if "sukromna_osoba" in l.page_markers:
            l.site_private_flag = True
        # TopReality показує блок "Realitná kancelária" з назвою фірми у контактах
        box = soup.find(string=re.compile(r"Realitn[áa] kancel[áa]ria", re.I))
        if box and l.site_private_flag is None:
            l.page_markers.append("rk_box")


# ---------- сайти «від власника» (без ріелторів за правилами порталу) ----------

def _next_data(soup: BeautifulSoup) -> dict:
    s = soup.find("script", id="__NEXT_DATA__")
    if not s or not s.string:
        return {}
    try:
        import json
        return json.loads(s.string)
    except ValueError:
        return {}


def _find_advert(obj, depth: int = 0):
    """Шукає в JSON Next.js словник оголошення: має ціну і площу/диспозицію."""
    if depth > 12:
        return None
    if isinstance(obj, dict):
        keys = set(obj)
        if "price" in keys and keys & {"surface", "disposition", "estateType", "offerType", "address"}:
            return obj
        for v in obj.values():
            r = _find_advert(v, depth + 1)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj[:50]:
            r = _find_advert(v, depth + 1)
            if r:
                return r
    return None


class OwnerPortal(Source):
    """Спільне: портал заявляє, що оголошення лише від власників. Не віримо сліпо (ріелтори
    проникають усюди), але даємо м'який плюс і читаємо дані з JSON Next.js, якщо є."""

    def enrich(self, soup: BeautifulSoup, l: Listing, page_text: str) -> None:
        if l.site_private_flag is None:
            l.site_private_flag = True
        ad = _find_advert(_next_data(soup))
        if ad:
            p = ad.get("price")
            if isinstance(p, (int, float)) and p > 0 and l.price_eur is None:
                l.price_eur, l.price_text = float(p), f"{p:,.0f} €".replace(",", " ")
            surf = ad.get("surface")
            if isinstance(surf, (int, float)) and surf > 0 and " m2" not in l.description:
                l.description += f" Plocha: {surf:g} m2."
            addr = ad.get("address")
            if isinstance(addr, str) and not l.location:
                l.location = clean(addr)[:80]
            elif isinstance(addr, dict) and not l.location:
                l.location = clean(" ".join(str(addr.get(k) or "") for k in ("street", "city", "cityDistrict")))[:80]
        if not l.location:
            m = re.search(r"Bratislava\s?[-–]\s?[A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ][\wáäčďéíľĺňóôŕšťúýž]+(?:\s[A-ZÁČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ][\wáäčďéíľĺňóôŕšťúýž]+)?",
                          l.title + " " + page_text)
            if m:
                l.location = m.group(0)


class Bezrealitky(OwnerPortal):
    """bezrealitky.sk (Next.js). Деталка: /nehnutelnosti-byty-domy/123456-ponuka-prenajom-bytu-…"""
    name = "bezrealitky"
    detail_re = re.compile(
        r"https?://(?:www\.)?bezrealitky\.(?:sk|cz)/(?:nehnutelnosti|nemovitosti)-byty-domy/(?P<id>\d{4,})(?:-[^/?#]*)?")


class Realfree(OwnerPortal):
    """realfree.sk. Деталка: /inzerat/prenajom-3-izboveho-bytu-…-4a3010; список: /prenajom-bytu-bratislava?page=N"""
    name = "realfree"
    detail_re = re.compile(r"https?://(?:www\.)?realfree\.sk/inzerat/(?P<id>[a-z0-9][a-z0-9\-]{3,})")


class Bezmaklerov(OwnerPortal):
    """bezmaklerov.sk. Деталка: /sk/inzerat/cg3K7idGqc6QVpqELegg7r"""
    name = "bezmaklerov"
    detail_re = re.compile(r"https?://(?:www\.)?bezmaklerov\.sk/(?:sk/)?inzerat/(?P<id>[A-Za-z0-9]{8,})")


SOURCES: dict[str, Source] = {s.name: s for s in (Bazos(), Nehnutelnosti(), RealitySk(), TopReality(),
                                                  Bezrealitky(), Realfree(), Bezmaklerov())}
