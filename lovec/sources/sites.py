"""Адаптери конкретних сайтів."""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Listing
from .base import Source, clean, parse_price


class Bazos(Source):
    """reality.bazos.sk — найбільше приватних оголошень.
    Деталка: https://reality.bazos.sk/inzerat/196719480/slug.php"""
    name = "bazos"
    detail_re = re.compile(r"https?://reality\.bazos\.sk/inzerat/(?P<id>\d+)/[^/?#]+\.php")
    description_selectors = ["div.popisdetail", "div.popis"]
    title_selectors = ["h1.nadpisdetail", "h1"]

    def enrich(self, soup: BeautifulSoup, l: Listing, page_text: str) -> None:
        # Таблиця з параметрами: "Meno:", "Lokalita:", "Cena:"
        for td in soup.find_all("td"):
            label = clean(td.get_text()).lower().rstrip(":")
            nxt = td.find_next_sibling("td")
            if not nxt:
                continue
            val = clean(nxt.get_text(" "))
            if label == "meno" and not l.seller_name:
                l.seller_name = val.split(" Všetky")[0][:80]
            elif label == "lokalita" and not l.location:
                l.location = val[:80]
            elif label == "cena" and l.price_eur is None:
                l.price_eur, l.price_text = parse_price(val)
                l.price_text = l.price_text or val[:40]
        # Посилання "усі оголошення користувача" — по ньому порахуємо кількість (див. user_listings_url)
        a = soup.find("a", href=re.compile(r"idmeno|hlokalita=.*&hledat=&.*idmeno|/inzeraty-uzivatela", re.I))
        if not a:
            a = soup.find("a", string=re.compile(r"inzer[aá]ty\s+(pou|u)[zž][ií]vate", re.I))
        if a:
            l.extra["user_url"] = urljoin(l.url, a["href"])

    @staticmethod
    def count_user_listings(html: str) -> int:
        m = re.search(r"Zobrazen\w*\s+\d+\s*-\s*\d+\s+inzer\w+\s+z\s+([\d\s]+)", html)
        if m:
            return int(re.sub(r"\s", "", m.group(1)))
        return len(set(re.findall(r"/inzerat/(\d+)/", html)))


class Nehnutelnosti(Source):
    """nehnutelnosti.sk — Next.js. Деталка: /detail/JuY-l_mO9Et/slug"""
    name = "nehnutelnosti"
    detail_re = re.compile(r"https?://(?:www\.)?nehnutelnosti\.sk/detail/(?P<id>[A-Za-z0-9_\-]{6,})/[^/?#]*")
    title_selectors = ["h1"]

    def enrich(self, soup: BeautifulSoup, l: Listing, page_text: str) -> None:
        # На картці/деталці агентства підписані назвою фірми; "Profi klient" — платний акаунт компанії.
        if "profi_klient" in l.page_markers:
            l.site_private_flag = False
        if "sukromna_osoba" in l.page_markers and l.site_private_flag is None:
            l.site_private_flag = True
        m = re.search(r"okres [^,]{3,40}", page_text)
        if m and not l.location:
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


SOURCES: dict[str, Source] = {s.name: s for s in (Bazos(), Nehnutelnosti(), RealitySk(), TopReality())}
